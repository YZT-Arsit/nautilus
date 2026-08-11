"""Plugin wiring for the continuous event-time MA strategy."""
from __future__ import annotations

from feature_engine.api import FeatureSpec, trade_price_mean_spec
from strategy_framework.plugin import StrategyPlugin
from strategies.continuous_tick_ma.config import ContinuousTickMaConfig
from strategies.continuous_tick_ma.strategy import ContinuousTickMaStrategy


def build_specs(config: ContinuousTickMaConfig) -> list[FeatureSpec]:
    return [
        trade_price_mean_spec(config.fast_name, window=config.fast_minutes),
        trade_price_mean_spec(config.slow_name, window=config.slow_minutes),
    ]


PLUGIN = StrategyPlugin(
    name="continuous_tick_ma",
    config_cls=ContinuousTickMaConfig,
    strategy_cls=ContinuousTickMaStrategy,
    build_specs=build_specs,
    default_config_path="strategies/continuous_tick_ma/config.yaml",
)
