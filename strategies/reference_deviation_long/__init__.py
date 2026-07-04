"""Reference Deviation System long strategy package.

Exposes the strategy's public symbols, most importantly ``PLUGIN`` (registered
in ``strategy_framework/registry.py``). Split across ``config`` (parameters),
``engine`` (pure decision maths — the RDV mean-deviation oscillator, threshold
entry, zero-cross exit), ``strategy`` (snapshot adapter), and ``plugin`` (feature
specs + registry wiring).
"""
from strategies.reference_deviation_long.config import ReferenceDeviationLongConfig
from strategies.reference_deviation_long.engine import (
    BUY,
    HOLD,
    SELL,
    ReferenceDeviationLongEngine,
)
from strategies.reference_deviation_long.plugin import PLUGIN, build_specs
from strategies.reference_deviation_long.strategy import ReferenceDeviationLongStrategy

__all__ = [
    "BUY",
    "HOLD",
    "PLUGIN",
    "SELL",
    "ReferenceDeviationLongConfig",
    "ReferenceDeviationLongEngine",
    "ReferenceDeviationLongStrategy",
    "build_specs",
]
