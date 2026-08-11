"""Continuous event-time 5/10-minute trade-price crossover strategy."""

from strategies.continuous_tick_ma.config import ContinuousTickMaConfig
from strategies.continuous_tick_ma.plugin import PLUGIN, build_specs
from strategies.continuous_tick_ma.strategy import ContinuousTickMaStrategy

__all__ = [
    "ContinuousTickMaConfig",
    "ContinuousTickMaStrategy",
    "PLUGIN",
    "build_specs",
]
