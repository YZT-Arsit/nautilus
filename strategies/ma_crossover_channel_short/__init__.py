"""MA-Crossover Channel-Breakout short strategy package.

Exposes the strategy's public symbols, most importantly ``PLUGIN`` (registered
in ``strategy_framework/registry.py`` as ``ma_crossover_channel_short``). Split
across ``config`` (parameters), ``engine`` (pure decision maths — fast/slow MA
crossover arming a channel breakout, with reversal exit, periodic-high trailing
stop, and post-stop re-entry, short side), ``strategy`` (snapshot adapter), and
``plugin`` (feature specs + registry wiring).
"""
from strategies.ma_crossover_channel_short.config import MaCrossoverChannelShortConfig
from strategies.ma_crossover_channel_short.engine import (
    BUY,
    HOLD,
    SELL,
    MaCrossoverChannelShortEngine,
)
from strategies.ma_crossover_channel_short.plugin import PLUGIN, build_specs
from strategies.ma_crossover_channel_short.strategy import MaCrossoverChannelShortStrategy

__all__ = [
    "BUY",
    "HOLD",
    "PLUGIN",
    "SELL",
    "MaCrossoverChannelShortConfig",
    "MaCrossoverChannelShortEngine",
    "MaCrossoverChannelShortStrategy",
    "build_specs",
]
