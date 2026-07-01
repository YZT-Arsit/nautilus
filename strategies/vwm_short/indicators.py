"""Self-contained indicator primitives for the VWM short strategy.

Previously spread across ``feature_engine.nautilus_indicators``,
``feature_engine.tradeblazer_features`` and
``nautilus_ext.strategies.tradeblazer_helpers``. Copied here so VWM is
self-contained after the legacy layers are removed.

``EmaFeature`` / ``AtrFeature`` wrap Nautilus native indicators
(``ExponentialMovingAverage`` / ``AverageTrueRange``) — this preserves the
exact XAverage / AvgTrueRange semantics of the original TradeBlazer port.
``nautilus_trader`` is imported at module top; this module is only imported
lazily (when a VWM strategy instance is built).
"""
from __future__ import annotations

from collections import deque

from nautilus_trader.indicators import AverageTrueRange
from nautilus_trader.indicators import ExponentialMovingAverage

from strategies.vwm_short.signal_types import BarInput


# --- TradeBlazer helpers ---------------------------------------------------

class MomentumState:
    """Rolling buffer for Momentum(x, N) = x[t] - x[t-N]."""

    def __init__(self, period: int) -> None:
        if period <= 0:
            raise ValueError("period must be > 0.")
        self.period = period
        self.values: deque[float] = deque(maxlen=period + 1)

    def reset(self) -> None:
        self.values.clear()

    def update(self, value: float) -> float | None:
        self.values.append(value)
        if len(self.values) <= self.period:
            return None
        return self.values[-1] - self.values[0]

    def state_dict(self) -> dict:
        return {"period": self.period, "values": list(self.values)}

    def load_state_dict(self, state: dict) -> None:
        if int(state["period"]) != self.period:
            raise ValueError("MomentumState period does not match checkpoint.")
        values = [float(value) for value in state.get("values", [])]
        if len(values) > self.period + 1:
            raise ValueError("MomentumState checkpoint contains too many values.")
        self.values.clear()
        self.values.extend(values)


def cross_over(prev: float | None, curr: float | None, threshold: float = 0.0) -> bool:
    return prev is not None and curr is not None and prev <= threshold < curr


def cross_under(prev: float | None, curr: float | None, threshold: float = 0.0) -> bool:
    return prev is not None and curr is not None and prev >= threshold > curr


class RawMomentumFeature:
    """TradeBlazer Momentum(Close, N): close[t] - close[t - N]."""

    def __init__(self, period: int) -> None:
        self._state = MomentumState(period)

    def reset(self) -> None:
        self._state.reset()

    def update(self, close: float) -> float | None:
        return self._state.update(close)

    def state_dict(self) -> dict:
        return self._state.state_dict()

    def load_state_dict(self, state: dict) -> None:
        self._state.load_state_dict(state)


# --- Nautilus-backed indicators (XAverage / AvgTrueRange) ------------------

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
