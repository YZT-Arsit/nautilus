"""OBV Revisited short — pure decision engine (position-aware, offline-testable).

Holds **only** the signal-decision maths (plain-Python; no ``feature_engine`` /
``strategy_framework`` / ``nautilus_trader`` / ``pandas``). Emits
``BUY``/``SELL``/``HOLD`` with the signal->order meaning left to
``SignalToOrderPolicy`` (``sell_means: short`` — SELL opens the short, BUY covers
it). Single unit, no pyramiding.

Ported from the TradeBlazer ``OBVRevisited_S`` system:

* ``WOBV`` is a running sum: when ``High != Low`` add ``((Close-Open)/(High-Low)) *
  Vol`` — a volatility-weighted OBV;
* ``SSMA = Average(WOBV, avg_length)`` (SMA of the WOBV);
* ``con = CrossOver(WOBV, SSMA)`` -> ``SellSetup = False``; ``con2 =
  CrossUnder(WOBV, SSMA)`` -> ``SellSetup = True``, ``SEPrice = Low``;
* while short (``MarketPosition == -1``) ``SellSetup`` is forced ``False``;
* entry (short): flat, ``SellSetup[1]`` and ``Low <= SEPrice[1] - tick`` -> short
  at ``Min(Open, SEPrice[1] - tick)``;
* exit (cover), once ``BarsSinceEntry > 0``: ``con[1]`` (WOBV up-crossed its MA on
  the previous bar) -> cover at Open.

Faithful TradeBlazer semantics preserved: the entry reads ``SellSetup[1]`` /
``SEPrice[1]`` and the exit reads ``con[1]`` (previous-bar values); ``SellSetup`` /
``SEPrice`` persist as running state (armed on a down-cross, cleared on an
up-cross or while short); ``MarketPosition == 0`` / ``== -1`` uses the bar-start
position and the exit is gated by ``BarsSinceEntry > 0``. There is **no** explicit
``Vol > 0`` gate on the orders in the source (WOBV only moves when ``Vol > 0``, so
a zero-volume stream never crosses and never trades).
"""
from __future__ import annotations

from collections import deque

from strategies.obv_revisited_short.config import ObvRevisitedShortConfig

BUY, SELL, HOLD = "BUY", "SELL", "HOLD"


class ObvRevisitedShortEngine:
    """Pure, position-aware OBV Revisited short engine."""

    def __init__(self, config: ObvRevisitedShortConfig) -> None:
        self.cfg = config
        self._wobv = 0.0
        self._wobv_hist: deque[float] = deque(maxlen=config.avg_length)

        self.current_bar = 0

        # position state
        self.position = 0                 # 0 flat, -1 short (short-only)
        self.bars_since_entry = 0
        self.entry_price: float | None = None

        # persistent setup state
        self.sell_setup = False
        self.se_price: float | None = None

        # previous-bar snapshots (the ``[1]`` values the decisions read)
        self._prev_wobv: float | None = None
        self._prev_ssma: float | None = None
        self._prev_sell_setup = False
        self._prev_se_price: float | None = None
        self._prev_con = False            # con[1] (CrossOver on the previous bar)

    def update(self, open_: float, high: float, low: float, close: float, volume: float):
        cfg = self.cfg
        self.current_bar += 1

        # 1. WOBV (volatility-weighted OBV) + its MA.
        if high - low != 0:
            self._wobv += ((close - open_) / (high - low)) * volume
        self._wobv_hist.append(self._wobv)
        ssma = sum(self._wobv_hist) / len(self._wobv_hist) if len(self._wobv_hist) == cfg.avg_length else None

        # 2. WOBV / MA crossovers (need the prior WOBV & MA).
        con = con2 = False
        if self._prev_wobv is not None and self._prev_ssma is not None and ssma is not None:
            con = self._prev_wobv <= self._prev_ssma and self._wobv > ssma
            con2 = self._prev_wobv >= self._prev_ssma and self._wobv < ssma

        # 3. Update the setup state (up-cross clears, down-cross arms at this low).
        if con:
            self.sell_setup = False
        if con2:
            self.sell_setup = True
            self.se_price = low

        mp_start = self.position

        # While short, the setup is forced off (TradeBlazer reset).
        if mp_start == -1:
            self.sell_setup = False

        signal, reason = HOLD, "hold"
        acted = False

        # 4. ENTRY (open short): previous bar armed + break of the prior trigger.
        if (
            not acted and mp_start == 0
            and self._prev_sell_setup and self._prev_se_price is not None
            and low <= self._prev_se_price - cfg.tick
        ):
            entry_price = min(open_, self._prev_se_price - cfg.tick)
            self.position = -1
            self.bars_since_entry = 0
            self.entry_price = entry_price
            signal, reason, acted = SELL, "enter_short", True

        # 5. EXIT (cover): WOBV up-crossed its MA on the previous bar.
        if not acted and mp_start == -1 and self.bars_since_entry > 0 and self._prev_con:
            self.position = 0
            self.bars_since_entry = 0
            self.entry_price = None
            signal, reason, acted = BUY, "exit_cover", True

        # 6. Roll the prev-bar snapshots, then advance counters.
        self._prev_wobv = self._wobv
        self._prev_ssma = ssma
        self._prev_sell_setup = self.sell_setup
        self._prev_se_price = self.se_price
        self._prev_con = con
        if self.position == -1:
            self.bars_since_entry += 1

        return signal, reason
