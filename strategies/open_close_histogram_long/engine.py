"""Open/Close Histogram long — pure decision engine (position-aware, offline).

Long-side mirror of ``strategies/open_close_histogram_short/engine.py``. Holds
**only** the signal-decision maths (plain-Python; no ``strategy_framework`` /
``nautilus_trader`` / ``pandas``). The low-level indicator primitives (EMA and
true range) come from the shared ``feature_engine.indicators`` library rather than
being re-implemented inline. Emits
``BUY``/``SELL``/``HOLD`` with the signal->order meaning left to
``SignalToOrderPolicy`` (``sell_means: flat`` — BUY opens the long, SELL flattens
it). Single unit, no pyramiding.

Ported from the TradeBlazer ``Open_Close_Histogram_L`` system:

* ``Histogram = XAverage(Close, close_len) - XAverage(Open, open_len)`` (close EMA
  minus open EMA);
* ``con1 = CrossOver(Histogram, 0)`` (up-cross, uptrend), ``con2 =
  CrossUnder(Histogram, 0)`` (down-cross, downtrend);
* ``ATR10 = Average(TrueRange, atr_len)``;
* on ``con1`` arm the triggers ``BuyPrice = High + ATR10*0.5`` and
  ``LongExitPrice = Low - ATR10*0.5`` (they persist until the next ``con1``);
* entry (long): flat, ``Histogram[1] > 0`` (uptrend ongoing) and ``High >=
  BuyPrice``, ``Vol > 0`` -> long at ``Max(Open, BuyPrice)``;
* exit (sell), once ``BarsSinceEntry > 0``: reverse-trend ``con2[1]`` -> sell at
  Open; else break ``Low <= LongExitPrice`` -> sell at ``Min(Open, LongExitPrice)``.

Faithful TradeBlazer semantics preserved (identical to the short engine, mirrored
to the long side): ``XAverage`` is a standard EMA seeded with the first value; the
entry reads ``Histogram[1]`` and the reverse exit reads ``con2[1]`` (previous bar);
``BuyPrice``/``LongExitPrice`` are armed on the up-cross bar and held; the TB entry
has no explicit ``MarketPosition == 0`` (a second ``Buy`` while long is a no-op
with pyramiding off) — modelled here as flat-only entry; ``MarketPosition == 0`` /
``== 1`` uses the bar-start position; exits are gated by ``BarsSinceEntry > 0``.
Exit priority: reverse-trend then exit trigger. Simple-mean ATR.
"""
from __future__ import annotations

from collections import deque

from feature_engine.indicators import Ema, true_range

from strategies.open_close_histogram_long.config import OpenCloseHistogramLongConfig

BUY, SELL, HOLD = "BUY", "SELL", "HOLD"


class OpenCloseHistogramLongEngine:
    """Pure, position-aware Open/Close Histogram long engine."""

    def __init__(self, config: OpenCloseHistogramLongConfig) -> None:
        self.cfg = config
        self._ema_close = Ema(config.close_len)
        self._ema_open = Ema(config.open_len)
        self._trs: deque[float] = deque(maxlen=config.atr_len)
        self._tr_prev_close: float | None = None

        self.current_bar = 0

        # position state
        self.position = 0                 # 0 flat, +1 long (long-only)
        self.bars_since_entry = 0
        self.entry_price: float | None = None

        # persistent triggers (armed on an up-cross, held until the next one)
        self.buy_price: float | None = None
        self.long_exit_price: float | None = None

        # previous-bar snapshots (the ``[1]`` values the decisions read)
        self._prev_hist: float | None = None
        self._prev_con2 = False

    def update(self, open_: float, high: float, low: float, close: float, volume: float):
        cfg = self.cfg
        self.current_bar += 1

        # 1. Histogram (close EMA minus open EMA) + zero-cross flags.
        hist = self._ema_close.update(close) - self._ema_open.update(open_)
        con1 = self._prev_hist is not None and self._prev_hist <= 0 and hist > 0
        con2 = self._prev_hist is not None and self._prev_hist >= 0 and hist < 0

        # 2. ATR (simple mean of true range).
        tr = true_range(high, low, self._tr_prev_close)
        self._trs.append(tr)
        self._tr_prev_close = close
        atr10 = sum(self._trs) / len(self._trs) if len(self._trs) == cfg.atr_len else None

        # 3. On an up-cross arm the entry / exit triggers (held until the next one).
        if con1 and atr10 is not None:
            self.buy_price = high + atr10 * 0.5
            self.long_exit_price = low - atr10 * 0.5

        mp_start = self.position
        signal, reason = HOLD, "hold"
        acted = False

        # 4. ENTRY (open long): ongoing uptrend + break of the entry trigger.
        if (
            not acted and mp_start == 0
            and self._prev_hist is not None and self._prev_hist > 0
            and self.buy_price is not None and high >= self.buy_price
            and volume > 0
        ):
            entry_price = max(open_, self.buy_price)
            self.position = 1
            self.bars_since_entry = 0
            self.entry_price = entry_price
            signal, reason, acted = BUY, "enter_long", True

        # 5. EXIT (sell): reverse-trend down-cross -> exit trigger break.
        if not acted and mp_start == 1 and self.bars_since_entry > 0 and volume > 0:
            if self._prev_con2:
                self._flat()
                signal, reason, acted = SELL, "exit_trend_down", True
            elif self.long_exit_price is not None and low <= self.long_exit_price:
                self._flat()
                signal, reason, acted = SELL, "exit_trigger", True

        # 6. Roll the prev-bar snapshots, then advance counters.
        self._prev_hist = hist
        self._prev_con2 = con2
        if self.position == 1:
            self.bars_since_entry += 1

        return signal, reason

    def _flat(self) -> None:
        self.position = 0
        self.bars_since_entry = 0
        self.entry_price = None
