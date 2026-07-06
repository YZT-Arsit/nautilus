"""Exponential moving average (TradeBlazer ``XAverage``).

Seeds on the first value and updates with ``alpha = 2 / (period + 1)``. Returns the
current EMA value (equal to the seed value on the first update). Pure Python; no
framework dependency.
"""
from __future__ import annotations


class Ema:
    """Standard EMA (XAverage): seed with the first value, alpha = 2/(period+1)."""

    def __init__(self, period: int) -> None:
        if period <= 0:
            raise ValueError("period must be > 0.")
        self._alpha = 2.0 / (period + 1.0)
        self.value: float | None = None

    def update(self, x: float) -> float | None:
        if self.value is None:
            self.value = x
        else:
            self.value += self._alpha * (x - self.value)
        return self.value
