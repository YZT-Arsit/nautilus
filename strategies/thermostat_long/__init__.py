"""Thermostat long strategy package.

Exposes the strategy's public symbols, most importantly ``PLUGIN`` (registered
in ``strategy_framework/registry.py``). Split across ``config`` (parameters),
``engine`` (pure decision maths — CMI regime switch, swing opening-range ATR
breakout, trend Bollinger breakout, per-regime exits), ``strategy`` (snapshot
adapter), and ``plugin`` (feature specs + registry wiring).
"""
from strategies.thermostat_long.config import ThermostatLongConfig
from strategies.thermostat_long.engine import (
    BUY,
    HOLD,
    SELL,
    ThermostatLongEngine,
)
from strategies.thermostat_long.plugin import PLUGIN, build_specs
from strategies.thermostat_long.strategy import ThermostatLongStrategy

__all__ = [
    "BUY",
    "HOLD",
    "PLUGIN",
    "SELL",
    "ThermostatLongConfig",
    "ThermostatLongEngine",
    "ThermostatLongStrategy",
    "build_specs",
]
