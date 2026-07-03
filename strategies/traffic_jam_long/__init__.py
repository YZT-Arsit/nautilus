"""Traffic Jam long strategy package.

Long-side mirror of ``strategies/traffic_jam_short``. Exposes the strategy's
public symbols, most importantly ``PLUGIN`` (registered in
``strategy_framework/registry.py``). Split across ``config`` (parameters),
``engine`` (pure decision maths — Wilder DMI/ADX ranging filter, consecutive
down-close fade, ATR protective stop + time exit), ``strategy`` (snapshot
adapter), and ``plugin`` (feature specs + registry wiring).
"""
from strategies.traffic_jam_long.config import TrafficJamLongConfig
from strategies.traffic_jam_long.engine import (
    BUY,
    HOLD,
    SELL,
    TrafficJamLongEngine,
)
from strategies.traffic_jam_long.plugin import PLUGIN, build_specs
from strategies.traffic_jam_long.strategy import TrafficJamLongStrategy

__all__ = [
    "BUY",
    "HOLD",
    "PLUGIN",
    "SELL",
    "TrafficJamLongConfig",
    "TrafficJamLongEngine",
    "TrafficJamLongStrategy",
    "build_specs",
]
