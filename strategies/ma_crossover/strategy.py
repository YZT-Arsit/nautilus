from __future__ import annotations
from dataclasses import dataclass
from typing import Literal
from feature_engine.api import FeatureSnapshot, FeatureSpec, rolling_mean_spec
from strategy_framework.plugin import StrategyPlugin

Signal = Literal["BUY", "SELL", "HOLD"]
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

def build_specs(config: MovingAverageCrossoverConfig) -> list[FeatureSpec]:
    kw = {"input_type": config.input_type, "input_field": config.input_field}
    return [
        rolling_mean_spec(config.fast_name, window=config.fast_window, **kw),
        rolling_mean_spec(config.slow_name, window=config.slow_window, **kw),
    ]

# Backward-compatible alias; prefer build_specs in new code.
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

PLUGIN = StrategyPlugin(
    name="ma_crossover",
    config_cls=MovingAverageCrossoverConfig,
    strategy_cls=MovingAverageCrossoverStrategy,
    build_specs=build_specs,
    default_config_path="strategies/ma_crossover/config.yaml",
)