"""Displaced-Bollinger long strategy package.

Exposes the strategy's public symbols, most importantly ``PLUGIN`` (registered
in ``strategy_framework/registry.py``). Split across ``config`` (parameters),
``engine`` (pure decision maths — Bollinger channel with a back-displaced mid-line
and current-width bands; long an upper-band break, sell a lower-band break),
``strategy`` (snapshot adapter), and ``plugin`` (feature specs + registry wiring).
"""
from strategies.displaced_boll_long.config import DisplacedBollLongConfig
from strategies.displaced_boll_long.engine import (
    BUY,
    HOLD,
    SELL,
    DisplacedBollLongEngine,
)
from strategies.displaced_boll_long.plugin import PLUGIN, build_specs
from strategies.displaced_boll_long.strategy import DisplacedBollLongStrategy

__all__ = [
    "BUY",
    "HOLD",
    "PLUGIN",
    "SELL",
    "DisplacedBollLongConfig",
    "DisplacedBollLongEngine",
    "DisplacedBollLongStrategy",
    "build_specs",
]
