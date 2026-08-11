"""FeatureSnapshot-only signal logic for the continuous tick MA strategy."""
from __future__ import annotations

from typing import Literal

from feature_engine.api import FeatureSnapshot
from strategies.continuous_tick_ma.config import ContinuousTickMaConfig

Signal = Literal["BUY", "SELL", "HOLD"]
BUY, SELL, HOLD = "BUY", "SELL", "HOLD"


def crossover_signal(
    prev_fast: float | None,
    prev_slow: float | None,
    fast: float | None,
    slow: float | None,
) -> Signal:
    """Return a crossing signal; equality belongs to the pre-cross side."""
    if prev_fast is None or prev_slow is None or fast is None or slow is None:
        return HOLD
    if prev_fast <= prev_slow and fast > slow:
        return BUY
    if prev_fast >= prev_slow and fast < slow:
        return SELL
    return HOLD


class ContinuousTickMaStrategy:
    """Consume 5/10-minute event-time means and emit BUY/SELL/HOLD only."""

    def __init__(self, config: ContinuousTickMaConfig) -> None:
        self._config = config
        self._prev_fast: float | None = None
        self._prev_slow: float | None = None

    def on_snapshot(self, snapshot: FeatureSnapshot) -> Signal:
        fast = snapshot.value(self._config.fast_name)
        slow = snapshot.value(self._config.slow_name)
        signal = crossover_signal(self._prev_fast, self._prev_slow, fast, slow)
        self._prev_fast, self._prev_slow = fast, slow
        return signal

    def on_warmup_snapshot(self, snapshot: FeatureSnapshot) -> None:
        self._prev_fast = snapshot.value(self._config.fast_name)
        self._prev_slow = snapshot.value(self._config.slow_name)
