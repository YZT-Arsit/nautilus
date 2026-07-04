"""JailBreak short strategy package.

Exposes the strategy's public symbols, most importantly ``PLUGIN`` (registered
in ``strategy_framework/registry.py``). Split across ``config`` (parameters),
``engine`` (pure decision maths — a long-period low-channel breakdown short with
an ATR protective stop and a short-period high-channel exit), ``strategy``
(snapshot adapter), and ``plugin`` (feature specs + registry wiring).
"""
from strategies.jailbreak_short.config import JailBreakShortConfig
from strategies.jailbreak_short.engine import (
    BUY,
    HOLD,
    SELL,
    JailBreakShortEngine,
)
from strategies.jailbreak_short.plugin import PLUGIN, build_specs
from strategies.jailbreak_short.strategy import JailBreakShortStrategy

__all__ = [
    "BUY",
    "HOLD",
    "PLUGIN",
    "SELL",
    "JailBreakShortConfig",
    "JailBreakShortEngine",
    "JailBreakShortStrategy",
    "build_specs",
]
