from __future__ import annotations

from nautilus_trader.indicators import AverageTrueRange
from nautilus_trader.indicators import ExponentialMovingAverage

from nautilus_ext.strategies.signal_types import BarInput


class EmaFeature:
    """Thin streaming wrapper for Nautilus native EMA/XAverage behavior."""

    def __init__(self, period: int) -> None:
        if period <= 0:
            raise ValueError("period must be > 0.")
        self.indicator = ExponentialMovingAverage(period)

    @property
    def value(self) -> float | None:
        return self.indicator.value if self.indicator.has_inputs else None

    def reset(self) -> None:
        self.indicator.reset()

    def update_raw(self, value: float) -> float | None:
        self.indicator.update_raw(value)
        return self.value


class AtrFeature:
    """Thin streaming wrapper for Nautilus native AverageTrueRange."""

    def __init__(self, period: int) -> None:
        if period <= 0:
            raise ValueError("period must be > 0.")
        self.indicator = AverageTrueRange(period)

    @property
    def value(self) -> float | None:
        return self.indicator.value if self.indicator.initialized else None

    def reset(self) -> None:
        self.indicator.reset()

    def update(self, bar: BarInput) -> float | None:
        self.indicator.update_raw(bar.high, bar.low, bar.close)
        return self.value
