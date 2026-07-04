"""Open/Close Histogram short strategy package.

Exposes the strategy's public symbols, most importantly ``PLUGIN`` (registered
in ``strategy_framework/registry.py``). Split across ``config`` (parameters),
``engine`` (pure decision maths — EMA(Close)-EMA(Open) histogram, zero-cross trend
with ATR-offset entry/exit triggers), ``strategy`` (snapshot adapter), and
``plugin`` (feature specs + registry wiring).
"""
from strategies.open_close_histogram_short.config import OpenCloseHistogramShortConfig
from strategies.open_close_histogram_short.engine import (
    BUY,
    HOLD,
    SELL,
    OpenCloseHistogramShortEngine,
)
from strategies.open_close_histogram_short.plugin import PLUGIN, build_specs
from strategies.open_close_histogram_short.strategy import OpenCloseHistogramShortStrategy

__all__ = [
    "BUY",
    "HOLD",
    "PLUGIN",
    "SELL",
    "OpenCloseHistogramShortConfig",
    "OpenCloseHistogramShortEngine",
    "OpenCloseHistogramShortStrategy",
    "build_specs",
]
