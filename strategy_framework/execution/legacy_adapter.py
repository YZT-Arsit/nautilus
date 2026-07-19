"""Reusable execution state for migrated legacy signal strategies.

This module does not create orders, simulate fills, or calculate PnL. Strategy-
specific adapters provide a signal-to-target mapping; this state object keeps a
pending decision target separate from the position and prices observed in real
``FillRecord`` results.
"""
from __future__ import annotations

from collections.abc import Mapping

from strategy_framework.execution.reports import ExecutionReport, FillRecord


class LegacyExecutionState:
    """Fill-synchronized state shared by simple long/flat and short/flat adapters."""

    def __init__(
        self,
        instrument_id: str,
        signal_targets: Mapping[str, int],
    ) -> None:
        targets = {str(signal): int(target) for signal, target in signal_targets.items()}
        if any(target not in (-1, 0, 1) for target in targets.values()):
            raise ValueError("legacy adapter targets must be -1, 0, or 1")
        self.instrument_id = instrument_id
        self._signal_targets = targets
        self._filled_quantity = 0.0
        self._decision_position = 0
        self._previous_decision_position = 0
        self._decision_bars_since_entry = 0
        self._fill_cursor = 0
        self._last_fill_price: float | None = None
        self._entry_fill_price: float | None = None
        self._exit_fill_price: float | None = None

    @property
    def position(self) -> int:
        """Actual direction derived only from observed filled quantity."""
        if self._filled_quantity > 0:
            return 1
        if self._filled_quantity < 0:
            return -1
        return 0

    @property
    def filled_quantity(self) -> float:
        return self._filled_quantity

    @property
    def pending_target_position(self) -> int:
        return self._decision_position

    @property
    def decision_position(self) -> int:
        return self._decision_position

    @property
    def previous_decision_position(self) -> int:
        return self._previous_decision_position

    @property
    def decision_bars_since_entry(self) -> int:
        return self._decision_bars_since_entry

    @property
    def last_fill_price(self) -> float | None:
        return self._last_fill_price

    @property
    def entry_fill_price(self) -> float | None:
        return self._entry_fill_price

    @property
    def exit_fill_price(self) -> float | None:
        return self._exit_fill_price

    def observe_signal(self, signal: str) -> None:
        """Update only the pending target; never update the filled position."""
        target = self._signal_targets.get(str(signal))
        if target is not None and target != self._decision_position:
            self._decision_position = target
            self._decision_bars_since_entry = 0
        self._previous_decision_position = self._decision_position
        if self._decision_position != 0:
            self._decision_bars_since_entry += 1

    def on_fill(self, fill: FillRecord) -> None:
        """Synchronize quantity, direction, and prices from one real fill."""
        if fill.instrument_id != self.instrument_id:
            return
        before = self._filled_quantity
        delta = float(fill.quantity) if fill.side == "BUY" else -float(fill.quantity)
        after = before + delta
        price = float(fill.price)
        self._filled_quantity = after
        self._last_fill_price = price
        if before == 0.0 and after != 0.0:
            self._entry_fill_price = price
        elif before != 0.0 and after == 0.0:
            self._exit_fill_price = price
        elif before * after < 0.0:
            self._exit_fill_price = price
            self._entry_fill_price = price

    def on_execution_report(self, report: ExecutionReport) -> None:
        """Consume unseen fills from a cumulative execution report."""
        fills = report.fills
        if self._fill_cursor > len(fills):
            raise ValueError("execution report fill history moved backwards")
        for fill in fills[self._fill_cursor :]:
            self.on_fill(fill)
        self._fill_cursor = len(fills)
