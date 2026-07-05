"""Escalator short strategy package.

Exposes the strategy's public symbols, most importantly ``PLUGIN`` (registered
in ``strategy_framework/registry.py``). Split across ``config`` (parameters),
``engine`` (pure decision maths — dual-MA regime + two-bar close-position pattern;
short a low-channel break, cover on a recent-high stop or a risk-multiple profit
target), ``strategy`` (snapshot adapter), and ``plugin`` (feature specs + registry
wiring).
"""
from strategies.escalator_short.config import EscalatorShortConfig
from strategies.escalator_short.engine import (
    BUY,
    HOLD,
    SELL,
    EscalatorShortEngine,
)
from strategies.escalator_short.plugin import PLUGIN, build_specs
from strategies.escalator_short.strategy import EscalatorShortStrategy

__all__ = [
    "BUY",
    "HOLD",
    "PLUGIN",
    "SELL",
    "EscalatorShortConfig",
    "EscalatorShortEngine",
    "EscalatorShortStrategy",
    "build_specs",
]
