"""Three EMA Crossover long strategy package.

Exposes the strategy's public symbols, most importantly ``PLUGIN`` (registered
in ``strategy_framework/registry.py``). Split across ``config`` (parameters),
``engine`` (pure decision maths — triple-EMA crossover entry, EMA-reversal exit,
ratcheting range trailing stop), ``strategy`` (snapshot adapter), and ``plugin``
(feature specs + registry wiring).
"""
from strategies.three_ema_crossover_long.config import ThreeEmaCrossoverLongConfig
from strategies.three_ema_crossover_long.engine import (
    BUY,
    HOLD,
    SELL,
    ThreeEmaCrossoverLongEngine,
)
from strategies.three_ema_crossover_long.plugin import PLUGIN, build_specs
from strategies.three_ema_crossover_long.strategy import ThreeEmaCrossoverLongStrategy

__all__ = [
    "BUY",
    "HOLD",
    "PLUGIN",
    "SELL",
    "ThreeEmaCrossoverLongConfig",
    "ThreeEmaCrossoverLongEngine",
    "ThreeEmaCrossoverLongStrategy",
    "build_specs",
]
