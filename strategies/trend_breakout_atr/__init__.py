"""Trend-breakout + ATR strategy package.

Exposes the strategy's public symbols, most importantly ``PLUGIN`` (registered
in ``strategy_framework/registry.py``).
"""
from strategies.trend_breakout_atr.strategy import (
    PLUGIN,
    TrendBreakoutAtrConfig,
    TrendBreakoutAtrEngine,
    TrendBreakoutAtrStrategy,
    build_specs,
)

__all__ = [
    "PLUGIN",
    "TrendBreakoutAtrConfig",
    "TrendBreakoutAtrEngine",
    "TrendBreakoutAtrStrategy",
    "build_specs",
]
