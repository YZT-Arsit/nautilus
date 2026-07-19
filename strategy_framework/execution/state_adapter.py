"""Fill-owned state boundary for legacy strategies with sized position plans.

The adapter records pending requested quantities and reconciles them against
``FillRecord`` results.  It does not submit orders or calculate cash, PnL, fees,
funding, slippage, or latency.
"""
from __future__ import annotations

from collections.abc import Iterable

from strategy_framework.execution.intents import TradeAction
from strategy_framework.execution.reports import ExecutionReport, FillRecord


class StrategyStateAdapter:
    """Reconcile a legacy strategy's pending sized plan with confirmed fills."""

    def __init__(self, instrument_id: str) -> None:
        self.instrument_id = instrument_id
        self._filled_quantity = 0.0
        self._average_fill_price: float | None = None
        self._pending_add_quantity = 0.0
        self._pending_reduce_quantity = 0.0
        self._confirmed_entries = 0
        self._fill_cursor = 0
        self._last_fill_price: float | None = None
        self._last_increase_fill_price: float | None = None
        self._last_reduce_fill_price: float | None = None
        self._entry_fill_price: float | None = None
        self._exit_fill_price: float | None = None

    @property
    def position(self) -> int:
        if self._filled_quantity > 0:
            return 1
        if self._filled_quantity < 0:
            return -1
        return 0

    @property
    def filled_quantity(self) -> float:
        return self._filled_quantity

    @property
    def average_fill_price(self) -> float | None:
        return self._average_fill_price

    @property
    def pending_add_quantity(self) -> float:
        return self._pending_add_quantity

    @property
    def pending_reduce_quantity(self) -> float:
        return self._pending_reduce_quantity

    @property
    def confirmed_entries(self) -> int:
        return self._confirmed_entries

    @property
    def last_fill_price(self) -> float | None:
        return self._last_fill_price

    @property
    def last_increase_fill_price(self) -> float | None:
        return self._last_increase_fill_price

    @property
    def last_reduce_fill_price(self) -> float | None:
        return self._last_reduce_fill_price

    @property
    def entry_fill_price(self) -> float | None:
        return self._entry_fill_price

    @property
    def exit_fill_price(self) -> float | None:
        return self._exit_fill_price

    def observe_actions(
        self,
        actions: Iterable[TradeAction],
        *,
        decision_position: int,
    ) -> None:
        """Record requested add/reduce quantities without changing position."""
        for action in actions:
            quantity = max(0.0, float(action.quantity))
            if action.close_all:
                self._pending_reduce_quantity += quantity
            elif (
                (decision_position > 0 and action.side == "BUY")
                or (decision_position < 0 and action.side == "SELL")
            ):
                self._pending_add_quantity += quantity
            else:
                self._pending_reduce_quantity += quantity

    def on_fill(self, fill: FillRecord) -> bool:
        """Apply one confirmed fill; return whether it belongs to this strategy."""
        if fill.instrument_id != self.instrument_id:
            return False
        quantity = float(fill.quantity)
        if quantity <= 0:
            return False
        before = self._filled_quantity
        delta = quantity if fill.side == "BUY" else -quantity
        after = before + delta
        price = float(fill.price)
        increasing = before == 0.0 or (before > 0 and delta > 0) or (before < 0 and delta < 0)
        reversing = before != 0.0 and before * after < 0.0

        self._last_fill_price = price
        if increasing or reversing:
            self._pending_add_quantity = max(0.0, self._pending_add_quantity - quantity)
            self._last_increase_fill_price = price
            self._confirmed_entries = 1 if before == 0.0 or reversing else self._confirmed_entries + 1
            if before == 0.0 or reversing:
                self._entry_fill_price = price
            if reversing:
                self._exit_fill_price = price
            if before == 0.0 or reversing or self._average_fill_price is None:
                self._average_fill_price = price
            else:
                total = abs(before) + quantity
                self._average_fill_price = (
                    self._average_fill_price * abs(before) + price * quantity
                ) / total
        else:
            reduced = min(abs(before), quantity)
            self._pending_reduce_quantity = max(0.0, self._pending_reduce_quantity - reduced)
            self._last_reduce_fill_price = price
            if after == 0.0 or reversing:
                self._exit_fill_price = price
            if after == 0.0:
                self._average_fill_price = None
                self._confirmed_entries = 0
            elif reversing:
                self._average_fill_price = price
                self._confirmed_entries = 1

        self._filled_quantity = after
        return True

    def on_execution_report(self, report: ExecutionReport) -> list[FillRecord]:
        """Consume unseen cumulative fills and return the accepted new fills."""
        if self._fill_cursor > len(report.fills):
            raise ValueError("execution report fill history moved backwards")
        accepted: list[FillRecord] = []
        for fill in report.fills[self._fill_cursor :]:
            if self.on_fill(fill):
                accepted.append(fill)
        self._fill_cursor = len(report.fills)
        return accepted
