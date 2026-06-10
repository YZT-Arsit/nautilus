"""MA crossover strategy.

Declares two ``rolling_mean`` features (a fast and a slow moving average) and
turns their crossover into BUY / SELL / HOLD signals. Low-level computation is
handled by the Feature Engine; see ``docs/ma_crossover_strategy_demo.md``.
"""
from __future__ import annotations

from dataclasses import dataclass

from nautilus_ext.features.api import FeatureSnapshot, FeatureSpec

Signal = str  # "BUY" | "SELL" | "HOLD"
BUY, SELL, HOLD = "BUY", "SELL", "HOLD"


@dataclass(frozen=True)
class MovingAverageCrossoverConfig:
    fast_window: int = 5
    slow_window: int = 20
    input_type: str = "bar"
    input_field: str = "close"

    @property
    def fast_name(self) -> str:
        return f"ma{self.fast_window}_{self.input_field}"

    @property
    def slow_name(self) -> str:
        return f"ma{self.slow_window}_{self.input_field}"


def _rolling_mean_spec(name: str, window: int, config: MovingAverageCrossoverConfig) -> FeatureSpec:
    return FeatureSpec(
        name,
        input_type=config.input_type,
        input_field=config.input_field,
        window=window,
        params={"type": "rolling_mean"},
    )


def build_specs(config: MovingAverageCrossoverConfig) -> list[FeatureSpec]:
    return [
        _rolling_mean_spec(config.fast_name, config.fast_window, config),
        _rolling_mean_spec(config.slow_name, config.slow_window, config),
    ]


# TODO: remove once all callers import build_specs.
build_ma_crossover_specs = build_specs


def crossover_signal(
    prev_fast: float | None,
    prev_slow: float | None,
    fast: float | None,
    slow: float | None,
) -> Signal:
    """Pure crossover rule; HOLD until both previous and current MAs exist."""
    if prev_fast is None or prev_slow is None or fast is None or slow is None:
        return HOLD
    if prev_fast <= prev_slow and fast > slow:
        return BUY
    if prev_fast >= prev_slow and fast < slow:
        return SELL
    return HOLD


class MovingAverageCrossoverStrategy:
    """Emit BUY / SELL / HOLD from successive snapshots, tracking previous MAs."""

    def __init__(self, config: MovingAverageCrossoverConfig) -> None:
        self._config = config
        self._prev_fast: float | None = None
        self._prev_slow: float | None = None

    def on_snapshot(self, snapshot: FeatureSnapshot) -> Signal:
        fast = snapshot.value(self._config.fast_name)
        slow = snapshot.value(self._config.slow_name)
        signal = crossover_signal(self._prev_fast, self._prev_slow, fast, slow)
        self._prev_fast, self._prev_slow = fast, slow
        return signal
