"""Open/Close Histogram long strategy package.

Exposes the strategy's public symbols, most importantly ``PLUGIN`` (registered
in ``strategy_framework/registry.py``). Split across ``config`` (parameters),
``engine`` (pure decision maths — EMA(Close)-EMA(Open) histogram, zero-cross trend
with ATR-offset entry/exit triggers), ``strategy`` (snapshot adapter), and
``plugin`` (feature specs + registry wiring).
"""
from strategies.open_close_histogram_long.config import OpenCloseHistogramLongConfig
from strategies.open_close_histogram_long.engine import (
    BUY,
    HOLD,
    SELL,
    OpenCloseHistogramLongEngine,
)
from strategies.open_close_histogram_long.plugin import PLUGIN, build_specs
from strategies.open_close_histogram_long.strategy import OpenCloseHistogramLongStrategy

__all__ = [
    "BUY",
    "HOLD",
    "PLUGIN",
    "SELL",
    "OpenCloseHistogramLongConfig",
    "OpenCloseHistogramLongEngine",
    "OpenCloseHistogramLongStrategy",
    "build_specs",
]
