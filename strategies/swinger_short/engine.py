"""Swinger short — pure decision engine (position-aware, offline-testable).

Holds **only** the signal-decision maths (plain-Python; no ``feature_engine`` /
``strategy_framework`` / ``nautilus_trader`` / ``pandas``). Emits
``BUY``/``SELL``/``HOLD`` with the signal->order meaning left to
``SignalToOrderPolicy`` (``sell_means: short`` — SELL opens the short, BUY covers
it). Single unit, no pyramiding.

Ported from the TradeBlazer ``Swinger_S`` system:

* ``TrendMA`` = ``AverageFC(Close, trend_ma_length)`` (SMA-50 trend filter);
* ``PriceOsci`` = ``PriceOscillator(Close, fast, slow)`` = ``SMA(Close,fast) -
  SMA(Close,slow)`` — the momentum of the moving averages;
* entry (short): flat, ``TrendMA[1] != 0``, ``Close[1] < TrendMA[1]`` (price under
  the trend), ``PriceOsci[1] >= 0`` (momentum still positive) AND ``PriceOsci[1] <
  PriceOsci[2]`` (momentum weakening), ``Vol > 0`` -> sell short at Open;
* exit (cover): short for a full bar, ``PriceOsci[1] > PriceOsci[2]`` (momentum
  strengthening) AND ``High >= HighestFC(High, exit_stop_n)[1]`` -> cover at
  ``Max(Open, ExitS[1])``.

Faithful TradeBlazer semantics preserved:

* All entry/exit comparisons read the **previous** bar's TrendMA / oscillator /
  ExitS (``[1]``) and the oscillator two bars back (``[2]``).
* ``MarketPosition`` is the **bar-start** position (``mp_start``): entry tests
  ``mp_start != -1`` (flat), exit tests ``mp_start == -1`` (short). The exit is
  additionally gated by ``MP[1] == -1`` (the position recorded at the end of the
  previous bar was short), so an entry bar never exits.

Fidelity note: ``PriceOscillator`` is taken as the simple-MA difference
``SMA(Close,fast) - SMA(Close,slow)`` (points), matching the ``AverageFC`` trend
MA; only the sign and the direction of change drive the logic.
"""
from __future__ import annotations

from collections import deque

from feature_engine.indicators import sma
from strategies.swinger_short.config import SwingerShortConfig

BUY, SELL, HOLD = "BUY", "SELL", "HOLD"


class SwingerShortEngine:
    """Pure, position-aware Swinger short engine."""

    def __init__(self, config: SwingerShortConfig) -> None:
        self.cfg = config
        self._fast: deque[float] = deque(maxlen=config.fast_ma_length)
        self._slow: deque[float] = deque(maxlen=config.slow_ma_length)
        self._trend: deque[float] = deque(maxlen=config.trend_ma_length)
        self._highs: deque[float] = deque(maxlen=config.exit_stop_n)

        self.current_bar = 0

        # position state
        self.position = 0                 # 0 flat, -1 short (short-only)
        self.bars_since_entry = 0

        # previous-bar snapshots (the ``[1]`` / ``[2]`` values the decisions read)
        self._prev_close: float | None = None
        self._prev_trend_ma: float | None = None
        self._prev_osci: float | None = None      # PriceOsci[1]
        self._prev2_osci: float | None = None     # PriceOsci[2]
        self._prev_exits: float | None = None     # ExitS[1] = HighestFC(High, N)[1]
        self._prev_mp = 0                          # MP[1] = MarketPosition at end of prev bar

    def update(self, open_: float, high: float, low: float, close: float, volume: float):
        """Legacy immediate-fill wrapper retained for baseline compatibility."""
        signal, reason = self.generate_signal(
            open_, high, low, close, volume,
            position=self.position,
            previous_position=self._prev_mp,
            bars_since_entry=self.bars_since_entry,
        )
        if signal == SELL:
            self.position = -1
            self.bars_since_entry = 0
        elif signal == BUY:
            self.position = 0
            self.bars_since_entry = 0
        self._prev_mp = self.position
        if self.position == -1:
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
        self._highs.append(high)
        trend_ma = sma(self._trend) if len(self._trend) == cfg.trend_ma_length else None
        fast = sma(self._fast) if len(self._fast) == cfg.fast_ma_length else None
        slow = sma(self._slow) if len(self._slow) == cfg.slow_ma_length else None
        osci = fast - slow if (fast is not None and slow is not None) else None
        exits = max(self._highs) if len(self._highs) == cfg.exit_stop_n else None

        mp_start = position
        signal, reason = HOLD, "hold"
        acted = False

        # 2. ENTRY (open short): price below the trend, momentum positive & weakening.
        if (
            not acted and mp_start != -1
            and self._prev_trend_ma is not None and self._prev_trend_ma != 0
            and self._prev_close is not None and self._prev_close < self._prev_trend_ma
            and self._prev_osci is not None and self._prev2_osci is not None
            and self._prev_osci >= 0 and self._prev_osci < self._prev2_osci
            and volume > 0
        ):
            signal, reason, acted = SELL, "enter_short", True

        # 3. EXIT (cover): short a full bar, momentum strengthening, break the N-bar high.
        if (
            not acted and mp_start == -1 and previous_position == -1
            and self._prev_osci is not None and self._prev2_osci is not None
            and self._prev_osci > self._prev2_osci
            and self._prev_exits is not None and high >= self._prev_exits
            and volume > 0
        ):
            signal, reason, acted = BUY, "exit_cover", True

        # 4. Roll the prev-bar snapshots, then advance counters.
        self._prev_close = close
        self._prev_trend_ma = trend_ma
        self._prev2_osci = self._prev_osci
        self._prev_osci = osci
        self._prev_exits = exits

        return signal, reason
