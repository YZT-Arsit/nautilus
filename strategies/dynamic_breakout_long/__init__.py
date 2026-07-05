"""Dynamic Breakout II long strategy package.

Exposes the strategy's public symbols, most importantly ``PLUGIN`` (registered
in ``strategy_framework/registry.py``). Split across ``config`` (parameters),
``engine`` (pure decision maths — volatility-adaptive Bollinger + Donchian
breakout; long an upper-band/upper-channel break, sell on a lower breakout or a
cross back below the adaptive mid-line), ``strategy`` (snapshot adapter), and
``plugin`` (feature specs + registry wiring).
"""
from strategies.dynamic_breakout_long.config import DynamicBreakoutLongConfig
from strategies.dynamic_breakout_long.engine import (
    BUY,
    HOLD,
    SELL,
    DynamicBreakoutLongEngine,
)
from strategies.dynamic_breakout_long.plugin import PLUGIN, build_specs
from strategies.dynamic_breakout_long.strategy import DynamicBreakoutLongStrategy

__all__ = [
    "BUY",
    "HOLD",
    "PLUGIN",
    "SELL",
    "DynamicBreakoutLongConfig",
    "DynamicBreakoutLongEngine",
    "DynamicBreakoutLongStrategy",
    "build_specs",
]
