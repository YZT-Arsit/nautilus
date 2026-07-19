"""Fill-synchronized execution adapter for thermostat_long."""
from __future__ import annotations
from feature_engine.api import FeatureSnapshot
from strategy_framework.execution.legacy_adapter import LegacyExecutionState
from strategy_framework.execution.reports import ExecutionReport
from strategies.thermostat_long.config import ThermostatLongConfig
from strategies.thermostat_long.strategy import ThermostatLongStrategy

class ThermostatLongExecutionAdapter:
    """Preserve legacy signals while making actual position fill-owned."""
    def __init__(self, config: ThermostatLongConfig) -> None:
        self._signals = ThermostatLongStrategy(config)
        self._state = LegacyExecutionState(config.instrument_id, {"BUY": 1, "SELL": 0})
    @property
    def position(self) -> int:
        return self._state.position
    @property
    def decision_position(self) -> int:
        return self._state.decision_position
    @property
    def last_reason(self) -> str:
        return self._signals.last_reason
    def on_snapshot(self, snapshot: FeatureSnapshot) -> str:
        signal = self._signals.on_snapshot(snapshot)
        self._state.observe_signal(signal)
        return signal
    def on_execution_report(self, report: ExecutionReport) -> None:
        self._state.on_execution_report(report)
