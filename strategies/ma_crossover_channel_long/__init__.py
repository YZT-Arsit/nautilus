"""MA-Crossover Channel-Breakout long strategy package.

Exposes the strategy's public symbols, most importantly ``PLUGIN`` (registered
in ``strategy_framework/registry.py`` as ``ma_crossover_channel_long``). Split
across ``config`` (parameters), ``engine`` (pure decision maths — fast/slow MA
crossover arming a channel breakout, with reversal exit, periodic-low trailing
stop, and post-stop re-entry), ``strategy`` (snapshot adapter), and ``plugin``
(feature specs + registry wiring).
"""
from strategies.ma_crossover_channel_long.config import MaCrossoverChannelLongConfig
from strategies.ma_crossover_channel_long.engine import (
    BUY,
    HOLD,
    SELL,
    MaCrossoverChannelLongEngine,
)
from strategies.ma_crossover_channel_long.plugin import PLUGIN, build_specs
from strategies.ma_crossover_channel_long.strategy import MaCrossoverChannelLongStrategy

__all__ = [
    "BUY",
    "HOLD",
    "PLUGIN",
    "SELL",
    "MaCrossoverChannelLongConfig",
    "MaCrossoverChannelLongEngine",
    "MaCrossoverChannelLongStrategy",
    "build_specs",
]
