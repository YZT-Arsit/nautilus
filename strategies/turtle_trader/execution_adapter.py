"""Fill-synchronized execution ownership adapter for Turtle Trader."""
from __future__ import annotations

from feature_engine.api import FeatureSnapshot
from strategy_framework.execution.intents import PlannedSignal
from strategy_framework.execution.reports import ExecutionReport
from strategy_framework.execution.state_adapter import StrategyStateAdapter

from strategies.turtle_trader.config import TurtleTraderConfig
from strategies.turtle_trader.strategy import TurtleTraderStrategy


class TurtleTraderExecutionAdapter:
    """Keep the Turtle decision plan unchanged and reconcile pyramid fills."""

    def __init__(self, config: TurtleTraderConfig) -> None:
        self._signals = TurtleTraderStrategy(config)
        self._state = StrategyStateAdapter(config.instrument_id)

    @property
    def position(self) -> int:
        """Actual execution position, derived only from confirmed fills."""
        return self._state.position

    @property
    def decision_position(self) -> int:
        """Pending decision shadow used solely to preserve signal sequencing."""
        return self._signals.position

    @property
    def execution_state(self) -> StrategyStateAdapter:
        return self._state

    @property
    def last_reason(self) -> str:
        return self._signals.last_reason

    def on_snapshot(self, snapshot: FeatureSnapshot) -> PlannedSignal:
        signal = self._signals.on_snapshot(snapshot)
        self._state.observe_actions(
            signal.actions,
            decision_position=self._signals.position,
        )
        return signal

    def on_execution_report(self, report: ExecutionReport) -> None:
        """Reconcile actual pyramid state and fill anchors from unseen fills."""
        new_fills = self._state.on_execution_report(report)
        if not new_fills:
            return
        engine = self._signals._engine
        if self._state.position == 0:
            engine.position = 0
            engine.units = 0.0
            engine.current_entries = 0
            engine.pre_entry_price = None
            return
        engine.position = self._state.position
        engine.units = abs(self._state.filled_quantity)
        engine.current_entries = self._state.confirmed_entries
        engine.pre_entry_price = self._state.last_increase_fill_price
