"""First-PullBack short strategy package.

Exposes the strategy's public symbols, most importantly ``PLUGIN`` (registered
in ``strategy_framework/registry.py``). Split across ``config`` (parameters),
``engine`` (pure decision maths — MACD-signal-line trend regime + Close/ATR
pullback channel; short a lower-band break, cover on trend-over / upper-band
breaks), ``strategy`` (snapshot adapter), and ``plugin`` (feature specs + registry
wiring).
"""
from strategies.first_pullback_short.config import FirstPullbackShortConfig
from strategies.first_pullback_short.engine import (
    BUY,
    HOLD,
    SELL,
    FirstPullbackShortEngine,
)
from strategies.first_pullback_short.plugin import PLUGIN, build_specs
from strategies.first_pullback_short.strategy import FirstPullbackShortStrategy

__all__ = [
    "BUY",
    "HOLD",
    "PLUGIN",
    "SELL",
    "FirstPullbackShortConfig",
    "FirstPullbackShortEngine",
    "FirstPullbackShortStrategy",
    "build_specs",
]
