"""First-PullBack long plugin wiring (feature specs + registry plugin).

The framework-facing seam: owns the ``feature_engine`` / ``strategy_framework``
imports so the decision engine stays framework-free. Imports **no**
``nautilus_trader``.
"""
from __future__ import annotations

from feature_engine.api import FeatureSpec, rolling_mean_spec
from strategy_framework.plugin import StrategyPlugin

from strategies.first_pullback_long.execution_adapter import FirstPullbackLongExecutionAdapter

from strategies.first_pullback_long.config import FirstPullbackLongConfig
from strategies.first_pullback_long.strategy import (
    _CLOSE,
    _HIGH,
    _LOW,
    _OPEN,
    _VOLUME,
    FirstPullbackLongStrategy,
)


def build_specs(config: FirstPullbackLongConfig) -> list[FeatureSpec]:
    """Passthrough OHLCV specs consumed by ``on_snapshot``."""
    passthrough = {"input_type": "bar", "window": 1}
    return [
        rolling_mean_spec(_OPEN, input_field="open", **passthrough),
        rolling_mean_spec(_HIGH, input_field="high", **passthrough),
        rolling_mean_spec(_LOW, input_field="low", **passthrough),
        rolling_mean_spec(_CLOSE, input_field="close", **passthrough),
        rolling_mean_spec(_VOLUME, input_field="volume", **passthrough),
    ]


PLUGIN = StrategyPlugin(
    name="first_pullback_long",
    config_cls=FirstPullbackLongConfig,
    strategy_cls=FirstPullbackLongExecutionAdapter,
    build_specs=build_specs,
    default_config_path="strategies/first_pullback_long/config.yaml",
)
