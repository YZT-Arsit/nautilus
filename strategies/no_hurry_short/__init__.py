"""No Hurry short strategy package.

Exposes the strategy's public symbols, most importantly ``PLUGIN`` (registered
in ``strategy_framework/registry.py``). Split across ``config`` (parameters),
``engine`` (pure decision maths — a time-shifted high/low channel breakout with
an ATR trailing stop), ``strategy`` (snapshot adapter), and ``plugin`` (feature
specs + registry wiring).
"""
from strategies.no_hurry_short.config import NoHurryShortConfig
from strategies.no_hurry_short.engine import (
    BUY,
    HOLD,
    SELL,
    NoHurryShortEngine,
)
from strategies.no_hurry_short.plugin import PLUGIN, build_specs
from strategies.no_hurry_short.strategy import NoHurryShortStrategy

__all__ = [
    "BUY",
    "HOLD",
    "PLUGIN",
    "SELL",
    "NoHurryShortConfig",
    "NoHurryShortEngine",
    "NoHurryShortStrategy",
    "build_specs",
]
