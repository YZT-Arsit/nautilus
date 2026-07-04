"""Reference Deviation System short strategy package.

Exposes the strategy's public symbols, most importantly ``PLUGIN`` (registered
in ``strategy_framework/registry.py``). Split across ``config`` (parameters),
``engine`` (pure decision maths — the RDV mean-deviation oscillator, threshold
entry, zero-cross exit), ``strategy`` (snapshot adapter), and ``plugin`` (feature
specs + registry wiring).
"""
from strategies.reference_deviation_short.config import ReferenceDeviationShortConfig
from strategies.reference_deviation_short.engine import (
    BUY,
    HOLD,
    SELL,
    ReferenceDeviationShortEngine,
)
from strategies.reference_deviation_short.plugin import PLUGIN, build_specs
from strategies.reference_deviation_short.strategy import ReferenceDeviationShortStrategy

__all__ = [
    "BUY",
    "HOLD",
    "PLUGIN",
    "SELL",
    "ReferenceDeviationShortConfig",
    "ReferenceDeviationShortEngine",
    "ReferenceDeviationShortStrategy",
    "build_specs",
]
