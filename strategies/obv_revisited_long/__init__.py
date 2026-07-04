"""OBV Revisited long strategy package.

Exposes the strategy's public symbols, most importantly ``PLUGIN`` (registered
in ``strategy_framework/registry.py``). Split across ``config`` (parameters),
``engine`` (pure decision maths — volatility-weighted OBV crossed against its MA,
high-trigger long entry, down-cross exit), ``strategy`` (snapshot adapter), and
``plugin`` (feature specs + registry wiring).
"""
from strategies.obv_revisited_long.config import ObvRevisitedLongConfig
from strategies.obv_revisited_long.engine import (
    BUY,
    HOLD,
    SELL,
    ObvRevisitedLongEngine,
)
from strategies.obv_revisited_long.plugin import PLUGIN, build_specs
from strategies.obv_revisited_long.strategy import ObvRevisitedLongStrategy

__all__ = [
    "BUY",
    "HOLD",
    "PLUGIN",
    "SELL",
    "ObvRevisitedLongConfig",
    "ObvRevisitedLongEngine",
    "ObvRevisitedLongStrategy",
    "build_specs",
]
