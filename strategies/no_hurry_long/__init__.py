"""No Hurry long strategy package.

Exposes the strategy's public symbols, most importantly ``PLUGIN`` (registered
in ``strategy_framework/registry.py``). Split across ``config`` (parameters),
``engine`` (pure decision maths — a time-shifted high/low channel breakout with
an ATR trailing stop, long side), ``strategy`` (snapshot adapter), and ``plugin``
(feature specs + registry wiring).
"""
from strategies.no_hurry_long.config import NoHurryLongConfig
from strategies.no_hurry_long.engine import (
    BUY,
    HOLD,
    SELL,
    NoHurryLongEngine,
)
from strategies.no_hurry_long.plugin import PLUGIN, build_specs
from strategies.no_hurry_long.strategy import NoHurryLongStrategy

__all__ = [
    "BUY",
    "HOLD",
    "PLUGIN",
    "SELL",
    "NoHurryLongConfig",
    "NoHurryLongEngine",
    "NoHurryLongStrategy",
    "build_specs",
]
