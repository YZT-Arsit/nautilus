"""Fill-state adapter for workbook families with fill-anchored exits."""
from __future__ import annotations

from feature_engine.api import FeatureSnapshot
from strategy_framework.execution.reports import ExecutionReport
from strategy_framework.execution.intents import PlannedSignal
from strategy_framework.execution.state_adapter import StrategyStateAdapter

from strategies.workbook_parametric.config import WorkbookParametricConfig
from strategies.workbook_parametric.strategy import WorkbookParametricStrategy


class WorkbookExecutionAdapter:
    """Pass confirmed fill anchors to decision logic without assuming fills."""

    def __init__(self, config: WorkbookParametricConfig) -> None:
        self._signals = WorkbookParametricStrategy(config)
        self._state = StrategyStateAdapter(config.instrument_id)

    @property
    def decision_position(self) -> float:
        return self._signals.decision_position

    @property
    def position(self) -> float:
        return self._state.filled_quantity

    def on_snapshot(self, snapshot: FeatureSnapshot):
        self._signals.execution_entry_price = self._state.entry_fill_price
        signal = self._signals.on_snapshot(snapshot)
        if not isinstance(signal, PlannedSignal):
            signal = PlannedSignal(str(signal), ())
        self._state.observe_actions(signal.actions, decision_position=self._signals.decision_position)
        return signal

    def on_warmup_snapshot(self, snapshot: FeatureSnapshot) -> None:
        self._signals.on_warmup_snapshot(snapshot)

    def on_execution_report(self, report: ExecutionReport) -> None:
        new_fills = self._state.on_execution_report(report)
        self._signals.execution_entry_price = self._state.entry_fill_price
        if new_fills:
            self._signals.synchronize_execution(
                position=self._state.filled_quantity,
                fill_price=float(new_fills[-1].price),
            )
