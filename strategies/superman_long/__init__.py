"""Superman System long strategy package.

Exposes the strategy's public symbols, most importantly ``PLUGIN`` (registered
in ``strategy_framework/registry.py``). Split across ``config`` (parameters),
``engine`` (pure decision maths — market-strength index, momentum turn, channel
breakout entry, stop/profit-target/reverse exits), ``strategy`` (snapshot
adapter), and ``plugin`` (feature specs + registry wiring).
"""
from strategies.superman_long.config import SupermanLongConfig
from strategies.superman_long.engine import (
    BUY,
    HOLD,
    SELL,
    SupermanLongEngine,
)
from strategies.superman_long.plugin import PLUGIN, build_specs
from strategies.superman_long.strategy import SupermanLongStrategy

__all__ = [
    "BUY",
    "HOLD",
    "PLUGIN",
    "SELL",
    "SupermanLongConfig",
    "SupermanLongEngine",
    "SupermanLongStrategy",
    "build_specs",
]
