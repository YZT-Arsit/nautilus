"""Average-Channel Range-Leader short strategy package.

Exposes the strategy's public symbols, most importantly ``PLUGIN`` (registered
in ``strategy_framework/registry.py``). Split across ``config`` (parameters),
``engine`` (pure decision maths — displaced low/median MA channel + range-leader
bar; short a prior range-leader closing below the low MA, cover on a mid- then
outer-channel stop), ``strategy`` (snapshot adapter), and ``plugin`` (feature specs
+ registry wiring).
"""
from strategies.avg_channel_range_leader_short.config import AvgChannelRangeLeaderShortConfig
from strategies.avg_channel_range_leader_short.engine import (
    BUY,
    HOLD,
    SELL,
    AvgChannelRangeLeaderShortEngine,
)
from strategies.avg_channel_range_leader_short.plugin import PLUGIN, build_specs
from strategies.avg_channel_range_leader_short.strategy import AvgChannelRangeLeaderShortStrategy

__all__ = [
    "BUY",
    "HOLD",
    "PLUGIN",
    "SELL",
    "AvgChannelRangeLeaderShortConfig",
    "AvgChannelRangeLeaderShortEngine",
    "AvgChannelRangeLeaderShortStrategy",
    "build_specs",
]
