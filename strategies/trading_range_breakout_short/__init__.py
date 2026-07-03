"""Trading Range Breakout short strategy package.

Exposes the strategy's public symbols, most importantly ``PLUGIN`` (registered
in ``strategy_framework/registry.py``). Split across ``config`` (parameters),
``engine`` (pure decision maths — quiet-range gap-sum filter, downside breakout
entry, initial + ATR-trailing + reversal exits), ``strategy`` (snapshot adapter),
and ``plugin`` (feature specs + registry wiring).
"""
from strategies.trading_range_breakout_short.config import TradingRangeBreakoutShortConfig
from strategies.trading_range_breakout_short.engine import (
    BUY,
    HOLD,
    SELL,
    TradingRangeBreakoutShortEngine,
)
from strategies.trading_range_breakout_short.plugin import PLUGIN, build_specs
from strategies.trading_range_breakout_short.strategy import TradingRangeBreakoutShortStrategy

__all__ = [
    "BUY",
    "HOLD",
    "PLUGIN",
    "SELL",
    "TradingRangeBreakoutShortConfig",
    "TradingRangeBreakoutShortEngine",
    "TradingRangeBreakoutShortStrategy",
    "build_specs",
]
