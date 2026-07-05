"""DoubleYourFun long strategy package.

Exposes the strategy's public symbols, most importantly ``PLUGIN`` (registered
in ``strategy_framework/registry.py``). Split across ``config`` (parameters),
``engine`` (pure decision maths — displaced-MA up/down/up double crossing arms a
high breakout; sell on the farther of a reversal / trailing stop), ``strategy``
(snapshot adapter), and ``plugin`` (feature specs + registry wiring).
"""
from strategies.double_your_fun_long.config import DoubleYourFunLongConfig
from strategies.double_your_fun_long.engine import (
    BUY,
    HOLD,
    SELL,
    DoubleYourFunLongEngine,
)
from strategies.double_your_fun_long.plugin import PLUGIN, build_specs
from strategies.double_your_fun_long.strategy import DoubleYourFunLongStrategy

__all__ = [
    "BUY",
    "HOLD",
    "PLUGIN",
    "SELL",
    "DoubleYourFunLongConfig",
    "DoubleYourFunLongEngine",
    "DoubleYourFunLongStrategy",
    "build_specs",
]
