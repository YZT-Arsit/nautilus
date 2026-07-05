"""Ghost Trader short strategy package.

Exposes the strategy's public symbols, most importantly ``PLUGIN`` (registered
in ``strategy_framework/registry.py``). Split across ``config`` (parameters),
``engine`` (pure decision maths — a simulated EMA/RSI/Donchian short that only
sends real orders after a simulated loss), ``strategy`` (snapshot adapter), and
``plugin`` (feature specs + registry wiring).
"""
from strategies.ghost_trader_short.config import GhostTraderShortConfig
from strategies.ghost_trader_short.engine import (
    BUY,
    HOLD,
    SELL,
    GhostTraderShortEngine,
)
from strategies.ghost_trader_short.plugin import PLUGIN, build_specs
from strategies.ghost_trader_short.strategy import GhostTraderShortStrategy

__all__ = [
    "BUY",
    "HOLD",
    "PLUGIN",
    "SELL",
    "GhostTraderShortConfig",
    "GhostTraderShortEngine",
    "GhostTraderShortStrategy",
    "build_specs",
]
