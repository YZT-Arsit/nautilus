"""ADX + MA-channel short strategy package.

Exposes the strategy's public symbols, most importantly ``PLUGIN`` (registered in
``strategy_framework/registry.py``). Split across ``config`` (parameters),
``engine`` (pure decision maths — Wilder ADX + EMA high/low channel; short when a
rising ADX and a sub-low-EMA close set up a channel-width breakout target, cover on
a break back above the prior low EMA), ``strategy`` (snapshot adapter), and
``plugin`` (feature specs + registry wiring).
"""
from strategies.adx_ma_channel_short.config import AdxMaChannelShortConfig
from strategies.adx_ma_channel_short.engine import (
    BUY,
    HOLD,
    SELL,
    AdxMaChannelShortEngine,
)
from strategies.adx_ma_channel_short.plugin import PLUGIN, build_specs
from strategies.adx_ma_channel_short.strategy import AdxMaChannelShortStrategy

__all__ = [
    "BUY",
    "HOLD",
    "PLUGIN",
    "SELL",
    "AdxMaChannelShortConfig",
    "AdxMaChannelShortEngine",
    "AdxMaChannelShortStrategy",
    "build_specs",
]
