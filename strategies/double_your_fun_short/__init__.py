"""DoubleYourFun short strategy package.

Exposes the strategy's public symbols, most importantly ``PLUGIN`` (registered
in ``strategy_framework/registry.py``). Split across ``config`` (parameters),
``engine`` (pure decision maths — displaced-MA down/up/down double crossing arms a
low breakout; cover on the nearer of a reversal / trailing stop), ``strategy``
(snapshot adapter), and ``plugin`` (feature specs + registry wiring).
"""
from strategies.double_your_fun_short.config import DoubleYourFunShortConfig
from strategies.double_your_fun_short.engine import (
    BUY,
    HOLD,
    SELL,
    DoubleYourFunShortEngine,
)
from strategies.double_your_fun_short.plugin import PLUGIN, build_specs
from strategies.double_your_fun_short.strategy import DoubleYourFunShortStrategy

__all__ = [
    "BUY",
    "HOLD",
    "PLUGIN",
    "SELL",
    "DoubleYourFunShortConfig",
    "DoubleYourFunShortEngine",
    "DoubleYourFunShortStrategy",
    "build_specs",
]
