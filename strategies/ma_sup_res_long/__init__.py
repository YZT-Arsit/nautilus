"""MA Support/Resistance long strategy package.

Exposes the strategy's public symbols, most importantly ``PLUGIN`` (registered
in ``strategy_framework/registry.py``). Split across ``config`` (parameters),
``engine`` (pure decision maths — a variable support/resistance framework from
price vs a moving average with dual ATR stops, long side), ``strategy`` (snapshot
adapter), and ``plugin`` (feature specs + registry wiring).
"""
from strategies.ma_sup_res_long.config import MaSupResLongConfig
from strategies.ma_sup_res_long.engine import (
    BUY,
    HOLD,
    SELL,
    MaSupResLongEngine,
)
from strategies.ma_sup_res_long.plugin import PLUGIN, build_specs
from strategies.ma_sup_res_long.strategy import MaSupResLongStrategy

__all__ = [
    "BUY",
    "HOLD",
    "PLUGIN",
    "SELL",
    "MaSupResLongConfig",
    "MaSupResLongEngine",
    "MaSupResLongStrategy",
    "build_specs",
]
