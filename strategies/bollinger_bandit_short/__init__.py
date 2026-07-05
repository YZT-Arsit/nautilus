"""Bollinger Bandit short strategy package.

Exposes the strategy's public symbols, most importantly ``PLUGIN`` (registered
in ``strategy_framework/registry.py``). Split across ``config`` (parameters),
``engine`` (pure decision maths — Bollinger lower-band breakout with a ROC filter
and an adaptive-length exit MA that tightens in-trade), ``strategy`` (snapshot
adapter), and ``plugin`` (feature specs + registry wiring).
"""
from strategies.bollinger_bandit_short.config import BollingerBanditShortConfig
from strategies.bollinger_bandit_short.engine import (
    BUY,
    HOLD,
    SELL,
    BollingerBanditShortEngine,
)
from strategies.bollinger_bandit_short.plugin import PLUGIN, build_specs
from strategies.bollinger_bandit_short.strategy import BollingerBanditShortStrategy

__all__ = [
    "BUY",
    "HOLD",
    "PLUGIN",
    "SELL",
    "BollingerBanditShortConfig",
    "BollingerBanditShortEngine",
    "BollingerBanditShortStrategy",
    "build_specs",
]
