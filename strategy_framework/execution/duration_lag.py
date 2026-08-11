"""Real-time lag adapter for target-position execution on irregular events.

This adapter schedules a strategy signal by absolute nanosecond time and turns
it into an :class:`OrderIntent` on the first later market event whose exchange
timestamp reaches the due time.  It owns only pending target state and the
filled position quantity; order filling and PnL remain in the configured
execution backend.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Any, Callable

from strategy_framework.execution.intents import OrderIntent
from strategy_framework.execution.reports import FillRecord


@dataclass(frozen=True)
class PendingTarget:
    instrument_id: str
    direction: int
    signal: str
    signal_time_ns: int
    due_time_ns: int


@dataclass(frozen=True)
class DurationExecutionAttempt:
    target: PendingTarget
    intent: OrderIntent | None
    fill: FillRecord | None
    fill_time_ns: int
    price: float
    position_before: float
    position_after: float

    @property
    def observed_lag_ns(self) -> int:
        return self.fill_time_ns - self.target.signal_time_ns


class DurationLagTargetAdapter:
    """Schedule BUY/SELL targets by duration and reconcile state from fills.

    ``notional`` is converted to a target quantity at the actual execution
    event price.  Reversals therefore submit the delta between the current
    fill-synchronised quantity and the new signed target quantity.  Setting
    ``reverse=True`` changes only the signal-to-target mapping; signal
    generation is untouched.
    """

    def __init__(
        self,
        *,
        lag_ns: int,
        notional: float,
        reverse: bool = False,
        price_field: str = "price",
    ) -> None:
        if lag_ns < 0:
            raise ValueError("lag_ns must be non-negative")
        if notional <= 0:
            raise ValueError("notional must be positive")
        self.lag_ns = int(lag_ns)
        self.notional = float(notional)
        self.reverse = bool(reverse)
        self.price_field = price_field
        self._pending: deque[PendingTarget] = deque()
        self._position_qty = 0.0
        self._last_market_time_ns: int | None = None

    @property
    def position_qty(self) -> float:
        return self._position_qty

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    def schedule(self, event: Any, signal: str) -> PendingTarget | None:
        """Schedule BUY/SELL; HOLD and unknown labels do not create targets."""
        if signal not in ("BUY", "SELL"):
            return None
        direction = 1 if signal == "BUY" else -1
        if self.reverse:
            direction *= -1
        signal_time_ns = int(getattr(event, "event_time_ns"))
        target = PendingTarget(
            instrument_id=str(getattr(event, "instrument_id")),
            direction=direction,
            signal=signal,
            signal_time_ns=signal_time_ns,
            due_time_ns=signal_time_ns + self.lag_ns,
        )
        self._pending.append(target)
        return target

    def _intent(self, target: PendingTarget, event: Any, price: float) -> OrderIntent | None:
        target_qty = target.direction * self.notional / price
        delta = target_qty - self._position_qty
        if abs(delta) <= 1e-15:
            return None
        return OrderIntent(
            instrument_id=target.instrument_id,
            side="BUY" if delta > 0 else "SELL",
            quantity=abs(delta),
            event_time_ns=int(getattr(event, "event_time_ns")),
            reason=(
                f"duration_lag_target signal={target.signal} "
                f"reverse={self.reverse}"
            ),
            metadata={
                "signal_time_ns": target.signal_time_ns,
                "due_time_ns": target.due_time_ns,
                "configured_lag_ns": self.lag_ns,
                "target_direction": target.direction,
                "target_notional": self.notional,
                "fill_price": price,
            },
        )

    def on_fill(self, fill: FillRecord) -> None:
        """Synchronise actual position from a backend fill record."""
        signed = float(fill.quantity) if fill.side == "BUY" else -float(fill.quantity)
        self._position_qty += signed

    def on_market_event(
        self,
        event: Any,
        fill_handler: Callable[[OrderIntent, Any], FillRecord | None],
    ) -> list[DurationExecutionAttempt]:
        """Execute every target due on this first eligible market event.

        Call this before computing/scheduling the current event's new signal.
        Consequently ``lag_ns=0`` still fills on the first *following* trade,
        never on the trade used to calculate the signal.
        """
        event_time_ns = int(getattr(event, "event_time_ns"))
        if self._last_market_time_ns is not None and event_time_ns < self._last_market_time_ns:
            raise ValueError("market event timestamps must be non-decreasing")
        self._last_market_time_ns = event_time_ns
        price = float(getattr(event, self.price_field))
        if price <= 0:
            raise ValueError(f"{self.price_field} must be positive")

        attempts: list[DurationExecutionAttempt] = []
        while self._pending and self._pending[0].due_time_ns <= event_time_ns:
            target = self._pending.popleft()
            before = self._position_qty
            intent = self._intent(target, event, price)
            fill = fill_handler(intent, event) if intent is not None else None
            if fill is not None:
                self.on_fill(fill)
            attempts.append(
                DurationExecutionAttempt(
                    target=target,
                    intent=intent,
                    fill=fill,
                    fill_time_ns=event_time_ns,
                    price=price,
                    position_before=before,
                    position_after=self._position_qty,
                )
            )
        return attempts
