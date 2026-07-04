"""Swinger long strategy package.

Exposes the strategy's public symbols, most importantly ``PLUGIN`` (registered
in ``strategy_framework/registry.py``). Split across ``config`` (parameters),
``engine`` (pure decision maths — trend-MA filter, price-oscillator momentum
entry, N-bar-low momentum-reversal exit), ``strategy`` (snapshot adapter), and
``plugin`` (feature specs + registry wiring).
"""
from strategies.swinger_long.config import SwingerLongConfig
from strategies.swinger_long.engine import (
    BUY,
    HOLD,
    SELL,
    SwingerLongEngine,
)
from strategies.swinger_long.plugin import PLUGIN, build_specs
from strategies.swinger_long.strategy import SwingerLongStrategy

__all__ = [
    "BUY",
    "HOLD",
    "PLUGIN",
    "SELL",
    "SwingerLongConfig",
    "SwingerLongEngine",
    "SwingerLongStrategy",
    "build_specs",
]
