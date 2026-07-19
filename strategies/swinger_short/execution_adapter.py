"""Fill-synchronized execution adapter for Swinger short."""
from __future__ import annotations

from feature_engine.api import FeatureSnapshot
from strategy_framework.execution.legacy_adapter import LegacyExecutionState
from strategy_framework.execution.reports import ExecutionReport

from strategies.swinger_short.config import SwingerShortConfig
from strategies.swinger_short.strategy import SwingerShortStrategy


class SwingerShortExecutionAdapter:
    def __init__(self, config: SwingerShortConfig) -> None:
        self._signals = SwingerShortStrategy(config)
        self._state = LegacyExecutionState(config.instrument_id, {"SELL": -1, "BUY": 0})

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
        signal = self._signals.signal_from_snapshot(
            snapshot,
            position=self._state.decision_position,
            previous_position=self._state.previous_decision_position,
            bars_since_entry=self._state.decision_bars_since_entry,
        )
        self._state.observe_signal(signal)
        return signal

    def on_execution_report(self, report: ExecutionReport) -> None:
        self._state.on_execution_report(report)
