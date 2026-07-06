"""MA Support/Resistance long — pure decision engine (position-aware, offline).

Holds **only** the signal-decision maths (plain-Python; no ``feature_engine`` /
``strategy_framework`` / ``nautilus_trader`` / ``pandas``). Emits
``BUY``/``SELL``/``HOLD`` with the signal->order meaning left to
``SignalToOrderPolicy`` (``sell_means: flat`` — BUY opens the long, SELL flattens
it). Single unit, no pyramiding.

Ported from the TradeBlazer ``Moving_Average_Sup_and_Res_L`` system — the long
mirror of ``ma_sup_res_short``:

* ``MA = AverageFC(Close, MALength)`` (simple mean); ``ATR =
  AvgTrueRange(ATRLength)``;
* a **golden-cross** (``CrossOver(Close, MA)``) arms the resistance line:
  ``ResistanceFlag = True``, ``ResistanceLine = High``; while ``Close > MA`` the
  resistance line tracks every higher ``High``;
* a **death-cross** (``CrossUnder(Close, MA)``) — if the resistance was armed —
  records ``EntryPriceL = ResistanceLine[1]`` (the long entry line) and disarms;
  while ``Close < MA`` the (vestigial, for the long side) support line tracks every
  lower ``Low``;
* entry (long): ``MarketPosition != 1``, ``EntryPriceL[1] != 0`` and
  ``EntryPriceL[2] != 0``, ``Close[2] < EntryPriceL[2]`` and ``Close[1] >=
  EntryPriceL[1]`` (close crossed back above the entry line on the prior bar) and
  ``Vol > 0`` -> long at ``Open``; set ``ProtectStopL = Low[1] -
  ProtectStopATRMulti * ATR[1]``;
* exit (flatten), once long for a full bar (``MP[1] == 1``): ``HighAfterEntry``
  tracks the highest high since entry and ``TrailStopL = HighAfterEntry[1] -
  TrailStopATRMulti * ATR[1]``. If ``Low <= ProtectStopL[1]`` and ``ProtectStopL[1]
  >= TrailStopL`` sell at ``Min(Open, ProtectStopL[1])``; else if ``Low <=
  TrailStopL`` sell at ``Min(Open, TrailStopL)``.

Faithful TradeBlazer semantics preserved: all ``[1]`` / ``[2]`` reads use the
previous / two-bars-back values (snapshotted before the current bar mutates the
carried-forward series); ``SupportLine`` / ``ResistanceLine`` / ``EntryPriceL`` /
the flags / ``ProtectStopL`` / ``HighAfterEntry`` persist as running state; the
entry tests ``MarketPosition != 1`` (bar-start position) and the exit is gated by
``MP[1] == 1`` (long for a full bar), so entry and sell never fire on one bar.
``AverageFC`` / ``AvgTrueRange`` are simple means over their windows (matches the
other ports; the TradeBlazer ATR builtin uses Wilder smoothing).
"""
from __future__ import annotations

from collections import deque

from feature_engine.indicators import true_range
from strategies.ma_sup_res_long.config import MaSupResLongConfig

BUY, SELL, HOLD = "BUY", "SELL", "HOLD"

_SUPPORT_INIT = -9_999_999.0
_RESISTANCE_INIT = 9_999_999.0


class MaSupResLongEngine:
    """Pure, position-aware MA Support/Resistance long engine."""

    def __init__(self, config: MaSupResLongConfig) -> None:
        self.cfg = config
        self._closes: deque[float] = deque(maxlen=config.ma_length)
        self._trs: deque[float] = deque(maxlen=config.atr_length)
        self._tr_prev_close: float | None = None

        self.current_bar = 0

        # position state
        self.position = 0                 # 0 flat, 1 long (long-only)
        self.bars_since_entry = 0
        self.entry_price: float | None = None

        # persistent series (carry forward across bars unless reassigned)
        self.support_line = _SUPPORT_INIT
        self.resistance_line = _RESISTANCE_INIT
        self.support_flag = False
        self.resistance_flag = False
        self.entry_price_l = 0.0
        self.protect_stop_l = 0.0
        self.high_after_entry: float | None = None

        # previous-bar snapshots (the ``[1]`` / ``[2]`` values the decisions read)
        self._prev_close: float | None = None
        self._prev2_close: float | None = None
        self._prev_ma: float | None = None
        self._prev_atr: float | None = None
        self._prev_low: float | None = None
        self._prev_entry_price_l = 0.0
        self._prev2_entry_price_l = 0.0
        self._prev_protect_stop_l = 0.0
        self._prev_high_after_entry: float | None = None

    def update(self, open_: float, high: float, low: float, close: float, volume: float):
        cfg = self.cfg
        self.current_bar += 1

        # Snapshot the carried-forward series BEFORE this bar mutates them (the
        # ``[1]`` reads).
        prev_support_flag = self.support_flag
        prev_resistance_flag = self.resistance_flag
        prev_support_line = self.support_line
        prev_resistance_line = self.resistance_line

        # 1. MA (AverageFC) and ATR (simple mean of true range).
        self._closes.append(close)
        ma = sum(self._closes) / len(self._closes) if len(self._closes) == cfg.ma_length else None
        tr = true_range(high, low, self._tr_prev_close)
        self._trs.append(tr)
        self._tr_prev_close = close
        atr = sum(self._trs) / len(self._trs) if len(self._trs) == cfg.atr_length else None

        # 2. Close / MA crosses (need the prior close & MA).
        cross_under = cross_over = False
        if self._prev_close is not None and self._prev_ma is not None and ma is not None:
            cross_under = self._prev_close >= self._prev_ma and close < ma   # death cross
            cross_over = self._prev_close <= self._prev_ma and close > ma    # golden cross

        # 3. Death cross -> record the long entry line, arm/init the support line.
        if cross_under:
            if prev_resistance_flag:
                self.entry_price_l = prev_resistance_line   # EntryPriceL = ResistanceLine[1]
                self.resistance_flag = False
            self.support_flag = True
            self.support_line = low

        # 4. Golden cross -> arm the resistance line.
        if cross_over:
            if prev_support_flag:
                self.support_flag = False
            self.resistance_flag = True
            self.resistance_line = high

        # 5. Track the lines while price sits on one side of the MA (uses [1]).
        if ma is not None:
            if close > ma:
                if high > prev_resistance_line:
                    self.resistance_line = high
            elif close < ma:
                if low < prev_support_line:
                    self.support_line = low

        mp_start = self.position
        signal, reason = HOLD, "hold"
        acted = False
        just_entered = False

        # 6. ENTRY (open long): prior bar's close crossed back above the entry line.
        if (
            not acted and mp_start != 1
            and self._prev_entry_price_l != 0 and self._prev2_entry_price_l != 0
            and self._prev2_close is not None and self._prev_close is not None
            and self._prev2_close < self._prev2_entry_price_l
            and self._prev_close >= self._prev_entry_price_l
            and volume > 0
        ):
            self.position = 1
            self.bars_since_entry = 0
            self.entry_price = open_
            if self._prev_low is not None and self._prev_atr is not None:
                self.protect_stop_l = self._prev_low - cfg.protect_stop_atr_multi * self._prev_atr
            signal, reason, acted = BUY, "enter_long", True
            just_entered = True

        # 7. HighAfterEntry (highest high since entry) + trailing stop level.
        if just_entered:
            self.high_after_entry = high
        elif mp_start == 1:
            prev_hae = self._prev_high_after_entry if self._prev_high_after_entry is not None else high
            self.high_after_entry = max(prev_hae, high)

        trail_stop = None
        if self._prev_high_after_entry is not None and self._prev_atr is not None:
            trail_stop = self._prev_high_after_entry - cfg.trail_stop_atr_multi * self._prev_atr

        # 8. EXIT (flatten): protective stop, else trailing stop.
        if (
            not acted and mp_start == 1 and self.bars_since_entry > 0 and volume > 0
            and trail_stop is not None
        ):
            ps1 = self._prev_protect_stop_l
            if low <= ps1 and ps1 >= trail_stop:
                self.position = 0
                self.bars_since_entry = 0
                self.entry_price = None
                signal, reason, acted = SELL, "exit_protect_stop", True
            elif low <= trail_stop:
                self.position = 0
                self.bars_since_entry = 0
                self.entry_price = None
                signal, reason, acted = SELL, "exit_trail_stop", True

        # 9. Roll the prev-bar snapshots, then advance counters.
        self._prev2_close = self._prev_close
        self._prev_close = close
        self._prev_ma = ma
        self._prev_atr = atr
        self._prev_low = low
        self._prev2_entry_price_l = self._prev_entry_price_l
        self._prev_entry_price_l = self.entry_price_l
        self._prev_protect_stop_l = self.protect_stop_l
        self._prev_high_after_entry = self.high_after_entry
        if self.position == 1:
            self.bars_since_entry += 1

        return signal, reason
