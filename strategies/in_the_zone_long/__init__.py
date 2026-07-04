"""In The Zone long strategy package.

Exposes the strategy's public symbols, most importantly ``PLUGIN`` (registered
in ``strategy_framework/registry.py``). Split across ``config`` (parameters),
``engine`` (pure decision maths — a 4-bar box breakout long with ATR protective,
break-even, and profit-target exits), ``strategy`` (snapshot adapter), and
``plugin`` (feature specs + registry wiring).
"""
from strategies.in_the_zone_long.config import InTheZoneLongConfig
from strategies.in_the_zone_long.engine import (
    BUY,
    HOLD,
    SELL,
    InTheZoneLongEngine,
)
from strategies.in_the_zone_long.plugin import PLUGIN, build_specs
from strategies.in_the_zone_long.strategy import InTheZoneLongStrategy

__all__ = [
    "BUY",
    "HOLD",
    "PLUGIN",
    "SELL",
    "InTheZoneLongConfig",
    "InTheZoneLongEngine",
    "InTheZoneLongStrategy",
    "build_specs",
]
