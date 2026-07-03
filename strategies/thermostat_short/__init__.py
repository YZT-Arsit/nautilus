"""Thermostat short strategy package.

Exposes the strategy's public symbols, most importantly ``PLUGIN`` (registered
in ``strategy_framework/registry.py``). Split across ``config`` (parameters),
``engine`` (pure decision maths — CMI regime switch, swing opening-range ATR
breakout, trend Bollinger breakout, per-regime exits), ``strategy`` (snapshot
adapter), and ``plugin`` (feature specs + registry wiring).
"""
from strategies.thermostat_short.config import ThermostatShortConfig
from strategies.thermostat_short.engine import (
    BUY,
    HOLD,
    SELL,
    ThermostatShortEngine,
)
from strategies.thermostat_short.plugin import PLUGIN, build_specs
from strategies.thermostat_short.strategy import ThermostatShortStrategy

__all__ = [
    "BUY",
    "HOLD",
    "PLUGIN",
    "SELL",
    "ThermostatShortConfig",
    "ThermostatShortEngine",
    "ThermostatShortStrategy",
    "build_specs",
]
