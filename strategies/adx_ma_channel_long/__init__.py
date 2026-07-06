"""ADX + MA-channel long strategy package.

Exposes the strategy's public symbols, most importantly ``PLUGIN`` (registered in
``strategy_framework/registry.py``). Split across ``config`` (parameters),
``engine`` (pure decision maths — Wilder ADX + EMA high/low channel; long when a
rising ADX and an above-high-EMA close set up a channel-width breakout target, sell
on a break back below the prior high EMA), ``strategy`` (snapshot adapter), and
``plugin`` (feature specs + registry wiring).
"""
from strategies.adx_ma_channel_long.config import AdxMaChannelLongConfig
from strategies.adx_ma_channel_long.engine import (
    BUY,
    HOLD,
    SELL,
    AdxMaChannelLongEngine,
)
from strategies.adx_ma_channel_long.plugin import PLUGIN, build_specs
from strategies.adx_ma_channel_long.strategy import AdxMaChannelLongStrategy

__all__ = [
    "BUY",
    "HOLD",
    "PLUGIN",
    "SELL",
    "AdxMaChannelLongConfig",
    "AdxMaChannelLongEngine",
    "AdxMaChannelLongStrategy",
    "build_specs",
]
