"""Dynamic Breakout II short strategy package.

Exposes the strategy's public symbols, most importantly ``PLUGIN`` (registered
in ``strategy_framework/registry.py``). Split across ``config`` (parameters),
``engine`` (pure decision maths — volatility-adaptive Bollinger + Donchian
breakout; short a lower-band/lower-channel break, cover on an upper breakout or a
cross back above the adaptive mid-line), ``strategy`` (snapshot adapter), and
``plugin`` (feature specs + registry wiring).
"""
from strategies.dynamic_breakout_short.config import DynamicBreakoutShortConfig
from strategies.dynamic_breakout_short.engine import (
    BUY,
    HOLD,
    SELL,
    DynamicBreakoutShortEngine,
)
from strategies.dynamic_breakout_short.plugin import PLUGIN, build_specs
from strategies.dynamic_breakout_short.strategy import DynamicBreakoutShortStrategy

__all__ = [
    "BUY",
    "HOLD",
    "PLUGIN",
    "SELL",
    "DynamicBreakoutShortConfig",
    "DynamicBreakoutShortEngine",
    "DynamicBreakoutShortStrategy",
    "build_specs",
]
