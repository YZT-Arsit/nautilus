"""Average True Range primitives shared by feature and strategy adapters."""
from __future__ import annotations

from collections.abc import Sequence
from collections import deque

from feature_engine.indicators.sma import sma


def true_range(high: float, low: float, prev_close: float | None) -> float:
    """Return True Range, using ``high - low`` when no prior close exists."""
    if prev_close is None:
        return high - low
    return max(high - low, abs(high - prev_close), abs(low - prev_close))


def simple_atr(true_ranges: Sequence[float], period: int) -> float | None:
    """Return the simple ATR for the latest ``period`` values, or ``None``."""
    if period <= 0:
        raise ValueError("period must be > 0")
    if len(true_ranges) < period:
        return None
    return sma(list(true_ranges)[-period:])


class SimpleAtr:
    """Streaming simple ATR, matching Nautilus' default SIMPLE calculation."""

    def __init__(self, period: int) -> None:
        if period <= 0:
            raise ValueError("period must be > 0")
        self.period = period
        self._ranges: deque[float] = deque(maxlen=period)
        self._prev_close: float | None = None
        self.value: float | None = None

    def reset(self) -> None:
        self._ranges.clear()
        self._prev_close = None
        self.value = None

    def update(self, high: float, low: float, close: float) -> float | None:
        self._ranges.append(true_range(high, low, self._prev_close))
        self._prev_close = close
        self.value = simple_atr(self._ranges, self.period)
        return self.value
