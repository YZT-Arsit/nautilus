"""JailBreak long strategy package.

Exposes the strategy's public symbols, most importantly ``PLUGIN`` (registered
in ``strategy_framework/registry.py``). Split across ``config`` (parameters),
``engine`` (pure decision maths — a long-period high-channel breakout long with
an ATR protective stop and a short-period low-channel exit), ``strategy``
(snapshot adapter), and ``plugin`` (feature specs + registry wiring).
"""
from strategies.jailbreak_long.config import JailBreakLongConfig
from strategies.jailbreak_long.engine import (
    BUY,
    HOLD,
    SELL,
    JailBreakLongEngine,
)
from strategies.jailbreak_long.plugin import PLUGIN, build_specs
from strategies.jailbreak_long.strategy import JailBreakLongStrategy

__all__ = [
    "BUY",
    "HOLD",
    "PLUGIN",
    "SELL",
    "JailBreakLongConfig",
    "JailBreakLongEngine",
    "JailBreakLongStrategy",
    "build_specs",
]
