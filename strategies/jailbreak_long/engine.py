"""JailBreak long — pure decision engine (position-aware, offline-testable).

Holds **only** the signal-decision maths (plain-Python; no ``feature_engine`` /
``strategy_framework`` / ``nautilus_trader`` / ``pandas``). Emits
``BUY``/``SELL``/``HOLD`` with the signal->order meaning left to
``SignalToOrderPolicy`` (``sell_means: flat`` — BUY opens the long, SELL flattens
it). Single unit, no pyramiding.

Ported from the TradeBlazer ``JailBreakSys_L`` system — the long mirror of
``jailbreak_short``:

* ``L1 = Max(Length1, Length2)``; ``L2 = Min(Length1, Length2)``;
* ``Upperband = Highest(High, L1)`` (entry channel); ``Exitlong = Lowest(Low,
  L2)`` (exit channel); ``ATR = AvgTrueRange(AtrVal)``;
* entry (long): flat, ``High >= Upperband[1] + tick`` and ``Vol > 0`` -> long at
  ``Max(Open, Upperband[1] + tick)``; set ``ProtectStopL = EntryPrice - IPS *
  ATR[1]``;
* exit (flatten), once ``BarsSinceEntry > 0`` and ``Vol > 0``: if ``Low <=
  ProtectStopL[1]`` and ``ProtectStopL[1] >= Exitlong[1]`` sell at ``Min(Open,
  ProtectStopL[1])``; else if ``Low <= Exitlong[1] - tick`` sell at ``Min(Open,
  Exitlong[1] - tick)``.

Faithful TradeBlazer semantics preserved: the entry reads ``Upperband[1]`` and the
exit reads ``ProtectStopL[1]`` / ``Exitlong[1]`` (previous-bar values,
snapshotted before the roll); ``ProtectStopL`` persists as running state (set on
entry from the entry fill price - ``IPS * ATR[1]``); ``MarketPosition`` uses the
bar-start position and the exit is gated by ``BarsSinceEntry > 0``, so entry and
sell never fire on one bar. ``Highest`` / ``Lowest`` are taken over the available
window. ``AvgTrueRange`` is a simple mean of true range over ``atr_val`` (matches
the other ports; the TradeBlazer builtin uses Wilder smoothing).
"""
from __future__ import annotations

from collections import deque

from feature_engine.indicators import simple_atr, true_range

from strategies.jailbreak_long.config import JailBreakLongConfig

BUY, SELL, HOLD = "BUY", "SELL", "HOLD"


class JailBreakLongEngine:
    """Pure, position-aware JailBreak long engine."""

    def __init__(self, config: JailBreakLongConfig) -> None:
        self.cfg = config
        l1 = max(config.length1, config.length2)   # entry (long-period) channel
        l2 = min(config.length1, config.length2)   # exit (short-period) channel
        self._entry_highs: deque[float] = deque(maxlen=l1)
        self._exit_lows: deque[float] = deque(maxlen=l2)
        self._trs: deque[float] = deque(maxlen=config.atr_val)
        self._tr_prev_close: float | None = None

        self.current_bar = 0

        # position state
        self.position = 0                 # 0 flat, 1 long (long-only)
        self.bars_since_entry = 0
        self.entry_price: float | None = None

        # persistent state
        self.protect_stop_l: float | None = None

        # previous-bar snapshots (the ``[1]`` values the decisions read)
        self._prev_upperband: float | None = None
        self._prev_exitlong: float | None = None
        self._prev_atr: float | None = None
        self._prev_protect_stop_l: float | None = None

    def update(self, open_: float, high: float, low: float, close: float, volume: float):
        cfg = self.cfg
        self.current_bar += 1

        # 1. Channels (over the available window) and ATR (simple mean of TR).
        self._entry_highs.append(high)
        self._exit_lows.append(low)
        upperband = max(self._entry_highs)
        exitlong = min(self._exit_lows)

        tr = true_range(high, low, self._tr_prev_close)
        self._trs.append(tr)
        self._tr_prev_close = close
        atr = simple_atr(self._trs, cfg.atr_val)

        mp_start = self.position
        signal, reason = HOLD, "hold"
        acted = False

        # 2. ENTRY (open long): break above the prior long-period high channel.
        if (
            not acted and mp_start == 0
            and self._prev_upperband is not None
            and high >= self._prev_upperband + cfg.tick and volume > 0
        ):
            entry_price = max(open_, self._prev_upperband + cfg.tick)
            self.position = 1
            self.bars_since_entry = 0
            self.entry_price = entry_price
            self.protect_stop_l = (
                entry_price - cfg.ips * self._prev_atr if self._prev_atr is not None else None
            )
            signal, reason, acted = BUY, "enter_long", True

        # 3. EXIT (flatten): ATR protective stop, else short-period low channel.
        if not acted and mp_start == 1 and self.bars_since_entry > 0 and volume > 0:
            ps1 = self._prev_protect_stop_l
            el1 = self._prev_exitlong
            if el1 is not None:
                if ps1 is not None and low <= ps1 and ps1 >= el1:
                    self.position = 0
                    self.bars_since_entry = 0
                    self.entry_price = None
                    signal, reason, acted = SELL, "exit_protect_stop", True
                elif low <= el1 - cfg.tick:
                    self.position = 0
                    self.bars_since_entry = 0
                    self.entry_price = None
                    signal, reason, acted = SELL, "exit_channel", True

        # 4. Roll the prev-bar snapshots, then advance counters.
        self._prev_upperband = upperband
        self._prev_exitlong = exitlong
        self._prev_atr = atr
        self._prev_protect_stop_l = self.protect_stop_l
        if self.position == 1:
            self.bars_since_entry += 1

        return signal, reason
