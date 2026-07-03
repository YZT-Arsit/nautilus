"""Three EMA Crossover short — pure decision engine (position-aware, offline).

Holds **only** the signal-decision maths (plain-Python; no ``feature_engine`` /
``strategy_framework`` / ``nautilus_trader`` / ``pandas``). Emits
``BUY``/``SELL``/``HOLD`` with the signal->order meaning left to
``SignalToOrderPolicy`` (``sell_means: short`` — SELL opens the short, BUY covers
it). Single unit, no pyramiding.

Ported from the TradeBlazer ``Three_EMA_Crossover_System_S`` system:

* ``Avg1/Avg2/Avg3`` = ``XAverage(Close, avg_len1/2/3)`` — fast / mid / slow EMAs;
* ``SellCon1`` = ``CrossUnder(Avg1, Avg2)`` (fast EMA crosses below the mid EMA);
* entry (short): flat, ``SellCon1[1]`` AND ``Avg2[1] < Avg3[1]`` AND ``Vol > 0``
  -> sell short at Open;
* exit #1 (reversal): ``Avg1[1] > Avg2[1]`` -> cover at Open;
* exit #2 (trailing stop): a stop seeded at ``High + Average(High-Low, r_length)``
  on the entry bar, then ratcheted **down** each subsequent bar by
  ``ShortStopPrice -= (ShortStopPrice - High) / 3``; cover when
  ``High >= ShortStopPrice[1]``.

``[1]`` semantics preserved: the crossover flag, the ``Avg2<Avg3`` filter, the
reversal test, and the trailing-stop level all read the **previous** bar's
values, and every exit is gated by ``BarsSinceEntry > 0`` so an entry bar never
exits.

Fidelity notes:

* ``XAverage`` is a standard EMA seeded with the first close, ``alpha =
  2/(period+1)`` — identical to Nautilus ``ExponentialMovingAverage`` (used by
  ``vwm_short``), so the pure engine matches the Nautilus-backed indicator.
* TradeBlazer fills entry / reversal at ``Open`` and the trailing stop at
  ``Max(Open, ShortStopPrice[1])``. On the shared string-signal path the fill is
  a market fill at the signal bar (same accepted limitation as ``vwm_short``);
  the engine still books ``last_entry_price = Open`` internally.
"""
from __future__ import annotations

from collections import deque

from strategies.three_ema_crossover_short.config import ThreeEmaCrossoverShortConfig

BUY, SELL, HOLD = "BUY", "SELL", "HOLD"


class _Ema:
    """Standard EMA (XAverage): seed with the first value, alpha = 2/(period+1)."""

    def __init__(self, period: int) -> None:
        self._alpha = 2.0 / (period + 1.0)
        self.value: float | None = None

    def update(self, x: float) -> float | None:
        if self.value is None:
            self.value = x
        else:
            self.value += self._alpha * (x - self.value)
        return self.value


class ThreeEmaCrossoverShortEngine:
    """Pure, position-aware Three EMA Crossover short engine."""

    def __init__(self, config: ThreeEmaCrossoverShortConfig) -> None:
        self.cfg = config
        self._ema1 = _Ema(config.avg_len1)
        self._ema2 = _Ema(config.avg_len2)
        self._ema3 = _Ema(config.avg_len3)
        self._ranges: deque[float] = deque(maxlen=config.r_length)  # MyRange = High-Low

        self.current_bar = 0

        # position state
        self.position = 0                 # 0 flat, -1 short (short-only)
        self.bars_since_entry = 0
        self.short_stop_price: float | None = None
        self.last_entry_price: float | None = None

        # previous-bar snapshots (the ``[1]`` values the decisions read)
        self._prev_avg1: float | None = None
        self._prev_avg2: float | None = None
        self._prev_avg3: float | None = None
        self._prev_sellcon1 = False
        self._prev_short_stop: float | None = None

    def update(self, open_: float, high: float, low: float, close: float, volume: float):
        cfg = self.cfg
        self.current_bar += 1

        # 1. EMAs on the current close (capture the prior values for the crossover).
        prev_avg1, prev_avg2 = self._prev_avg1, self._prev_avg2
        avg1 = self._ema1.update(close)
        avg2 = self._ema2.update(close)
        avg3 = self._ema3.update(close)

        # 2. SellCon1 = CrossUnder(Avg1, Avg2): fast was >= mid last bar, now < mid.
        sellcon1 = (
            prev_avg1 is not None and prev_avg2 is not None
            and prev_avg1 >= prev_avg2 and avg1 < avg2
        )

        # 3. RangeS = Average(High-Low, r_length) (SMA over the last r_length bars).
        self._ranges.append(high - low)
        range_s = sum(self._ranges) / len(self._ranges)

        signal, reason = HOLD, "hold"
        entered = False

        # 4. ENTRY (open short): previous bar's crossover + mid below slow + volume.
        if (
            self.position == 0
            and self._prev_sellcon1
            and self._prev_avg2 is not None
            and self._prev_avg3 is not None
            and self._prev_avg2 < self._prev_avg3
            and volume > 0
        ):
            self.position = -1
            self.bars_since_entry = 0
            self.last_entry_price = open_
            signal, reason = SELL, "enter_short"
            entered = True

        # 5. Trailing-stop bookkeeping: seed on the entry bar, ratchet down after.
        if self.position == -1 and self.bars_since_entry == 0:
            self.short_stop_price = high + range_s
        elif self.position == -1 and self.bars_since_entry > 0 and self.short_stop_price is not None:
            self.short_stop_price = self.short_stop_price - (self.short_stop_price - high) / 3.0

        # 6. EXITS (priority: EMA reversal -> trailing stop), only once short a full bar.
        if self.position == -1 and self.bars_since_entry > 0 and volume > 0 and not entered:
            if (
                self._prev_avg1 is not None
                and self._prev_avg2 is not None
                and self._prev_avg1 > self._prev_avg2
            ):
                signal, reason = BUY, "exit_reversal"
                self._flat()
            elif self._prev_short_stop is not None and high >= self._prev_short_stop:
                signal, reason = BUY, "exit_trailing_stop"
                self._flat()

        # 7. Save the ``[1]`` snapshots, then advance counters.
        self._prev_avg1, self._prev_avg2, self._prev_avg3 = avg1, avg2, avg3
        self._prev_sellcon1 = sellcon1
        self._prev_short_stop = self.short_stop_price
        if self.position == -1:
            self.bars_since_entry += 1

        return signal, reason

    def _flat(self) -> None:
        self.position = 0
        self.bars_since_entry = 0
        self.short_stop_price = None
        self.last_entry_price = None
