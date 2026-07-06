"""RedRover short — pure decision engine (position-aware, offline-testable).

Holds **only** the signal-decision maths (plain-Python; no ``feature_engine`` /
``strategy_framework`` / ``nautilus_trader`` / ``pandas``). Emits
``BUY``/``SELL``/``HOLD`` with the signal->order meaning left to
``SignalToOrderPolicy`` (``sell_means: short`` — SELL opens the short, BUY covers
it). Single unit, no pyramiding.

Ported from the TradeBlazer ``RedRover_S`` system:

* ``WAvgPrice = (High + Low + 2*Close) / 4`` — weighted bar price;
* ``Resistance = 2*WAvgPrice - Low`` (== ``Close + range/2``);
* ``Support = 2*WAvgPrice - High`` (== ``Close - range/2``);
* entry (short): flat, ``Low <= Support[1] - tick`` (breaks the prior support),
  ``Vol > 0`` -> short at ``Min(Open, Support[1] - tick)``; at the entry bar record
  ``myExitPrice = EntryPrice - ATR * atr_s`` (this bar's ATR);
* exit (cover), once ``BarsSinceEntry > 0``: profit target ``Low <= myExitPrice``
  -> ``Min(Open, myExitPrice)``; else reverse ``High >= Resistance[1] + tick`` ->
  ``Max(Open, Resistance[1] + tick)``.

Faithful TradeBlazer semantics preserved: the entry / reverse compare against the
**previous** bar's Support / Resistance (``[1]``); ``myExitPrice`` is fixed at the
entry bar from that bar's ATR and the entry fill, and held; ``MarketPosition == 0``
/ ``== -1`` uses the bar-start position and the exit is gated by
``BarsSinceEntry > 0`` (an entry bar never exits). Exit priority: profit target
then reverse.

Fidelity note: ``AvgTrueRange`` is a simple mean of true range over ``atr_length``
(matches ``trend_breakout_atr`` / ``trendscore_*``; the TradeBlazer builtin uses
Wilder smoothing) — keeps the engine Nautilus-free.
"""
from __future__ import annotations

from collections import deque

from feature_engine.indicators import true_range
from strategies.redrover_short.config import RedRoverShortConfig

BUY, SELL, HOLD = "BUY", "SELL", "HOLD"


class RedRoverShortEngine:
    """Pure, position-aware RedRover short engine."""

    def __init__(self, config: RedRoverShortConfig) -> None:
        self.cfg = config
        self._trs: deque[float] = deque(maxlen=config.atr_length)
        self._tr_prev_close: float | None = None

        self.current_bar = 0

        # position state
        self.position = 0                 # 0 flat, -1 short (short-only)
        self.bars_since_entry = 0
        self.entry_price: float | None = None
        self.my_exit_price: float | None = None   # ATR profit target, fixed at entry

        # previous-bar snapshots (the ``[1]`` values the decisions read)
        self._prev_support: float | None = None
        self._prev_resistance: float | None = None

    def update(self, open_: float, high: float, low: float, close: float, volume: float):
        cfg = self.cfg
        self.current_bar += 1

        # 1. ATR (simple mean of true range).
        tr = true_range(high, low, self._tr_prev_close)
        self._trs.append(tr)
        self._tr_prev_close = close
        atr = sum(self._trs) / len(self._trs) if len(self._trs) == cfg.atr_length else None

        # 2. Weighted price + support / resistance (current bar).
        wavg = (high + low + 2.0 * close) / 4.0
        resistance = 2.0 * wavg - low
        support = 2.0 * wavg - high

        mp_start = self.position
        signal, reason = HOLD, "hold"
        acted = False

        # 3. ENTRY (open short): break the prior support line.
        if (
            not acted and mp_start == 0
            and self._prev_support is not None and atr is not None
            and low <= self._prev_support - cfg.tick
            and volume > 0
        ):
            entry_price = min(open_, self._prev_support - cfg.tick)
            self.position = -1
            self.bars_since_entry = 0
            self.entry_price = entry_price
            self.my_exit_price = entry_price - atr * cfg.atr_s
            signal, reason, acted = SELL, "enter_short", True

        # 4. EXIT (cover): profit target -> reverse break.
        if not acted and mp_start == -1 and self.bars_since_entry > 0 and volume > 0:
            if self.my_exit_price is not None and low <= self.my_exit_price:
                self._cover()
                signal, reason, acted = BUY, "exit_take_profit", True
            elif (
                self._prev_resistance is not None
                and high >= self._prev_resistance + cfg.tick
            ):
                self._cover()
                signal, reason, acted = BUY, "exit_reverse", True

        # 5. Roll the prev-bar snapshots, then advance counters.
        self._prev_support = support
        self._prev_resistance = resistance
        if self.position == -1:
            self.bars_since_entry += 1

        return signal, reason

    def _cover(self) -> None:
        self.position = 0
        self.bars_since_entry = 0
        self.entry_price = None
        self.my_exit_price = None
