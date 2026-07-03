"""TrendScore short strategy package.

Exposes the strategy's public symbols, most importantly ``PLUGIN`` (registered
in ``strategy_framework/registry.py``). Split across ``config`` (parameters),
``engine`` (pure decision maths — close-vs-prior-closes scoring, MA of price and
score, ATR protective/trailing/break-even stop stack), ``strategy`` (snapshot
adapter), and ``plugin`` (feature specs + registry wiring).
"""
from strategies.trendscore_short.config import TrendScoreShortConfig
from strategies.trendscore_short.engine import (
    BUY,
    HOLD,
    SELL,
    TrendScoreShortEngine,
)
from strategies.trendscore_short.plugin import PLUGIN, build_specs
from strategies.trendscore_short.strategy import TrendScoreShortStrategy

__all__ = [
    "BUY",
    "HOLD",
    "PLUGIN",
    "SELL",
    "TrendScoreShortConfig",
    "TrendScoreShortEngine",
    "TrendScoreShortStrategy",
    "build_specs",
]
