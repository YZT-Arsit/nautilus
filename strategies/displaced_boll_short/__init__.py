"""Displaced-Bollinger short strategy package.

Exposes the strategy's public symbols, most importantly ``PLUGIN`` (registered
in ``strategy_framework/registry.py``). Split across ``config`` (parameters),
``engine`` (pure decision maths — Bollinger channel with a back-displaced mid-line
and current-width bands; short a lower-band break, cover an upper-band break),
``strategy`` (snapshot adapter), and ``plugin`` (feature specs + registry wiring).
"""
from strategies.displaced_boll_short.config import DisplacedBollShortConfig
from strategies.displaced_boll_short.engine import (
    BUY,
    HOLD,
    SELL,
    DisplacedBollShortEngine,
)
from strategies.displaced_boll_short.plugin import PLUGIN, build_specs
from strategies.displaced_boll_short.strategy import DisplacedBollShortStrategy

__all__ = [
    "BUY",
    "HOLD",
    "PLUGIN",
    "SELL",
    "DisplacedBollShortConfig",
    "DisplacedBollShortEngine",
    "DisplacedBollShortStrategy",
    "build_specs",
]
