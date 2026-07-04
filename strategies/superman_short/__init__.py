"""Superman System short strategy package.

Exposes the strategy's public symbols, most importantly ``PLUGIN`` (registered
in ``strategy_framework/registry.py``). Split across ``config`` (parameters),
``engine`` (pure decision maths — market-strength index, momentum turn, channel
breakout entry, stop/profit-target/reverse exits), ``strategy`` (snapshot
adapter), and ``plugin`` (feature specs + registry wiring).
"""
from strategies.superman_short.config import SupermanShortConfig
from strategies.superman_short.engine import (
    BUY,
    HOLD,
    SELL,
    SupermanShortEngine,
)
from strategies.superman_short.plugin import PLUGIN, build_specs
from strategies.superman_short.strategy import SupermanShortStrategy

__all__ = [
    "BUY",
    "HOLD",
    "PLUGIN",
    "SELL",
    "SupermanShortConfig",
    "SupermanShortEngine",
    "SupermanShortStrategy",
    "build_specs",
]
