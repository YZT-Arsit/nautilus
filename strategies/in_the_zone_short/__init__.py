"""In The Zone short strategy package.

Exposes the strategy's public symbols, most importantly ``PLUGIN`` (registered
in ``strategy_framework/registry.py``). Split across ``config`` (parameters),
``engine`` (pure decision maths — a 4-bar box breakout short with ATR protective,
break-even, and profit-target exits), ``strategy`` (snapshot adapter), and
``plugin`` (feature specs + registry wiring).
"""
from strategies.in_the_zone_short.config import InTheZoneShortConfig
from strategies.in_the_zone_short.engine import (
    BUY,
    HOLD,
    SELL,
    InTheZoneShortEngine,
)
from strategies.in_the_zone_short.plugin import PLUGIN, build_specs
from strategies.in_the_zone_short.strategy import InTheZoneShortStrategy

__all__ = [
    "BUY",
    "HOLD",
    "PLUGIN",
    "SELL",
    "InTheZoneShortConfig",
    "InTheZoneShortEngine",
    "InTheZoneShortStrategy",
    "build_specs",
]
