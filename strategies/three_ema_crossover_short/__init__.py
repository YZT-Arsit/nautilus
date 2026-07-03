"""Three EMA Crossover short strategy package.

Exposes the strategy's public symbols, most importantly ``PLUGIN`` (registered
in ``strategy_framework/registry.py``). Split across ``config`` (parameters),
``engine`` (pure decision maths — triple-EMA crossover entry, EMA-reversal exit,
ratcheting range trailing stop), ``strategy`` (snapshot adapter), and ``plugin``
(feature specs + registry wiring).
"""
from strategies.three_ema_crossover_short.config import ThreeEmaCrossoverShortConfig
from strategies.three_ema_crossover_short.engine import (
    BUY,
    HOLD,
    SELL,
    ThreeEmaCrossoverShortEngine,
)
from strategies.three_ema_crossover_short.plugin import PLUGIN, build_specs
from strategies.three_ema_crossover_short.strategy import ThreeEmaCrossoverShortStrategy

__all__ = [
    "BUY",
    "HOLD",
    "PLUGIN",
    "SELL",
    "ThreeEmaCrossoverShortConfig",
    "ThreeEmaCrossoverShortEngine",
    "ThreeEmaCrossoverShortStrategy",
    "build_specs",
]
