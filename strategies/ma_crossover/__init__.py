"""MA crossover strategy package.

Exposes the strategy's public symbols, most importantly ``PLUGIN`` (registered
in ``strategy_framework/registry.py``).
"""
from strategies.ma_crossover.strategy import (
    PLUGIN,
    MovingAverageCrossoverConfig,
    MovingAverageCrossoverStrategy,
    build_ma_crossover_specs,
    build_specs,
    crossover_signal,
)

__all__ = [
    "PLUGIN",
    "MovingAverageCrossoverConfig",
    "MovingAverageCrossoverStrategy",
    "build_specs",
    "build_ma_crossover_specs",
    "crossover_signal",
]
