"""VWM short strategy package.

Exposes the strategy's public symbols, most importantly ``PLUGIN`` (registered
in ``strategy_framework/registry.py``).
"""
from strategies.vwm_short.strategy import (
    PLUGIN,
    VwmShortConfig,
    VwmShortStrategy,
    build_specs,
)

__all__ = [
    "PLUGIN",
    "VwmShortConfig",
    "VwmShortStrategy",
    "build_specs",
]
