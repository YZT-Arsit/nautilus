"""Trend-breakout + ATR plugin wiring (feature specs + registry plugin).

This is the framework-facing seam: it owns the ``feature_engine`` /
``strategy_framework`` imports (``FeatureSpec``, ``rolling_mean_spec``,
``StrategyPlugin``) so the decision engine stays framework-free. Imports **no**
``nautilus_trader``. Behaviour - the produced specs and the registered plugin -
is unchanged from the original single-file module.
"""
from __future__ import annotations

from feature_engine.api import FeatureSpec, rolling_mean_spec
from strategy_framework.plugin import StrategyPlugin

from strategies.trend_breakout_atr.config import TrendBreakoutAtrConfig
from strategies.trend_breakout_atr.strategy import (
    _CLOSE,
    _HIGH,
    _LOW,
    TrendBreakoutAtrStrategy,
)


def build_specs(config: TrendBreakoutAtrConfig) -> list[FeatureSpec]:
    """Passthrough OHLC specs (close/high/low) consumed by ``on_snapshot``."""
    passthrough = {"input_type": "bar", "window": 1}
    return [
        rolling_mean_spec(_CLOSE, input_field="close", **passthrough),
        rolling_mean_spec(_HIGH, input_field="high", **passthrough),
        rolling_mean_spec(_LOW, input_field="low", **passthrough),
    ]


PLUGIN = StrategyPlugin(
    name="trend_breakout_atr",
    config_cls=TrendBreakoutAtrConfig,
    strategy_cls=TrendBreakoutAtrStrategy,
    build_specs=build_specs,
    default_config_path="configs/backtests/trend_breakout_atr_btcusdt_1m_3d.yaml",
)
