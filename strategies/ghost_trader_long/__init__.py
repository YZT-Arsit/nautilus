"""Ghost Trader long strategy package.

Exposes the strategy's public symbols, most importantly ``PLUGIN`` (registered
in ``strategy_framework/registry.py``). Split across ``config`` (parameters),
``engine`` (pure decision maths — a simulated EMA/RSI/Donchian long that only
sends real orders after a simulated loss), ``strategy`` (snapshot adapter), and
``plugin`` (feature specs + registry wiring).
"""
from strategies.ghost_trader_long.config import GhostTraderLongConfig
from strategies.ghost_trader_long.engine import (
    BUY,
    HOLD,
    SELL,
    GhostTraderLongEngine,
)
from strategies.ghost_trader_long.plugin import PLUGIN, build_specs
from strategies.ghost_trader_long.strategy import GhostTraderLongStrategy

__all__ = [
    "BUY",
    "HOLD",
    "PLUGIN",
    "SELL",
    "GhostTraderLongConfig",
    "GhostTraderLongEngine",
    "GhostTraderLongStrategy",
    "build_specs",
]
