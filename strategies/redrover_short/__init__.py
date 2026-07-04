"""RedRover short strategy package.

Exposes the strategy's public symbols, most importantly ``PLUGIN`` (registered
in ``strategy_framework/registry.py``). Split across ``config`` (parameters),
``engine`` (pure decision maths — weighted-price support/resistance breakout with
ATR profit target and reverse-break exit), ``strategy`` (snapshot adapter), and
``plugin`` (feature specs + registry wiring).
"""
from strategies.redrover_short.config import RedRoverShortConfig
from strategies.redrover_short.engine import (
    BUY,
    HOLD,
    SELL,
    RedRoverShortEngine,
)
from strategies.redrover_short.plugin import PLUGIN, build_specs
from strategies.redrover_short.strategy import RedRoverShortStrategy

__all__ = [
    "BUY",
    "HOLD",
    "PLUGIN",
    "SELL",
    "RedRoverShortConfig",
    "RedRoverShortEngine",
    "RedRoverShortStrategy",
    "build_specs",
]
