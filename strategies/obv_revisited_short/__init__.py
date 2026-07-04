"""OBV Revisited short strategy package.

Exposes the strategy's public symbols, most importantly ``PLUGIN`` (registered
in ``strategy_framework/registry.py``). Split across ``config`` (parameters),
``engine`` (pure decision maths — volatility-weighted OBV crossed against its MA,
low-trigger short entry, up-cross exit), ``strategy`` (snapshot adapter), and
``plugin`` (feature specs + registry wiring).
"""
from strategies.obv_revisited_short.config import ObvRevisitedShortConfig
from strategies.obv_revisited_short.engine import (
    BUY,
    HOLD,
    SELL,
    ObvRevisitedShortEngine,
)
from strategies.obv_revisited_short.plugin import PLUGIN, build_specs
from strategies.obv_revisited_short.strategy import ObvRevisitedShortStrategy

__all__ = [
    "BUY",
    "HOLD",
    "PLUGIN",
    "SELL",
    "ObvRevisitedShortConfig",
    "ObvRevisitedShortEngine",
    "ObvRevisitedShortStrategy",
    "build_specs",
]
