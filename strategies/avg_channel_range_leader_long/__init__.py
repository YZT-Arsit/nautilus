"""Average-Channel Range-Leader long strategy package.

Exposes the strategy's public symbols, most importantly ``PLUGIN`` (registered
in ``strategy_framework/registry.py``). Split across ``config`` (parameters),
``engine`` (pure decision maths — displaced high/median MA channel + range-leader
bar; long a prior range-leader closing above the high MA, sell on a mid- then
outer-channel stop), ``strategy`` (snapshot adapter), and ``plugin`` (feature specs
+ registry wiring).
"""
from strategies.avg_channel_range_leader_long.config import AvgChannelRangeLeaderLongConfig
from strategies.avg_channel_range_leader_long.engine import (
    BUY,
    HOLD,
    SELL,
    AvgChannelRangeLeaderLongEngine,
)
from strategies.avg_channel_range_leader_long.plugin import PLUGIN, build_specs
from strategies.avg_channel_range_leader_long.strategy import AvgChannelRangeLeaderLongStrategy

__all__ = [
    "BUY",
    "HOLD",
    "PLUGIN",
    "SELL",
    "AvgChannelRangeLeaderLongConfig",
    "AvgChannelRangeLeaderLongEngine",
    "AvgChannelRangeLeaderLongStrategy",
    "build_specs",
]
