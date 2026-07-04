"""Keltner Channel short strategy package.

Exposes the strategy's public symbols, most importantly ``PLUGIN`` (registered
in ``strategy_framework/registry.py``). Split across ``config`` (parameters),
``engine`` (pure decision maths — a Keltner channel with an armed trigger short
entry and a mid-cross / N-bar-high exit), ``strategy`` (snapshot adapter), and
``plugin`` (feature specs + registry wiring).
"""
from strategies.keltner_channel_short.config import KeltnerChannelShortConfig
from strategies.keltner_channel_short.engine import (
    BUY,
    HOLD,
    SELL,
    KeltnerChannelShortEngine,
)
from strategies.keltner_channel_short.plugin import PLUGIN, build_specs
from strategies.keltner_channel_short.strategy import KeltnerChannelShortStrategy

__all__ = [
    "BUY",
    "HOLD",
    "PLUGIN",
    "SELL",
    "KeltnerChannelShortConfig",
    "KeltnerChannelShortEngine",
    "KeltnerChannelShortStrategy",
    "build_specs",
]
