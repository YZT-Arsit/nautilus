"""King Keltner short strategy package.

Exposes the strategy's public symbols, most importantly ``PLUGIN`` (registered
in ``strategy_framework/registry.py``). Split across ``config`` (parameters),
``engine`` (pure decision maths — a typical-price MA with a lower ATR band,
short on a downward-turning MA breaking the band, cover on a break back above the
MA), ``strategy`` (snapshot adapter), and ``plugin`` (feature specs + registry
wiring).
"""
from strategies.king_keltner_short.config import KingKeltnerShortConfig
from strategies.king_keltner_short.engine import (
    BUY,
    HOLD,
    SELL,
    KingKeltnerShortEngine,
)
from strategies.king_keltner_short.plugin import PLUGIN, build_specs
from strategies.king_keltner_short.strategy import KingKeltnerShortStrategy

__all__ = [
    "BUY",
    "HOLD",
    "PLUGIN",
    "SELL",
    "KingKeltnerShortConfig",
    "KingKeltnerShortEngine",
    "KingKeltnerShortStrategy",
    "build_specs",
]
