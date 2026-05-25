from __future__ import annotations

from nautilus_ext.strategies.tradeblazer_helpers import MomentumState
from nautilus_ext.strategies.tradeblazer_helpers import cross_over
from nautilus_ext.strategies.tradeblazer_helpers import cross_under


class RawMomentumFeature:
    """TradeBlazer Momentum(Close, N): close[t] - close[t - N]."""

    def __init__(self, period: int) -> None:
        self._state = MomentumState(period)

    def reset(self) -> None:
        self._state.reset()

    def update(self, close: float) -> float | None:
        return self._state.update(close)


__all__ = ["RawMomentumFeature", "cross_over", "cross_under"]
