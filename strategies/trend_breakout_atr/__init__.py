"""Trend-breakout + ATR strategy package.

Exposes the strategy's public symbols, most importantly ``PLUGIN`` (registered
in ``strategy_framework/registry.py``). The implementation is split across
``config`` (parameters), ``engine`` (pure decision maths), ``strategy`` (snapshot
adapter), and ``plugin`` (feature specs + registry wiring).
"""
from strategies.trend_breakout_atr.config import TrendBreakoutAtrConfig
from strategies.trend_breakout_atr.engine import (
    BUY,
    HOLD,
    SELL,
    TrendBreakoutAtrEngine,
)
from strategies.trend_breakout_atr.plugin import PLUGIN, build_specs
from strategies.trend_breakout_atr.strategy import TrendBreakoutAtrStrategy

__all__ = [
    "BUY",
    "HOLD",
    "PLUGIN",
    "SELL",
    "TrendBreakoutAtrConfig",
    "TrendBreakoutAtrEngine",
    "TrendBreakoutAtrStrategy",
    "build_specs",
]
