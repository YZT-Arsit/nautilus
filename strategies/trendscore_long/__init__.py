"""TrendScore long strategy package.

Long-side mirror of ``strategies/trendscore_short``. Exposes the strategy's
public symbols, most importantly ``PLUGIN`` (registered in
``strategy_framework/registry.py``). Split across ``config`` (parameters),
``engine`` (pure decision maths), ``strategy`` (snapshot adapter), and ``plugin``
(feature specs + registry wiring).
"""
from strategies.trendscore_long.config import TrendScoreLongConfig
from strategies.trendscore_long.engine import (
    BUY,
    HOLD,
    SELL,
    TrendScoreLongEngine,
)
from strategies.trendscore_long.plugin import PLUGIN, build_specs
from strategies.trendscore_long.strategy import TrendScoreLongStrategy

__all__ = [
    "BUY",
    "HOLD",
    "PLUGIN",
    "SELL",
    "TrendScoreLongConfig",
    "TrendScoreLongEngine",
    "TrendScoreLongStrategy",
    "build_specs",
]
