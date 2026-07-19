"""MA Support/Resistance short — pure decision engine (position-aware, offline).

Holds **only** the signal-decision maths (plain-Python; no ``feature_engine`` /
``strategy_framework`` / ``nautilus_trader`` / ``pandas``). Emits
``BUY``/``SELL``/``HOLD`` with the signal->order meaning left to
``SignalToOrderPolicy`` (``sell_means: short`` — SELL opens the short, BUY covers
it). Single unit, no pyramiding.

Ported from the TradeBlazer ``Moving_Average_Sup_and_Res_S`` system:

* ``MA = AverageFC(Close, MALength)`` (simple mean); ``ATR =
  AvgTrueRange(ATRLength)``;
* a **death-cross** (``CrossUnder(Close, MA)``) arms the support line:
  ``SupportFlag = True``, ``SupportLine = Low``; while ``Close < MA`` the support
  line tracks every lower ``Low``;
* a **golden-cross** (``CrossOver(Close, MA)``) — if the support was armed —
  records ``EntryPriceS = SupportLine[1]`` (the short entry line) and disarms;
  while ``Close > MA`` the (vestigial, for the short side) resistance line tracks
  every higher ``High``;
* entry (short): ``MarketPosition != -1``, ``EntryPriceS[1] != 0`` and
  ``EntryPriceS[2] != 0``, ``Close[2] > EntryPriceS[2]`` and ``Close[1] <=
  EntryPriceS[1]`` (close crossed back below the entry line on the prior bar) and
  ``Vol > 0`` -> short at ``Open``; set ``ProtectStopS = High[1] +
  ProtectStopATRMulti * ATR[1]``;
* exit (cover), once short for a full bar (``MP[1] == -1``): ``LowAfterEntry``
  tracks the lowest low since entry and ``TrailStopS = LowAfterEntry[1] +
  TrailStopATRMulti * ATR[1]``. If ``High >= ProtectStopS[1]`` and ``ProtectStopS[1]
  <= TrailStopS`` cover at ``Max(Open, ProtectStopS[1])``; else if ``High >=
  TrailStopS`` cover at ``Max(Open, TrailStopS)``.

Faithful TradeBlazer semantics preserved: all ``[1]`` / ``[2]`` reads use the
previous / two-bars-back values (snapshotted before the current bar mutates the
carried-forward series); ``SupportLine`` / ``ResistanceLine`` / ``EntryPriceS`` /
the flags / ``ProtectStopS`` / ``LowAfterEntry`` persist as running state; the
entry tests ``MarketPosition != -1`` (bar-start position) and the exit is gated
by ``MP[1] == -1`` (short for a full bar), so entry and cover never fire on one
bar. ``AverageFC`` / ``AvgTrueRange`` are simple means over their windows (matches
the other ports; the TradeBlazer ATR builtin uses Wilder smoothing).
"""
from __future__ import annotations

from collections import deque

from feature_engine.indicators import simple_atr, sma, true_range
from strategies.ma_sup_res_short.config import MaSupResShortConfig

BUY, SELL, HOLD = "BUY", "SELL", "HOLD"

_SUPPORT_INIT = -9_999_999.0
_RESISTANCE_INIT = 9_999_999.0


class MaSupResShortEngine:
    """Pure, position-aware MA Support/Resistance short engine."""

    def __init__(self, config: MaSupResShortConfig) -> None:
        self.cfg = config
        self._closes: deque[float] = deque(maxlen=config.ma_length)
        self._trs: deque[float] = deque(maxlen=config.atr_length)
        self._tr_prev_close: float | None = None

        self.current_bar = 0

        # position state
        self.position = 0                 # 0 flat, -1 short (short-only)
        self.bars_since_entry = 0
        self.entry_price: float | None = None

        # persistent series (carry forward across bars unless reassigned)
        self.support_line = _SUPPORT_INIT
        self.resistance_line = _RESISTANCE_INIT
        self.support_flag = False
        self.resistance_flag = False
        self.entry_price_s = 0.0
        self.protect_stop_s = 0.0
        self.low_after_entry: float | None = None

        # previous-bar snapshots (the ``[1]`` / ``[2]`` values the decisions read)
        self._prev_close: float | None = None
        self._prev2_close: float | None = None
        self._prev_ma: float | None = None
        self._prev_atr: float | None = None
        self._prev_high: float | None = None
        self._prev_entry_price_s = 0.0
        self._prev2_entry_price_s = 0.0
        self._prev_protect_stop_s = 0.0
        self._prev_low_after_entry: float | None = None

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
        ma = sma(self._closes) if len(self._closes) == cfg.ma_length else None
        tr = true_range(high, low, self._tr_prev_close)
        self._trs.append(tr)
        self._tr_prev_close = close
        atr = simple_atr(self._trs, cfg.atr_length)

        # 2. Close / MA crosses (need the prior close & MA).
        cross_under = cross_over = False
        if self._prev_close is not None and self._prev_ma is not None and ma is not None:
            cross_under = self._prev_close >= self._prev_ma and close < ma   # death cross
            cross_over = self._prev_close <= self._prev_ma and close > ma    # golden cross

        # 3. Death cross -> arm/init the support line.
        if cross_under:
            if prev_resistance_flag:
                self.resistance_flag = False
            self.support_flag = True
            self.support_line = low

        # 4. Golden cross -> record the short entry line, arm the resistance line.
        if cross_over:
            if prev_support_flag:
                self.entry_price_s = prev_support_line   # EntryPriceS = SupportLine[1]
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

        # 6. ENTRY (open short): prior bar's close crossed back below the entry line.
        if (
            not acted and mp_start != -1
            and self._prev_entry_price_s != 0 and self._prev2_entry_price_s != 0
            and self._prev2_close is not None and self._prev_close is not None
            and self._prev2_close > self._prev2_entry_price_s
            and self._prev_close <= self._prev_entry_price_s
            and volume > 0
        ):
            self.position = -1
            self.bars_since_entry = 0
            self.entry_price = open_
            if self._prev_high is not None and self._prev_atr is not None:
                self.protect_stop_s = self._prev_high + cfg.protect_stop_atr_multi * self._prev_atr
            signal, reason, acted = SELL, "enter_short", True
            just_entered = True

        # 7. LowAfterEntry (lowest low since entry) + trailing stop level.
        if just_entered:
            self.low_after_entry = low
        elif mp_start == -1:
            prev_lae = self._prev_low_after_entry if self._prev_low_after_entry is not None else low
            self.low_after_entry = min(prev_lae, low)

        trail_stop = None
        if self._prev_low_after_entry is not None and self._prev_atr is not None:
            trail_stop = self._prev_low_after_entry + cfg.trail_stop_atr_multi * self._prev_atr

        # 8. EXIT (cover): protective stop, else trailing stop.
        if (
            not acted and mp_start == -1 and self.bars_since_entry > 0 and volume > 0
            and trail_stop is not None
        ):
            ps1 = self._prev_protect_stop_s
            if high >= ps1 and ps1 <= trail_stop:
                self.position = 0
                self.bars_since_entry = 0
                self.entry_price = None
                signal, reason, acted = BUY, "exit_protect_stop", True
            elif high >= trail_stop:
                self.position = 0
                self.bars_since_entry = 0
                self.entry_price = None
                signal, reason, acted = BUY, "exit_trail_stop", True

        # 9. Roll the prev-bar snapshots, then advance counters.
        self._prev2_close = self._prev_close
        self._prev_close = close
        self._prev_ma = ma
        self._prev_atr = atr
        self._prev_high = high
        self._prev2_entry_price_s = self._prev_entry_price_s
        self._prev_entry_price_s = self.entry_price_s
        self._prev_protect_stop_s = self.protect_stop_s
        self._prev_low_after_entry = self.low_after_entry
        if self.position == -1:
            self.bars_since_entry += 1

        return signal, reason
