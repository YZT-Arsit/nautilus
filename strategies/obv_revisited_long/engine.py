"""OBV Revisited long — pure decision engine (position-aware, offline-testable).

Long-side mirror of ``strategies/obv_revisited_short/engine.py``. Holds **only**
the signal-decision maths (plain-Python; no ``feature_engine`` /
``strategy_framework`` / ``nautilus_trader`` / ``pandas``). Emits
``BUY``/``SELL``/``HOLD`` with the signal->order meaning left to
``SignalToOrderPolicy`` (``sell_means: flat`` — BUY opens the long, SELL flattens
it). Single unit, no pyramiding.

Ported from the TradeBlazer ``OBVRevisited_L`` system:

* ``WOBV`` is a running sum: when ``High != Low`` add ``((Close-Open)/(High-Low)) *
  Vol`` — a volatility-weighted OBV;
* ``SSMA = Average(WOBV, avg_length)`` (SMA of the WOBV);
* ``con = CrossOver(WOBV, SSMA)`` -> ``BuySetup = True``, ``LEPrice = High``;
  ``con2 = CrossUnder(WOBV, SSMA)`` -> ``BuySetup = False``;
* while long (``MarketPosition == 1``) ``BuySetup`` is forced ``False``;
* entry (long): flat, ``BuySetup[1]`` and ``High >= LEPrice[1] + tick`` -> long at
  ``Max(Open, LEPrice[1] + tick)``;
* exit (sell), once ``BarsSinceEntry > 0``: ``con2[1]`` (WOBV down-crossed its MA
  on the previous bar) -> sell at Open.

Faithful TradeBlazer semantics preserved (identical to the short engine, mirrored
to the long side): the entry reads ``BuySetup[1]`` / ``LEPrice[1]`` and the exit
reads ``con2[1]``; ``BuySetup`` / ``LEPrice`` persist as running state (armed on an
up-cross, cleared on a down-cross or while long); ``MarketPosition == 0`` / ``== 1``
uses the bar-start position and the exit is gated by ``BarsSinceEntry > 0``. There
is **no** explicit ``Vol > 0`` gate on the orders (WOBV only moves when ``Vol > 0``).
"""
from __future__ import annotations

from collections import deque

from feature_engine.indicators import sma
from strategies.obv_revisited_long.config import ObvRevisitedLongConfig

BUY, SELL, HOLD = "BUY", "SELL", "HOLD"


class ObvRevisitedLongEngine:
    """Pure, position-aware OBV Revisited long engine."""

    def __init__(self, config: ObvRevisitedLongConfig) -> None:
        self.cfg = config
        self._wobv = 0.0
        self._wobv_hist: deque[float] = deque(maxlen=config.avg_length)

        self.current_bar = 0

        # position state
        self.position = 0                 # 0 flat, +1 long (long-only)
        self.bars_since_entry = 0
        self.entry_price: float | None = None

        # persistent setup state
        self.buy_setup = False
        self.le_price: float | None = None

        # previous-bar snapshots (the ``[1]`` values the decisions read)
        self._prev_wobv: float | None = None
        self._prev_ssma: float | None = None
        self._prev_buy_setup = False
        self._prev_le_price: float | None = None
        self._prev_con2 = False           # con2[1] (CrossUnder on the previous bar)

    def update(self, open_: float, high: float, low: float, close: float, volume: float):
        cfg = self.cfg
        self.current_bar += 1

        # 1. WOBV (volatility-weighted OBV) + its MA.
        if high - low != 0:
            self._wobv += ((close - open_) / (high - low)) * volume
        self._wobv_hist.append(self._wobv)
        ssma = sma(self._wobv_hist) if len(self._wobv_hist) == cfg.avg_length else None

        # 2. WOBV / MA crossovers (need the prior WOBV & MA).
        con = con2 = False
        if self._prev_wobv is not None and self._prev_ssma is not None and ssma is not None:
            con = self._prev_wobv <= self._prev_ssma and self._wobv > ssma
            con2 = self._prev_wobv >= self._prev_ssma and self._wobv < ssma

        # 3. Update the setup state (up-cross arms at this high, down-cross clears).
        if con:
            self.buy_setup = True
            self.le_price = high
        if con2:
            self.buy_setup = False

        mp_start = self.position

        # While long, the setup is forced off (TradeBlazer reset).
        if mp_start == 1:
            self.buy_setup = False

        signal, reason = HOLD, "hold"
        acted = False

        # 4. ENTRY (open long): previous bar armed + break of the prior trigger.
        if (
            not acted and mp_start == 0
            and self._prev_buy_setup and self._prev_le_price is not None
            and high >= self._prev_le_price + cfg.tick
        ):
            entry_price = max(open_, self._prev_le_price + cfg.tick)
            self.position = 1
            self.bars_since_entry = 0
            self.entry_price = entry_price
            signal, reason, acted = BUY, "enter_long", True

        # 5. EXIT (sell): WOBV down-crossed its MA on the previous bar.
        if not acted and mp_start == 1 and self.bars_since_entry > 0 and self._prev_con2:
            self.position = 0
            self.bars_since_entry = 0
            self.entry_price = None
            signal, reason, acted = SELL, "exit_sell", True

        # 6. Roll the prev-bar snapshots, then advance counters.
        self._prev_wobv = self._wobv
        self._prev_ssma = ssma
        self._prev_buy_setup = self.buy_setup
        self._prev_le_price = self.le_price
        self._prev_con2 = con2
        if self.position == 1:
            self.bars_since_entry += 1

        return signal, reason
