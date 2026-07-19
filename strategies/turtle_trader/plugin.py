"""Turtle trading system plugin wiring (feature specs + registry plugin).

The framework-facing seam: it owns the ``feature_engine`` / ``strategy_framework``
imports (``FeatureSpec``, ``rolling_mean_spec``, ``StrategyPlugin``) so the
decision engine stays framework-free. Imports **no** ``nautilus_trader``.
"""
from __future__ import annotations

from feature_engine.api import FeatureSpec, rolling_mean_spec
from strategy_framework.plugin import StrategyPlugin

from strategies.turtle_trader.config import TurtleTraderConfig
from strategies.turtle_trader.execution_adapter import TurtleTraderExecutionAdapter
from strategies.turtle_trader.strategy import (
    _CLOSE,
    _HIGH,
    _LOW,
    _OPEN,
)


def build_specs(config: TurtleTraderConfig) -> list[FeatureSpec]:
    """Passthrough OHLC specs (open/high/low/close) consumed by ``on_snapshot``."""
    passthrough = {"input_type": "bar", "window": 1}
    return [
        rolling_mean_spec(_OPEN, input_field="open", **passthrough),
        rolling_mean_spec(_HIGH, input_field="high", **passthrough),
        rolling_mean_spec(_LOW, input_field="low", **passthrough),
        rolling_mean_spec(_CLOSE, input_field="close", **passthrough),
    ]


PLUGIN = StrategyPlugin(
    name="turtle_trader",
    config_cls=TurtleTraderConfig,
    strategy_cls=TurtleTraderExecutionAdapter,
    build_specs=build_specs,
    default_config_path="strategies/turtle_trader/config.yaml",
)
