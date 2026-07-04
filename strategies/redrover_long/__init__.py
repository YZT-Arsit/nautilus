"""RedRover long strategy package.

Exposes the strategy's public symbols, most importantly ``PLUGIN`` (registered
in ``strategy_framework/registry.py``). Split across ``config`` (parameters),
``engine`` (pure decision maths — weighted-price support/resistance breakout with
ATR profit target and reverse-break exit), ``strategy`` (snapshot adapter), and
``plugin`` (feature specs + registry wiring).
"""
from strategies.redrover_long.config import RedRoverLongConfig
from strategies.redrover_long.engine import (
    BUY,
    HOLD,
    SELL,
    RedRoverLongEngine,
)
from strategies.redrover_long.plugin import PLUGIN, build_specs
from strategies.redrover_long.strategy import RedRoverLongStrategy

__all__ = [
    "BUY",
    "HOLD",
    "PLUGIN",
    "SELL",
    "RedRoverLongConfig",
    "RedRoverLongEngine",
    "RedRoverLongStrategy",
    "build_specs",
]
