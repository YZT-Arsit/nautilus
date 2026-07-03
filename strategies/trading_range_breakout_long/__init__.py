"""Trading Range Breakout long strategy package.

Long-side mirror of ``strategies/trading_range_breakout_short``. Exposes the
strategy's public symbols, most importantly ``PLUGIN`` (registered in
``strategy_framework/registry.py``). Split across ``config`` (parameters),
``engine`` (pure decision maths — quiet-range gap-sum filter, upside breakout
entry, initial + ATR-trailing + reversal exits), ``strategy`` (snapshot adapter),
and ``plugin`` (feature specs + registry wiring).
"""
from strategies.trading_range_breakout_long.config import TradingRangeBreakoutLongConfig
from strategies.trading_range_breakout_long.engine import (
    BUY,
    HOLD,
    SELL,
    TradingRangeBreakoutLongEngine,
)
from strategies.trading_range_breakout_long.plugin import PLUGIN, build_specs
from strategies.trading_range_breakout_long.strategy import TradingRangeBreakoutLongStrategy

__all__ = [
    "BUY",
    "HOLD",
    "PLUGIN",
    "SELL",
    "TradingRangeBreakoutLongConfig",
    "TradingRangeBreakoutLongEngine",
    "TradingRangeBreakoutLongStrategy",
    "build_specs",
]
