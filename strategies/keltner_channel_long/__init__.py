"""Keltner Channel long strategy package.

Exposes the strategy's public symbols, most importantly ``PLUGIN`` (registered
in ``strategy_framework/registry.py``). Split across ``config`` (parameters),
``engine`` (pure decision maths — a Keltner channel with an armed trigger long
entry and a mid-cross / N-bar-low exit), ``strategy`` (snapshot adapter), and
``plugin`` (feature specs + registry wiring).
"""
from strategies.keltner_channel_long.config import KeltnerChannelLongConfig
from strategies.keltner_channel_long.engine import (
    BUY,
    HOLD,
    SELL,
    KeltnerChannelLongEngine,
)
from strategies.keltner_channel_long.plugin import PLUGIN, build_specs
from strategies.keltner_channel_long.strategy import KeltnerChannelLongStrategy

__all__ = [
    "BUY",
    "HOLD",
    "PLUGIN",
    "SELL",
    "KeltnerChannelLongConfig",
    "KeltnerChannelLongEngine",
    "KeltnerChannelLongStrategy",
    "build_specs",
]
