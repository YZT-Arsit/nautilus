"""VWM long strategy package.

Long-side mirror of ``strategies/vwm_short``. Exposes the strategy's public
symbols, most importantly ``PLUGIN`` (registered in
``strategy_framework/registry.py``).
"""
from strategies.vwm_long.strategy import (
    PLUGIN,
    VwmLongConfig,
    VwmLongStrategy,
    build_specs,
)

__all__ = [
    "PLUGIN",
    "VwmLongConfig",
    "VwmLongStrategy",
    "build_specs",
]
