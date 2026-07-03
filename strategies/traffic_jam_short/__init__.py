"""Traffic Jam short strategy package.

Exposes the strategy's public symbols, most importantly ``PLUGIN`` (registered
in ``strategy_framework/registry.py``). Split across ``config`` (parameters),
``engine`` (pure decision maths — Wilder DMI/ADX ranging filter, consecutive
up-close fade, ATR protective stop + time exit), ``strategy`` (snapshot adapter),
and ``plugin`` (feature specs + registry wiring).
"""
from strategies.traffic_jam_short.config import TrafficJamShortConfig
from strategies.traffic_jam_short.engine import (
    BUY,
    HOLD,
    SELL,
    TrafficJamShortEngine,
)
from strategies.traffic_jam_short.plugin import PLUGIN, build_specs
from strategies.traffic_jam_short.strategy import TrafficJamShortStrategy

__all__ = [
    "BUY",
    "HOLD",
    "PLUGIN",
    "SELL",
    "TrafficJamShortConfig",
    "TrafficJamShortEngine",
    "TrafficJamShortStrategy",
    "build_specs",
]
