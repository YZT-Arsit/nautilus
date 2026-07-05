"""Dual-MA plugin wiring (feature specs + registry plugin).

The framework-facing seam: owns the ``feature_engine`` / ``strategy_framework``
imports so the decision engine stays framework-free. Imports **no**
``nautilus_trader``.
"""
from __future__ import annotations

from feature_engine.api import FeatureSpec, rolling_mean_spec
from strategy_framework.plugin import StrategyPlugin

from strategies.dual_ma.config import DualMaConfig
from strategies.dual_ma.strategy import _CLOSE, _OPEN, DualMaStrategy


def build_specs(config: DualMaConfig) -> list[FeatureSpec]:
    """Passthrough open/close specs consumed by ``on_snapshot``."""
    passthrough = {"input_type": "bar", "window": 1}
    return [
        rolling_mean_spec(_OPEN, input_field="open", **passthrough),
        rolling_mean_spec(_CLOSE, input_field="close", **passthrough),
    ]


PLUGIN = StrategyPlugin(
    name="dual_ma",
    config_cls=DualMaConfig,
    strategy_cls=DualMaStrategy,
    build_specs=build_specs,
    default_config_path="strategies/dual_ma/config.yaml",
)
