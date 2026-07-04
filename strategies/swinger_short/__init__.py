"""Swinger short strategy package.

Exposes the strategy's public symbols, most importantly ``PLUGIN`` (registered
in ``strategy_framework/registry.py``). Split across ``config`` (parameters),
``engine`` (pure decision maths — trend-MA filter, price-oscillator momentum
entry, N-bar-high momentum-reversal exit), ``strategy`` (snapshot adapter), and
``plugin`` (feature specs + registry wiring).
"""
from strategies.swinger_short.config import SwingerShortConfig
from strategies.swinger_short.engine import (
    BUY,
    HOLD,
    SELL,
    SwingerShortEngine,
)
from strategies.swinger_short.plugin import PLUGIN, build_specs
from strategies.swinger_short.strategy import SwingerShortStrategy

__all__ = [
    "BUY",
    "HOLD",
    "PLUGIN",
    "SELL",
    "SwingerShortConfig",
    "SwingerShortEngine",
    "SwingerShortStrategy",
    "build_specs",
]
