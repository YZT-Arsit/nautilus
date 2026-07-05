"""Escalator long strategy package.

Exposes the strategy's public symbols, most importantly ``PLUGIN`` (registered
in ``strategy_framework/registry.py``). Split across ``config`` (parameters),
``engine`` (pure decision maths — dual-MA regime + two-bar close-position pattern;
long a high-channel break, sell on a recent-low stop or a risk-multiple profit
target), ``strategy`` (snapshot adapter), and ``plugin`` (feature specs + registry
wiring).
"""
from strategies.escalator_long.config import EscalatorLongConfig
from strategies.escalator_long.engine import (
    BUY,
    HOLD,
    SELL,
    EscalatorLongEngine,
)
from strategies.escalator_long.plugin import PLUGIN, build_specs
from strategies.escalator_long.strategy import EscalatorLongStrategy

__all__ = [
    "BUY",
    "HOLD",
    "PLUGIN",
    "SELL",
    "EscalatorLongConfig",
    "EscalatorLongEngine",
    "EscalatorLongStrategy",
    "build_specs",
]
