"""Fill-synchronized lifecycle adapter for trend_breakout_atr."""
from __future__ import annotations

from feature_engine.api import FeatureSnapshot
from strategy_framework.execution.reports import ExecutionReport
from strategy_framework.execution.state_adapter import StrategyStateAdapter
from strategies.trend_breakout_atr.config import TrendBreakoutAtrConfig
from strategies.trend_breakout_atr.strategy import TrendBreakoutAtrStrategy


class TrendBreakoutAtrExecutionAdapter:
    """Keep bidirectional decision state separate from confirmed fills."""

    def __init__(self, config: TrendBreakoutAtrConfig) -> None:
        self._signals = TrendBreakoutAtrStrategy(config)
        self._state = StrategyStateAdapter(config.instrument_id)

    @property
    def position(self) -> int:
        return self._state.position

    @property
    def decision_position(self) -> int:
        return self._signals.position

    @property
    def last_reason(self) -> str:
        return self._signals.last_reason

    def on_snapshot(self, snapshot: FeatureSnapshot) -> str:
        return self._signals.on_snapshot(snapshot)

    def on_execution_report(self, report: ExecutionReport) -> None:
        self._state.on_execution_report(report)
