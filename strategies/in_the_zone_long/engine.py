"""In The Zone long — pure decision engine (position-aware, offline-testable).

Holds **only** the signal-decision maths (plain-Python; no ``feature_engine`` /
``strategy_framework`` / ``nautilus_trader`` / ``pandas``). Emits
``BUY``/``SELL``/``HOLD`` with the signal->order meaning left to
``SignalToOrderPolicy`` (``sell_means: flat`` — BUY opens the long, SELL flattens
it). Single unit, no pyramiding.

Ported from the TradeBlazer ``In_The_Zone_L`` system — the long mirror of
``in_the_zone_short`` (bars are labelled 3,2,1,0 left-to-right, 0 = current):

* zone setup (while not long and not yet armed): if ``Close[1] >= High[3]`` set
  ``UpLine = High[3]`` and ``DownLine = LowestFC(Low, CancelFlagN)[1]``; if the
  current close is inside ``[DownLine, UpLine]`` arm ``EntryFlag`` with the
  trigger ``EntryPriceL = High[0]``. While armed, a close ``< DownLine`` cancels;
* entry (long): not long, ``CurrentBar >= ATRLength``, ``EntryFlag[1]``, ``High >=
  EntryPriceL[1]`` and ``Vol > 0`` -> long at ``Max(Open, EntryPriceL[1])``; set
  ``ProtectStopL = Low[1] - ProtectStopATRMulti*ATR[1]`` and ``ProfitTargetStopL =
  High[1] + ProfitTargetATRMulti*ATR[1]``;
* exit (flatten), once long for a full bar (``MP[1] == 1``) and ``Vol > 0``:
  ``HighAfterEntry`` tracks the highest high since entry; the exit line is
  ``BreakEvenStopL`` (== the entry fill price) once the move has run
  ``BreakEvenStopATRMulti*ATR[1]`` in favour, else ``ProtectStopL[1]``. If ``Open
  >= ProfitTargetStopL[1]`` sell at ``Open`` (profit target); else if ``Low <=
  ExitLineL`` sell at ``Min(Open, ExitLineL)`` (protective / break-even stop).

Faithful TradeBlazer semantics preserved: all ``[1]`` / ``[3]`` reads use the
previous / three-bars-back values (snapshotted before the current bar mutates the
carried-forward series); ``UpLine`` / ``DownLine`` / ``EntryFlag`` / ``EntryPriceL``
/ the stops / ``HighAfterEntry`` persist as running state; ``MarketPosition`` uses
the bar-start position and the exit is gated by ``MP[1] == 1``, so entry and sell
never fire on one bar. There **is** a ``Vol > 0`` gate. ``AvgTrueRange`` is a simple
mean of true range over ``atr_length`` (the TradeBlazer builtin uses Wilder
smoothing).
"""
from __future__ import annotations

from collections import deque

from feature_engine.indicators import true_range

from strategies.in_the_zone_long.config import InTheZoneLongConfig

BUY, SELL, HOLD = "BUY", "SELL", "HOLD"


class InTheZoneLongEngine:
    """Pure, position-aware In The Zone long engine."""

    def __init__(self, config: InTheZoneLongConfig) -> None:
        self.cfg = config
        self._lows_n: deque[float] = deque(maxlen=config.cancel_flag_n)   # LowestFC(Low, CancelFlagN)
        self._recent_highs: deque[float] = deque(maxlen=3)                # High[1], High[2], High[3]
        self._trs: deque[float] = deque(maxlen=config.atr_length)
        self._tr_prev_close: float | None = None

        self._bar = -1                    # TradeBlazer CurrentBar (0-based)

        # position state
        self.position = 0                 # 0 flat, 1 long (long-only)
        self.bars_since_entry = 0
        self.entry_price: float | None = None
        self.last_entry_price: float | None = None   # LastEntryPrice (== BreakEvenStopL)

        # persistent series (carry forward across bars)
        self.up_line: float | None = None
        self.down_line: float | None = None
        self.entry_flag = False
        self.entry_price_l: float | None = None
        self.protect_stop_l: float | None = None
        self.profit_target_stop_l: float | None = None
        self.high_after_entry: float | None = None

        # previous-bar snapshots (the ``[1]`` values the decisions read)
        self._prev_close: float | None = None
        self._prev_low: float | None = None
        self._prev_atr: float | None = None

    def update(self, open_: float, high: float, low: float, close: float, volume: float):
        cfg = self.cfg
        self._bar += 1
        cb = self._bar

        # DownLineTemp[1] = LowestFC(Low, CancelFlagN)[1] — prior N lows (before this bar).
        downlinetemp_1 = min(self._lows_n) if self._lows_n else None
        self._lows_n.append(low)

        # High[3] / High[1] from the recent highs (before appending the current high).
        high3 = self._recent_highs[0] if len(self._recent_highs) == 3 else None
        prev_high = self._recent_highs[-1] if self._recent_highs else None

        # ATR (simple mean of true range).
        tr = true_range(high, low, self._tr_prev_close)
        self._trs.append(tr)
        self._tr_prev_close = close
        atr = sum(self._trs) / len(self._trs) if len(self._trs) == cfg.atr_length else None

        # Snapshots of the carried series (the ``[1]`` reads).
        prev_entry_flag = self.entry_flag
        prev_entry_price_l = self.entry_price_l
        prev_protect_stop_l = self.protect_stop_l
        prev_profit_target_stop_l = self.profit_target_stop_l
        prev_high_after_entry = self.high_after_entry

        mp_start = self.position
        signal, reason = HOLD, "hold"
        acted = False
        just_entered = False

        # 1. ZONE SETUP (long): arm / cancel the box while not long.
        if mp_start != 1:
            if not prev_entry_flag:
                if self._prev_close is not None and high3 is not None and self._prev_close >= high3:
                    self.up_line = high3
                    self.down_line = downlinetemp_1
                    if (
                        self.up_line is not None and self.down_line is not None
                        and close <= self.up_line and close >= self.down_line
                    ):
                        self.entry_flag = True
                        self.entry_price_l = high      # EntryPriceL = High[0]
            else:  # armed on the previous bar
                if self.down_line is not None and close < self.down_line:
                    self.entry_flag = False            # cancel: close below the lower rail

        # 2. ENTRY (open long): break above the prior trigger.
        if (
            not acted and mp_start != 1 and cb >= cfg.atr_length
            and prev_entry_flag and prev_entry_price_l is not None
            and high >= prev_entry_price_l and volume > 0
        ):
            entry_price = max(open_, prev_entry_price_l)
            self.position = 1
            self.bars_since_entry = 0
            self.entry_price = entry_price
            self.last_entry_price = entry_price
            self.entry_flag = False
            if self._prev_low is not None and self._prev_atr is not None:
                self.protect_stop_l = self._prev_low - cfg.protect_stop_atr_multi * self._prev_atr
            if prev_high is not None and self._prev_atr is not None:
                self.profit_target_stop_l = prev_high + cfg.profit_target_atr_multi * self._prev_atr
            signal, reason, acted = BUY, "enter_long", True
            just_entered = True

        # 3. HighAfterEntry (highest high since entry).
        if just_entered:
            self.high_after_entry = high
        elif mp_start == 1:
            base = prev_high_after_entry if prev_high_after_entry is not None else high
            self.high_after_entry = max(base, high)

        break_even = self.last_entry_price   # BreakEvenStopL = LastEntryPrice

        # 4. EXIT (flatten): profit target, else protective / break-even stop.
        if not acted and mp_start == 1 and self.bars_since_entry > 0 and volume > 0:
            if prev_profit_target_stop_l is not None and open_ >= prev_profit_target_stop_l:
                self.position = 0
                self.bars_since_entry = 0
                self.entry_price = None
                signal, reason, acted = SELL, "exit_profit_target", True
            else:
                exit_line = None
                exit_reason = None
                if (
                    break_even is not None and prev_high_after_entry is not None
                    and self._prev_atr is not None
                    and prev_high_after_entry >= break_even + cfg.break_even_stop_atr_multi * self._prev_atr
                ):
                    exit_line, exit_reason = break_even, "exit_breakeven_stop"
                elif prev_protect_stop_l is not None:
                    exit_line, exit_reason = prev_protect_stop_l, "exit_protect_stop"
                if exit_line is not None and low <= exit_line:
                    self.position = 0
                    self.bars_since_entry = 0
                    self.entry_price = None
                    signal, reason, acted = SELL, exit_reason, True

        # 5. Roll snapshots / history, then advance counters.
        self._prev_close = close
        self._prev_low = low
        self._prev_atr = atr
        self._recent_highs.append(high)
        if self.position == 1:
            self.bars_since_entry += 1

        return signal, reason
