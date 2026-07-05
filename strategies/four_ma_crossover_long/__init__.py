"""Four-MA Crossover long strategy package.

Exposes the strategy's public symbols, most importantly ``PLUGIN`` (registered
in ``strategy_framework/registry.py``). Split across ``config`` (parameters),
``engine`` (pure decision maths — two SMA pairs; long on a double-bullish
arrangement + higher high, sell when the exit pair flips bearish), ``strategy``
(snapshot adapter), and ``plugin`` (feature specs + registry wiring).
"""
from strategies.four_ma_crossover_long.config import FourMaCrossoverLongConfig
from strategies.four_ma_crossover_long.engine import (
    BUY,
    HOLD,
    SELL,
    FourMaCrossoverLongEngine,
)
from strategies.four_ma_crossover_long.plugin import PLUGIN, build_specs
from strategies.four_ma_crossover_long.strategy import FourMaCrossoverLongStrategy

__all__ = [
    "BUY",
    "HOLD",
    "PLUGIN",
    "SELL",
    "FourMaCrossoverLongConfig",
    "FourMaCrossoverLongEngine",
    "FourMaCrossoverLongStrategy",
    "build_specs",
]
