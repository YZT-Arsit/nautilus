"""JailBreak short — pure decision engine (position-aware, offline-testable).

Holds **only** the signal-decision maths (plain-Python; no ``feature_engine`` /
``strategy_framework`` / ``nautilus_trader`` / ``pandas``). Emits
``BUY``/``SELL``/``HOLD`` with the signal->order meaning left to
``SignalToOrderPolicy`` (``sell_means: short`` — SELL opens the short, BUY covers
it). Single unit, no pyramiding.

Ported from the TradeBlazer ``JailBreakSys_S`` system:

* ``L1 = Max(Length1, Length2)``; ``L2 = Min(Length1, Length2)``;
* ``Lowerband = Lowest(Low, L1)`` (entry channel); ``Exitshort = Highest(High,
  L2)`` (exit channel); ``ATR = AvgTrueRange(AtrVal)``;
* entry (short): flat, ``Low <= Lowerband[1] - tick`` and ``Vol > 0`` -> short at
  ``Min(Open, Lowerband[1] - tick)``; set ``ProtectStopS = EntryPrice + IPS *
  ATR[1]``;
* exit (cover), once ``BarsSinceEntry > 0`` and ``Vol > 0``: if ``High >=
  ProtectStopS[1]`` and ``ProtectStopS[1] <= Exitshort[1]`` cover at ``Max(Open,
  ProtectStopS[1])``; else if ``High >= Exitshort[1] + tick`` cover at ``Max(Open,
  Exitshort[1] + tick)``.

Faithful TradeBlazer semantics preserved: the entry reads ``Lowerband[1]`` and the
exit reads ``ProtectStopS[1]`` / ``Exitshort[1]`` (previous-bar values,
snapshotted before the roll); ``ProtectStopS`` persists as running state (set on
entry from the entry fill price + ``IPS * ATR[1]``); ``MarketPosition`` uses the
bar-start position and the exit is gated by ``BarsSinceEntry > 0``, so entry and
cover never fire on one bar. ``Highest`` / ``Lowest`` are taken over the available
window. ``AvgTrueRange`` is a simple mean of true range over ``atr_val`` (matches
the other ports; the TradeBlazer builtin uses Wilder smoothing).
"""
from __future__ import annotations

from collections import deque

from feature_engine.indicators import simple_atr, true_range

from strategies.jailbreak_short.config import JailBreakShortConfig

BUY, SELL, HOLD = "BUY", "SELL", "HOLD"


class JailBreakShortEngine:
    """Pure, position-aware JailBreak short engine."""

    def __init__(self, config: JailBreakShortConfig) -> None:
        self.cfg = config
        l1 = max(config.length1, config.length2)   # entry (long-period) channel
        l2 = min(config.length1, config.length2)   # exit (short-period) channel
        self._entry_lows: deque[float] = deque(maxlen=l1)
        self._exit_highs: deque[float] = deque(maxlen=l2)
        self._trs: deque[float] = deque(maxlen=config.atr_val)
        self._tr_prev_close: float | None = None

        self.current_bar = 0

        # position state
        self.position = 0                 # 0 flat, -1 short (short-only)
        self.bars_since_entry = 0
        self.entry_price: float | None = None

        # persistent state
        self.protect_stop_s: float | None = None

        # previous-bar snapshots (the ``[1]`` values the decisions read)
        self._prev_lowerband: float | None = None
        self._prev_exitshort: float | None = None
        self._prev_atr: float | None = None
        self._prev_protect_stop_s: float | None = None

    def update(self, open_: float, high: float, low: float, close: float, volume: float):
        cfg = self.cfg
        self.current_bar += 1

        # 1. Channels (over the available window) and ATR (simple mean of TR).
        self._entry_lows.append(low)
        self._exit_highs.append(high)
        lowerband = min(self._entry_lows)
        exitshort = max(self._exit_highs)

        tr = true_range(high, low, self._tr_prev_close)
        self._trs.append(tr)
        self._tr_prev_close = close
        atr = simple_atr(self._trs, cfg.atr_val)

        mp_start = self.position
        signal, reason = HOLD, "hold"
        acted = False

        # 2. ENTRY (open short): break below the prior long-period low channel.
        if (
            not acted and mp_start == 0
            and self._prev_lowerband is not None
            and low <= self._prev_lowerband - cfg.tick and volume > 0
        ):
            entry_price = min(open_, self._prev_lowerband - cfg.tick)
            self.position = -1
            self.bars_since_entry = 0
            self.entry_price = entry_price
            self.protect_stop_s = (
                entry_price + cfg.ips * self._prev_atr if self._prev_atr is not None else None
            )
            signal, reason, acted = SELL, "enter_short", True

        # 3. EXIT (cover): ATR protective stop, else short-period high channel.
        if not acted and mp_start == -1 and self.bars_since_entry > 0 and volume > 0:
            ps1 = self._prev_protect_stop_s
            es1 = self._prev_exitshort
            if es1 is not None:
                if ps1 is not None and high >= ps1 and ps1 <= es1:
                    self.position = 0
                    self.bars_since_entry = 0
                    self.entry_price = None
                    signal, reason, acted = BUY, "exit_protect_stop", True
                elif high >= es1 + cfg.tick:
                    self.position = 0
                    self.bars_since_entry = 0
                    self.entry_price = None
                    signal, reason, acted = BUY, "exit_channel", True

        # 4. Roll the prev-bar snapshots, then advance counters.
        self._prev_lowerband = lowerband
        self._prev_exitshort = exitshort
        self._prev_atr = atr
        self._prev_protect_stop_s = self.protect_stop_s
        if self.position == -1:
            self.bars_since_entry += 1

        return signal, reason
