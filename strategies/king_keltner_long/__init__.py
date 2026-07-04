"""King Keltner long strategy package.

Exposes the strategy's public symbols, most importantly ``PLUGIN`` (registered
in ``strategy_framework/registry.py``). Split across ``config`` (parameters),
``engine`` (pure decision maths — a typical-price MA with an upper ATR band, long
on an upward-turning MA breaking the band, sell on a break back below the MA),
``strategy`` (snapshot adapter), and ``plugin`` (feature specs + registry wiring).
"""
from strategies.king_keltner_long.config import KingKeltnerLongConfig
from strategies.king_keltner_long.engine import (
    BUY,
    HOLD,
    SELL,
    KingKeltnerLongEngine,
)
from strategies.king_keltner_long.plugin import PLUGIN, build_specs
from strategies.king_keltner_long.strategy import KingKeltnerLongStrategy

__all__ = [
    "BUY",
    "HOLD",
    "PLUGIN",
    "SELL",
    "KingKeltnerLongConfig",
    "KingKeltnerLongEngine",
    "KingKeltnerLongStrategy",
    "build_specs",
]
