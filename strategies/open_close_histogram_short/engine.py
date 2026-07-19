"""Open/Close Histogram short — pure decision engine (position-aware, offline).

Holds **only** the signal-decision maths (plain-Python; no ``strategy_framework``
/ ``nautilus_trader`` / ``pandas``). The low-level indicator primitives (EMA and
true range) come from the shared ``feature_engine.indicators`` library rather than
being re-implemented inline. Emits
``BUY``/``SELL``/``HOLD`` with the signal->order meaning left to
``SignalToOrderPolicy`` (``sell_means: short`` — SELL opens the short, BUY covers
it). Single unit, no pyramiding.

Ported from the TradeBlazer ``Open_Close_Histogram_S`` system:

* ``Histogram = XAverage(Close, close_len) - XAverage(Open, open_len)`` (close EMA
  minus open EMA);
* ``con1 = CrossOver(Histogram, 0)`` (up-cross, uptrend), ``con2 =
  CrossUnder(Histogram, 0)`` (down-cross, downtrend);
* ``ATR10 = Average(TrueRange, atr_len)``;
* on ``con2`` arm the triggers ``SellPrice = Low - ATR10*0.5`` and
  ``ShortExitPrice = High + ATR10*0.5`` (they persist until the next ``con2``);
* entry (short): flat, ``Histogram[1] < 0`` (downtrend ongoing) and ``Low <=
  SellPrice``, ``Vol > 0`` -> short at ``Min(Open, SellPrice)``;
* exit (cover), once ``BarsSinceEntry > 0``: reverse-trend ``con1[1]`` -> cover at
  Open; else break ``High >= ShortExitPrice`` -> cover at ``Max(Open, ShortExitPrice)``.

Faithful TradeBlazer semantics preserved: ``XAverage`` is a standard EMA seeded
with the first value (``alpha = 2/(N+1)``, matching Nautilus/``three_ema_*``); the
entry reads ``Histogram[1]`` and the reverse exit reads ``con1[1]`` (previous bar);
``SellPrice``/``ShortExitPrice`` are armed on the down-cross bar and held. The TB
entry block has no explicit ``MarketPosition == 0`` (a second ``SellShort`` while
short is a no-op with pyramiding off) — modelled here as flat-only entry.
``MarketPosition == 0`` / ``== -1`` uses the bar-start position; exits are gated by
``BarsSinceEntry > 0``. Exit priority: reverse-trend then exit trigger. ATR is a
simple mean of true range (documented deviation from any Wilder builtin).
"""
from __future__ import annotations

from collections import deque

from feature_engine.indicators import Ema, simple_atr, true_range

from strategies.open_close_histogram_short.config import OpenCloseHistogramShortConfig

BUY, SELL, HOLD = "BUY", "SELL", "HOLD"


class OpenCloseHistogramShortEngine:
    """Pure, position-aware Open/Close Histogram short engine."""

    def __init__(self, config: OpenCloseHistogramShortConfig) -> None:
        self.cfg = config
        self._ema_close = Ema(config.close_len)
        self._ema_open = Ema(config.open_len)
        self._trs: deque[float] = deque(maxlen=config.atr_len)
        self._tr_prev_close: float | None = None

        self.current_bar = 0

        # position state
        self.position = 0                 # 0 flat, -1 short (short-only)
        self.bars_since_entry = 0
        self.entry_price: float | None = None

        # persistent triggers (armed on a down-cross, held until the next one)
        self.sell_price: float | None = None
        self.short_exit_price: float | None = None

        # previous-bar snapshots (the ``[1]`` values the decisions read)
        self._prev_hist: float | None = None
        self._prev_con1 = False

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
        atr10 = simple_atr(self._trs, cfg.atr_len)

        # 3. On a down-cross arm the entry / exit triggers (held until the next one).
        if con2 and atr10 is not None:
            self.sell_price = low - atr10 * 0.5
            self.short_exit_price = high + atr10 * 0.5

        mp_start = self.position
        signal, reason = HOLD, "hold"
        acted = False

        # 4. ENTRY (open short): ongoing downtrend + break of the entry trigger.
        if (
            not acted and mp_start == 0
            and self._prev_hist is not None and self._prev_hist < 0
            and self.sell_price is not None and low <= self.sell_price
            and volume > 0
        ):
            entry_price = min(open_, self.sell_price)
            self.position = -1
            self.bars_since_entry = 0
            self.entry_price = entry_price
            signal, reason, acted = SELL, "enter_short", True

        # 5. EXIT (cover): reverse-trend up-cross -> exit trigger break.
        if not acted and mp_start == -1 and self.bars_since_entry > 0 and volume > 0:
            if self._prev_con1:
                self._cover()
                signal, reason, acted = BUY, "exit_trend_up", True
            elif self.short_exit_price is not None and high >= self.short_exit_price:
                self._cover()
                signal, reason, acted = BUY, "exit_trigger", True

        # 6. Roll the prev-bar snapshots, then advance counters.
        self._prev_hist = hist
        self._prev_con1 = con1
        if self.position == -1:
            self.bars_since_entry += 1

        return signal, reason

    def _cover(self) -> None:
        self.position = 0
        self.bars_since_entry = 0
        self.entry_price = None
