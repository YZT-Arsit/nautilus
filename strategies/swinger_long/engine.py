"""Swinger long — pure decision engine (position-aware, offline-testable).

Long-side mirror of ``strategies/swinger_short/engine.py``. Holds **only** the
signal-decision maths (plain-Python; no ``feature_engine`` / ``strategy_framework``
/ ``nautilus_trader`` / ``pandas``). Emits ``BUY``/``SELL``/``HOLD`` with the
signal->order meaning left to ``SignalToOrderPolicy`` (``sell_means: flat`` — BUY
opens the long, SELL flattens it). Single unit, no pyramiding.

Ported from the TradeBlazer ``Swinger_L`` system:

* ``TrendMA`` = ``AverageFC(Close, trend_ma_length)`` (SMA-50 trend filter);
* ``PriceOsci`` = ``PriceOscillator(Close, fast, slow)`` = ``SMA(Close,fast) -
  SMA(Close,slow)`` — the momentum of the moving averages;
* entry (long): flat, ``TrendMA[1] != 0``, ``Close[1] > TrendMA[1]`` (price above
  the trend), ``PriceOsci[1] <= 0`` (momentum still negative) AND ``PriceOsci[1] >
  PriceOsci[2]`` (momentum strengthening), ``Vol > 0`` -> buy at Open;
* exit (sell): long for a full bar, ``PriceOsci[1] < PriceOsci[2]`` (momentum
  weakening) AND ``Low <= LowestFC(Low, exit_stop_n)[1]`` -> sell at
  ``Min(Open, ExitL[1])``.

Faithful TradeBlazer semantics preserved (identical to the short engine, mirrored
to the long side):

* All entry/exit comparisons read the **previous** bar's TrendMA / oscillator /
  ExitL (``[1]``) and the oscillator two bars back (``[2]``).
* ``MarketPosition`` is the **bar-start** position (``mp_start``): entry tests
  ``mp_start != 1`` (flat), exit tests ``mp_start == 1`` (long). The exit is
  additionally gated by ``MP[1] == 1`` (the position recorded at the end of the
  previous bar was long), so an entry bar never exits.

Fidelity note: ``PriceOscillator`` is taken as the simple-MA difference
``SMA(Close,fast) - SMA(Close,slow)`` (points), matching the ``AverageFC`` trend
MA; only the sign and the direction of change drive the logic.
"""
from __future__ import annotations

from collections import deque

from feature_engine.indicators import sma
from strategies.swinger_long.config import SwingerLongConfig

BUY, SELL, HOLD = "BUY", "SELL", "HOLD"


class SwingerLongEngine:
    """Pure, position-aware Swinger long engine."""

    def __init__(self, config: SwingerLongConfig) -> None:
        self.cfg = config
        self._fast: deque[float] = deque(maxlen=config.fast_ma_length)
        self._slow: deque[float] = deque(maxlen=config.slow_ma_length)
        self._trend: deque[float] = deque(maxlen=config.trend_ma_length)
        self._lows: deque[float] = deque(maxlen=config.exit_stop_n)

        self.current_bar = 0

        # position state
        self.position = 0                 # 0 flat, +1 long (long-only)
        self.bars_since_entry = 0

        # previous-bar snapshots (the ``[1]`` / ``[2]`` values the decisions read)
        self._prev_close: float | None = None
        self._prev_trend_ma: float | None = None
        self._prev_osci: float | None = None      # PriceOsci[1]
        self._prev2_osci: float | None = None     # PriceOsci[2]
        self._prev_exitl: float | None = None     # ExitL[1] = LowestFC(Low, N)[1]
        self._prev_mp = 0                          # MP[1] = MarketPosition at end of prev bar

    def update(self, open_: float, high: float, low: float, close: float, volume: float):
        """Legacy immediate-fill wrapper retained for baseline compatibility."""
        signal, reason = self.generate_signal(
            open_, high, low, close, volume,
            position=self.position,
            previous_position=self._prev_mp,
            bars_since_entry=self.bars_since_entry,
        )
        if signal == BUY:
            self.position = 1
            self.bars_since_entry = 0
        elif signal == SELL:
            self.position = 0
            self.bars_since_entry = 0
        self._prev_mp = self.position
        if self.position == 1:
            self.bars_since_entry += 1
        return signal, reason

    def generate_signal(
        self, open_: float, high: float, low: float, close: float, volume: float,
        *, position: int, previous_position: int, bars_since_entry: int,
    ):
        """Return the unchanged Swinger signal without assuming a fill."""
        cfg = self.cfg
        self.current_bar += 1

        # 1. current-bar indicators (decisions still read the prev-bar snapshots).
        self._fast.append(close)
        self._slow.append(close)
        self._trend.append(close)
        self._lows.append(low)
        trend_ma = sma(self._trend) if len(self._trend) == cfg.trend_ma_length else None
        fast = sma(self._fast) if len(self._fast) == cfg.fast_ma_length else None
        slow = sma(self._slow) if len(self._slow) == cfg.slow_ma_length else None
        osci = fast - slow if (fast is not None and slow is not None) else None
        exitl = min(self._lows) if len(self._lows) == cfg.exit_stop_n else None

        mp_start = position
        signal, reason = HOLD, "hold"
        acted = False

        # 2. ENTRY (open long): price above the trend, momentum negative & strengthening.
        if (
            not acted and mp_start != 1
            and self._prev_trend_ma is not None and self._prev_trend_ma != 0
            and self._prev_close is not None and self._prev_close > self._prev_trend_ma
            and self._prev_osci is not None and self._prev2_osci is not None
            and self._prev_osci <= 0 and self._prev_osci > self._prev2_osci
            and volume > 0
        ):
            signal, reason, acted = BUY, "enter_long", True

        # 3. EXIT (sell): long a full bar, momentum weakening, break the N-bar low.
        if (
            not acted and mp_start == 1 and previous_position == 1
            and self._prev_osci is not None and self._prev2_osci is not None
            and self._prev_osci < self._prev2_osci
            and self._prev_exitl is not None and low <= self._prev_exitl
            and volume > 0
        ):
            signal, reason, acted = SELL, "exit_sell", True

        # 4. Roll the prev-bar snapshots, then advance counters.
        self._prev_close = close
        self._prev_trend_ma = trend_ma
        self._prev2_osci = self._prev_osci
        self._prev_osci = osci
        self._prev_exitl = exitl

        return signal, reason
