"""MA Support/Resistance short strategy package.

Exposes the strategy's public symbols, most importantly ``PLUGIN`` (registered
in ``strategy_framework/registry.py``). Split across ``config`` (parameters),
``engine`` (pure decision maths — a variable support/resistance framework from
price vs a moving average, with dual ATR stops), ``strategy`` (snapshot adapter),
and ``plugin`` (feature specs + registry wiring).
"""
from strategies.ma_sup_res_short.config import MaSupResShortConfig
from strategies.ma_sup_res_short.engine import (
    BUY,
    HOLD,
    SELL,
    MaSupResShortEngine,
)
from strategies.ma_sup_res_short.plugin import PLUGIN, build_specs
from strategies.ma_sup_res_short.strategy import MaSupResShortStrategy

__all__ = [
    "BUY",
    "HOLD",
    "PLUGIN",
    "SELL",
    "MaSupResShortConfig",
    "MaSupResShortEngine",
    "MaSupResShortStrategy",
    "build_specs",
]
