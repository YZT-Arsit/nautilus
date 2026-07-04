"""Spread Channel Breakout long strategy package.

Exposes the strategy's public symbols, most importantly ``PLUGIN`` (registered
in ``strategy_framework/registry.py``). Split across ``config`` (parameters),
``engine`` (pure decision maths — spread-bar channel breakout with reverse & stop
exits), ``strategy`` (snapshot adapter), and ``plugin`` (feature specs + registry
wiring).
"""
from strategies.spread_channel_breakout_long.config import SpreadChannelBreakoutLongConfig
from strategies.spread_channel_breakout_long.engine import (
    BUY,
    HOLD,
    SELL,
    SpreadChannelBreakoutLongEngine,
)
from strategies.spread_channel_breakout_long.plugin import PLUGIN, build_specs
from strategies.spread_channel_breakout_long.strategy import SpreadChannelBreakoutLongStrategy

__all__ = [
    "BUY",
    "HOLD",
    "PLUGIN",
    "SELL",
    "SpreadChannelBreakoutLongConfig",
    "SpreadChannelBreakoutLongEngine",
    "SpreadChannelBreakoutLongStrategy",
    "build_specs",
]
