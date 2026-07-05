"""First-PullBack long strategy package.

Exposes the strategy's public symbols, most importantly ``PLUGIN`` (registered
in ``strategy_framework/registry.py``). Split across ``config`` (parameters),
``engine`` (pure decision maths — MACD-signal-line trend regime + Close/ATR
pullback channel; long an upper-band break, sell on trend-over / lower-band
breaks), ``strategy`` (snapshot adapter), and ``plugin`` (feature specs + registry
wiring).
"""
from strategies.first_pullback_long.config import FirstPullbackLongConfig
from strategies.first_pullback_long.engine import (
    BUY,
    HOLD,
    SELL,
    FirstPullbackLongEngine,
)
from strategies.first_pullback_long.plugin import PLUGIN, build_specs
from strategies.first_pullback_long.strategy import FirstPullbackLongStrategy

__all__ = [
    "BUY",
    "HOLD",
    "PLUGIN",
    "SELL",
    "FirstPullbackLongConfig",
    "FirstPullbackLongEngine",
    "FirstPullbackLongStrategy",
    "build_specs",
]
