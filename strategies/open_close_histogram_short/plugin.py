"""Open/Close Histogram short plugin wiring (feature specs + registry plugin).

The framework-facing seam: owns the ``feature_engine`` / ``strategy_framework``
imports so the decision engine stays framework-free. Imports **no**
``nautilus_trader``.
"""
from __future__ import annotations

from feature_engine.api import FeatureSpec, rolling_mean_spec
from strategy_framework.plugin import StrategyPlugin

from strategies.open_close_histogram_short.execution_adapter import OpenCloseHistogramShortExecutionAdapter

from strategies.open_close_histogram_short.config import OpenCloseHistogramShortConfig
from strategies.open_close_histogram_short.strategy import (
    _CLOSE,
    _HIGH,
    _LOW,
    _OPEN,
    _VOLUME,
    OpenCloseHistogramShortStrategy,
)


def build_specs(config: OpenCloseHistogramShortConfig) -> list[FeatureSpec]:
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
    name="open_close_histogram_short",
    config_cls=OpenCloseHistogramShortConfig,
    strategy_cls=OpenCloseHistogramShortExecutionAdapter,
    build_specs=build_specs,
    default_config_path="strategies/open_close_histogram_short/config.yaml",
)
