"""Bollinger Bandit long strategy package.

Exposes the strategy's public symbols, most importantly ``PLUGIN`` (registered
in ``strategy_framework/registry.py``). Split across ``config`` (parameters),
``engine`` (pure decision maths — Bollinger upper-band breakout with a ROC filter
and an adaptive-length exit MA that tightens in-trade), ``strategy`` (snapshot
adapter), and ``plugin`` (feature specs + registry wiring).
"""
from strategies.bollinger_bandit_long.config import BollingerBanditLongConfig
from strategies.bollinger_bandit_long.engine import (
    BUY,
    HOLD,
    SELL,
    BollingerBanditLongEngine,
)
from strategies.bollinger_bandit_long.plugin import PLUGIN, build_specs
from strategies.bollinger_bandit_long.strategy import BollingerBanditLongStrategy

__all__ = [
    "BUY",
    "HOLD",
    "PLUGIN",
    "SELL",
    "BollingerBanditLongConfig",
    "BollingerBanditLongEngine",
    "BollingerBanditLongStrategy",
    "build_specs",
]
