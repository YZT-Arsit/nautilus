"""Fill-synchronized execution adapter for ma_crossover_channel_short."""
from __future__ import annotations

from feature_engine.api import FeatureSnapshot
from strategy_framework.execution.legacy_adapter import LegacyExecutionState
from strategy_framework.execution.reports import ExecutionReport

from strategies.ma_crossover_channel_short.config import MaCrossoverChannelShortConfig
from strategies.ma_crossover_channel_short.strategy import MaCrossoverChannelShortStrategy


class MaCrossoverChannelShortExecutionAdapter:
    """Preserve legacy signals while making actual position fill-owned."""

    def __init__(self, config: MaCrossoverChannelShortConfig) -> None:
        self._signals = MaCrossoverChannelShortStrategy(config)
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
        signal = self._signals.on_snapshot(snapshot)
        self._state.observe_signal(signal)
        return signal

    def on_execution_report(self, report: ExecutionReport) -> None:
        self._state.on_execution_report(report)
