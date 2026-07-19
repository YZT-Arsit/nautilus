"""In The Zone short — pure decision engine (position-aware, offline-testable).

Holds **only** the signal-decision maths (plain-Python; no ``feature_engine`` /
``strategy_framework`` / ``nautilus_trader`` / ``pandas``). Emits
``BUY``/``SELL``/``HOLD`` with the signal->order meaning left to
``SignalToOrderPolicy`` (``sell_means: short`` — SELL opens the short, BUY covers
it). Single unit, no pyramiding.

Ported from the TradeBlazer ``In_The_Zone_S`` system (bars are labelled 3,2,1,0
left-to-right, 0 = current):

* zone setup (while not short and not yet armed): if ``Close[1] <= Low[3]`` set
  ``UpLine = HighestFC(High, CancelFlagN)[1]`` and ``DownLine = Low[3]``; if the
  current close is inside ``[DownLine, UpLine]`` arm ``EntryFlag`` with the
  trigger ``EntryPriceS = Low[0]``. While armed, a close ``> UpLine`` cancels;
* entry (short): not short, ``CurrentBar >= ATRLength``, ``EntryFlag[1]``, ``Low
  <= EntryPriceS[1]`` and ``Vol > 0`` -> short at ``Min(Open, EntryPriceS[1])``;
  set ``ProtectStopS = High[1] + ProtectStopATRMulti*ATR[1]`` and
  ``ProfitTargetStopS = Low[1] - ProfitTargetATRMulti*ATR[1]``;
* exit (cover), once short for a full bar (``MP[1] == -1``) and ``Vol > 0``:
  ``LowAfterEntry`` tracks the lowest low since entry; the exit line is
  ``BreakEvenStopS`` (== the entry fill price) once the move has run
  ``BreakEvenStopATRMulti*ATR[1]`` in favour, else ``ProtectStopS[1]``. If ``Open
  <= ProfitTargetStopS[1]`` cover at ``Open`` (profit target); else if ``High >=
  ExitLineS`` cover at ``Max(Open, ExitLineS)`` (protective / break-even stop).

Faithful TradeBlazer semantics preserved: all ``[1]`` / ``[3]`` reads use the
previous / three-bars-back values (snapshotted before the current bar mutates the
carried-forward series); ``UpLine`` / ``DownLine`` / ``EntryFlag`` / ``EntryPriceS``
/ the stops / ``LowAfterEntry`` persist as running state; ``MarketPosition`` uses
the bar-start position and the exit is gated by ``MP[1] == -1``, so entry and
cover never fire on one bar. There **is** a ``Vol > 0`` gate. ``AvgTrueRange`` is a
simple mean of true range over ``atr_length`` (the TradeBlazer builtin uses Wilder
smoothing).
"""
from __future__ import annotations

from collections import deque

from feature_engine.indicators import simple_atr, true_range

from strategies.in_the_zone_short.config import InTheZoneShortConfig

BUY, SELL, HOLD = "BUY", "SELL", "HOLD"


class InTheZoneShortEngine:
    """Pure, position-aware In The Zone short engine."""

    def __init__(self, config: InTheZoneShortConfig) -> None:
        self.cfg = config
        self._highs_n: deque[float] = deque(maxlen=config.cancel_flag_n)  # HighestFC(High, CancelFlagN)
        self._recent_lows: deque[float] = deque(maxlen=3)                 # Low[1], Low[2], Low[3]
        self._trs: deque[float] = deque(maxlen=config.atr_length)
        self._tr_prev_close: float | None = None

        self._bar = -1                    # TradeBlazer CurrentBar (0-based)

        # position state
        self.position = 0                 # 0 flat, -1 short (short-only)
        self.bars_since_entry = 0
        self.entry_price: float | None = None
        self.last_entry_price: float | None = None   # LastEntryPrice (== BreakEvenStopS)

        # persistent series (carry forward across bars)
        self.up_line: float | None = None
        self.down_line: float | None = None
        self.entry_flag = False
        self.entry_price_s: float | None = None
        self.protect_stop_s: float | None = None
        self.profit_target_stop_s: float | None = None
        self.low_after_entry: float | None = None

        # previous-bar snapshots (the ``[1]`` values the decisions read)
        self._prev_close: float | None = None
        self._prev_high: float | None = None
        self._prev_atr: float | None = None

    def update(self, open_: float, high: float, low: float, close: float, volume: float):
        cfg = self.cfg
        self._bar += 1
        cb = self._bar

        # UpLineTemp[1] = HighestFC(High, CancelFlagN)[1] — prior N highs (before this bar).
        uplinetemp_1 = max(self._highs_n) if self._highs_n else None
        self._highs_n.append(high)

        # Low[3] / Low[1] from the recent lows (before appending the current low).
        low3 = self._recent_lows[0] if len(self._recent_lows) == 3 else None
        prev_low = self._recent_lows[-1] if self._recent_lows else None

        # ATR (simple mean of true range).
        tr = true_range(high, low, self._tr_prev_close)
        self._trs.append(tr)
        self._tr_prev_close = close
        atr = simple_atr(self._trs, cfg.atr_length)

        # Snapshots of the carried series (the ``[1]`` reads).
        prev_entry_flag = self.entry_flag
        prev_entry_price_s = self.entry_price_s
        prev_protect_stop_s = self.protect_stop_s
        prev_profit_target_stop_s = self.profit_target_stop_s
        prev_low_after_entry = self.low_after_entry

        mp_start = self.position
        signal, reason = HOLD, "hold"
        acted = False
        just_entered = False

        # 1. ZONE SETUP (short): arm / cancel the box while not short.
        if mp_start != -1:
            if not prev_entry_flag:
                if self._prev_close is not None and low3 is not None and self._prev_close <= low3:
                    self.up_line = uplinetemp_1
                    self.down_line = low3
                    if (
                        self.up_line is not None and self.down_line is not None
                        and close <= self.up_line and close >= self.down_line
                    ):
                        self.entry_flag = True
                        self.entry_price_s = low       # EntryPriceS = Low[0]
            else:  # armed on the previous bar
                if self.up_line is not None and close > self.up_line:
                    self.entry_flag = False            # cancel: close above the upper rail

        # 2. ENTRY (open short): break below the prior trigger.
        if (
            not acted and mp_start != -1 and cb >= cfg.atr_length
            and prev_entry_flag and prev_entry_price_s is not None
            and low <= prev_entry_price_s and volume > 0
        ):
            entry_price = min(open_, prev_entry_price_s)
            self.position = -1
            self.bars_since_entry = 0
            self.entry_price = entry_price
            self.last_entry_price = entry_price
            self.entry_flag = False
            if self._prev_high is not None and self._prev_atr is not None:
                self.protect_stop_s = self._prev_high + cfg.protect_stop_atr_multi * self._prev_atr
            if prev_low is not None and self._prev_atr is not None:
                self.profit_target_stop_s = prev_low - cfg.profit_target_atr_multi * self._prev_atr
            signal, reason, acted = SELL, "enter_short", True
            just_entered = True

        # 3. LowAfterEntry (lowest low since entry).
        if just_entered:
            self.low_after_entry = low
        elif mp_start == -1:
            base = prev_low_after_entry if prev_low_after_entry is not None else low
            self.low_after_entry = min(base, low)

        break_even = self.last_entry_price   # BreakEvenStopS = LastEntryPrice

        # 4. EXIT (cover): profit target, else protective / break-even stop.
        if not acted and mp_start == -1 and self.bars_since_entry > 0 and volume > 0:
            if prev_profit_target_stop_s is not None and open_ <= prev_profit_target_stop_s:
                self.position = 0
                self.bars_since_entry = 0
                self.entry_price = None
                signal, reason, acted = BUY, "exit_profit_target", True
            else:
                exit_line = None
                exit_reason = None
                if (
                    break_even is not None and prev_low_after_entry is not None
                    and self._prev_atr is not None
                    and prev_low_after_entry <= break_even - cfg.break_even_stop_atr_multi * self._prev_atr
                ):
                    exit_line, exit_reason = break_even, "exit_breakeven_stop"
                elif prev_protect_stop_s is not None:
                    exit_line, exit_reason = prev_protect_stop_s, "exit_protect_stop"
                if exit_line is not None and high >= exit_line:
                    self.position = 0
                    self.bars_since_entry = 0
                    self.entry_price = None
                    signal, reason, acted = BUY, exit_reason, True

        # 5. Roll snapshots / history, then advance counters.
        self._prev_close = close
        self._prev_high = high
        self._prev_atr = atr
        self._recent_lows.append(low)
        if self.position == -1:
            self.bars_since_entry += 1

        return signal, reason
