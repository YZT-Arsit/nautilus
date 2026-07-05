"""Dual-MA (stop-and-reverse) strategy package.

Exposes the strategy's public symbols, most importantly ``PLUGIN`` (registered
in ``strategy_framework/registry.py``). Split across ``config`` (parameters),
``engine`` (pure decision maths — fast/slow SMA regime; always in the market,
flipping long<->short on a crossover of the previous-bar MAs via sized reversing
orders), ``strategy`` (snapshot adapter, rich-plan ``PlannedSignal``), and
``plugin`` (feature specs + registry wiring).
"""
from strategies.dual_ma.config import DualMaConfig
from strategies.dual_ma.engine import BUY, HOLD, SELL, DualMaEngine
from strategies.dual_ma.plugin import PLUGIN, build_specs
from strategies.dual_ma.strategy import DualMaStrategy

__all__ = [
    "BUY",
    "HOLD",
    "PLUGIN",
    "SELL",
    "DualMaConfig",
    "DualMaEngine",
    "DualMaStrategy",
    "build_specs",
]
