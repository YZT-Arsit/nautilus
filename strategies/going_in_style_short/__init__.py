"""Going in Style short strategy package.

Exposes the strategy's public symbols, most importantly ``PLUGIN`` (registered
in ``strategy_framework/registry.py``). Split across ``config`` (parameters),
``engine`` (pure decision maths — a new-low-pullback short entry with a
parabolic-SAR accelerating trailing stop), ``strategy`` (snapshot adapter), and
``plugin`` (feature specs + registry wiring).
"""
from strategies.going_in_style_short.config import GoingInStyleShortConfig
from strategies.going_in_style_short.engine import (
    BUY,
    HOLD,
    SELL,
    GoingInStyleShortEngine,
)
from strategies.going_in_style_short.plugin import PLUGIN, build_specs
from strategies.going_in_style_short.strategy import GoingInStyleShortStrategy

__all__ = [
    "BUY",
    "HOLD",
    "PLUGIN",
    "SELL",
    "GoingInStyleShortConfig",
    "GoingInStyleShortEngine",
    "GoingInStyleShortStrategy",
    "build_specs",
]
