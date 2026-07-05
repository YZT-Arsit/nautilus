"""Four-MA Crossover short strategy package.

Exposes the strategy's public symbols, most importantly ``PLUGIN`` (registered
in ``strategy_framework/registry.py``). Split across ``config`` (parameters),
``engine`` (pure decision maths — two SMA pairs; short on a double-bearish
arrangement + lower low, cover when the exit pair flips bullish), ``strategy``
(snapshot adapter), and ``plugin`` (feature specs + registry wiring).
"""
from strategies.four_ma_crossover_short.config import FourMaCrossoverShortConfig
from strategies.four_ma_crossover_short.engine import (
    BUY,
    HOLD,
    SELL,
    FourMaCrossoverShortEngine,
)
from strategies.four_ma_crossover_short.plugin import PLUGIN, build_specs
from strategies.four_ma_crossover_short.strategy import FourMaCrossoverShortStrategy

__all__ = [
    "BUY",
    "HOLD",
    "PLUGIN",
    "SELL",
    "FourMaCrossoverShortConfig",
    "FourMaCrossoverShortEngine",
    "FourMaCrossoverShortStrategy",
    "build_specs",
]
