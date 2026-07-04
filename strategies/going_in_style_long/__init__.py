"""Going in Style long strategy package.

Exposes the strategy's public symbols, most importantly ``PLUGIN`` (registered
in ``strategy_framework/registry.py``). Split across ``config`` (parameters),
``engine`` (pure decision maths — a new-high-pullback long entry with a
parabolic-SAR accelerating trailing stop), ``strategy`` (snapshot adapter), and
``plugin`` (feature specs + registry wiring).
"""
from strategies.going_in_style_long.config import GoingInStyleLongConfig
from strategies.going_in_style_long.engine import (
    BUY,
    HOLD,
    SELL,
    GoingInStyleLongEngine,
)
from strategies.going_in_style_long.plugin import PLUGIN, build_specs
from strategies.going_in_style_long.strategy import GoingInStyleLongStrategy

__all__ = [
    "BUY",
    "HOLD",
    "PLUGIN",
    "SELL",
    "GoingInStyleLongConfig",
    "GoingInStyleLongEngine",
    "GoingInStyleLongStrategy",
    "build_specs",
]
