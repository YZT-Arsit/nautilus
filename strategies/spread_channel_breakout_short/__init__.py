"""Spread Channel Breakout short strategy package.

Exposes the strategy's public symbols, most importantly ``PLUGIN`` (registered
in ``strategy_framework/registry.py``). Split across ``config`` (parameters),
``engine`` (pure decision maths — spread-bar channel breakout with reverse & stop
covers), ``strategy`` (snapshot adapter), and ``plugin`` (feature specs + registry
wiring).
"""
from strategies.spread_channel_breakout_short.config import SpreadChannelBreakoutShortConfig
from strategies.spread_channel_breakout_short.engine import (
    BUY,
    HOLD,
    SELL,
    SpreadChannelBreakoutShortEngine,
)
from strategies.spread_channel_breakout_short.plugin import PLUGIN, build_specs
from strategies.spread_channel_breakout_short.strategy import SpreadChannelBreakoutShortStrategy

__all__ = [
    "BUY",
    "HOLD",
    "PLUGIN",
    "SELL",
    "SpreadChannelBreakoutShortConfig",
    "SpreadChannelBreakoutShortEngine",
    "SpreadChannelBreakoutShortStrategy",
    "build_specs",
]
