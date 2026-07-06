"""Three EMA Crossover long — pure decision engine (position-aware, offline).

Long-side mirror of ``strategies/three_ema_crossover_short/engine.py``. Holds
**only** the signal-decision maths (plain-Python; no ``strategy_framework`` /
``nautilus_trader`` / ``pandas``). The EMA primitive comes from the shared
``feature_engine.indicators`` library rather than being re-implemented inline. Emits
``BUY``/``SELL``/``HOLD`` with the signal->order meaning left to
``SignalToOrderPolicy`` (``sell_means: flat`` — BUY opens the long, SELL flattens
it). Single unit, no pyramiding.

Ported from the TradeBlazer ``Three_EMA_Crossover_System_L`` system:

* ``Avg1/Avg2/Avg3`` = ``XAverage(Close, avg_len1/2/3)`` — fast / mid / slow EMAs;
* ``BuyCon1`` = ``CrossOver(Avg1, Avg2)`` (fast EMA crosses above the mid EMA);
* entry (long): flat, ``BuyCon1[1]`` AND ``Avg2[1] > Avg3[1]`` AND ``Vol > 0``
  -> buy at Open;
* exit #1 (reversal): ``Avg1[1] < Avg2[1]`` -> sell at Open;
* exit #2 (trailing stop): a stop seeded at ``Low - Average(High-Low, r_length)``
  on the entry bar, then ratcheted **up** each subsequent bar by
  ``LongStopPrice += (Low - LongStopPrice) * 0.25``; sell when
  ``Low <= LongStopPrice[1]``.

``[1]`` semantics preserved: the crossover flag, the ``Avg2>Avg3`` filter, the
reversal test, and the trailing-stop level all read the **previous** bar's
values, and every exit is gated by ``BarsSinceEntry > 0`` so an entry bar never
exits.

Fidelity notes:

* ``XAverage`` is a standard EMA seeded with the first close, ``alpha =
  2/(period+1)`` — identical to Nautilus ``ExponentialMovingAverage`` (used by
  ``vwm_short``), so the pure engine matches the Nautilus-backed indicator.
* TradeBlazer fills entry / reversal at ``Open`` and the trailing stop at
  ``Min(Open, LongStopPrice[1])``. On the shared string-signal path the fill is a
  market fill at the signal bar (same accepted limitation as ``vwm_short``); the
  engine still books ``last_entry_price = Open`` internally.
"""
from __future__ import annotations

from collections import deque

from feature_engine.indicators import Ema

from strategies.three_ema_crossover_long.config import ThreeEmaCrossoverLongConfig

BUY, SELL, HOLD = "BUY", "SELL", "HOLD"


class ThreeEmaCrossoverLongEngine:
    """Pure, position-aware Three EMA Crossover long engine."""

    def __init__(self, config: ThreeEmaCrossoverLongConfig) -> None:
        self.cfg = config
        self._ema1 = Ema(config.avg_len1)
        self._ema2 = Ema(config.avg_len2)
        self._ema3 = Ema(config.avg_len3)
        self._ranges: deque[float] = deque(maxlen=config.r_length)  # MyRange = High-Low

        self.current_bar = 0

        # position state
        self.position = 0                 # 0 flat, +1 long (long-only)
        self.bars_since_entry = 0
        self.long_stop_price: float | None = None
        self.last_entry_price: float | None = None

        # previous-bar snapshots (the ``[1]`` values the decisions read)
        self._prev_avg1: float | None = None
        self._prev_avg2: float | None = None
        self._prev_avg3: float | None = None
        self._prev_buycon1 = False
        self._prev_long_stop: float | None = None

    def update(self, open_: float, high: float, low: float, close: float, volume: float):
        cfg = self.cfg
        self.current_bar += 1

        # 1. EMAs on the current close (capture the prior values for the crossover).
        prev_avg1, prev_avg2 = self._prev_avg1, self._prev_avg2
        avg1 = self._ema1.update(close)
        avg2 = self._ema2.update(close)
        avg3 = self._ema3.update(close)

        # 2. BuyCon1 = CrossOver(Avg1, Avg2): fast was <= mid last bar, now > mid.
        buycon1 = (
            prev_avg1 is not None and prev_avg2 is not None
            and prev_avg1 <= prev_avg2 and avg1 > avg2
        )

        # 3. RangeL = Average(High-Low, r_length) (SMA over the last r_length bars).
        self._ranges.append(high - low)
        range_l = sum(self._ranges) / len(self._ranges)

        signal, reason = HOLD, "hold"
        entered = False

        # 4. ENTRY (open long): previous bar's crossover + mid above slow + volume.
        if (
            self.position == 0
            and self._prev_buycon1
            and self._prev_avg2 is not None
            and self._prev_avg3 is not None
            and self._prev_avg2 > self._prev_avg3
            and volume > 0
        ):
            self.position = 1
            self.bars_since_entry = 0
            self.last_entry_price = open_
            signal, reason = BUY, "enter_long"
            entered = True

        # 5. Trailing-stop bookkeeping: seed on the entry bar, ratchet up after.
        if self.position == 1 and self.bars_since_entry == 0:
            self.long_stop_price = low - range_l
        elif self.position == 1 and self.bars_since_entry > 0 and self.long_stop_price is not None:
            self.long_stop_price = self.long_stop_price + (low - self.long_stop_price) * 0.25

        # 6. EXITS (priority: EMA reversal -> trailing stop), only once long a full bar.
        if self.position == 1 and self.bars_since_entry > 0 and volume > 0 and not entered:
            if (
                self._prev_avg1 is not None
                and self._prev_avg2 is not None
                and self._prev_avg1 < self._prev_avg2
            ):
                signal, reason = SELL, "exit_reversal"
                self._flat()
            elif self._prev_long_stop is not None and low <= self._prev_long_stop:
                signal, reason = SELL, "exit_trailing_stop"
                self._flat()

        # 7. Save the ``[1]`` snapshots, then advance counters.
        self._prev_avg1, self._prev_avg2, self._prev_avg3 = avg1, avg2, avg3
        self._prev_buycon1 = buycon1
        self._prev_long_stop = self.long_stop_price
        if self.position == 1:
            self.bars_since_entry += 1

        return signal, reason

    def _flat(self) -> None:
        self.position = 0
        self.bars_since_entry = 0
        self.long_stop_price = None
        self.last_entry_price = None
