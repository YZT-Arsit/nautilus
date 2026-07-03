"""Turtle trading system strategy package.

Exposes the strategy's public symbols, most importantly ``PLUGIN`` (registered
in ``strategy_framework/registry.py``). The implementation is split across
``config`` (parameters), ``engine`` (pure decision maths — Donchian breakout,
failsafe, last-profitable-trade filter, N-based sizing, pyramiding, 2N stop,
trailing exit), ``strategy`` (snapshot adapter), and ``plugin`` (feature specs +
registry wiring).
"""
from strategies.turtle_trader.config import TurtleTraderConfig
from strategies.turtle_trader.engine import (
    BUY,
    EXIT,
    HOLD,
    SELL,
    TurtleTraderEngine,
)
from strategies.turtle_trader.plugin import PLUGIN, build_specs
from strategies.turtle_trader.strategy import TurtleTraderStrategy

__all__ = [
    "BUY",
    "EXIT",
    "HOLD",
    "PLUGIN",
    "SELL",
    "TurtleTraderConfig",
    "TurtleTraderEngine",
    "TurtleTraderStrategy",
    "build_specs",
]
