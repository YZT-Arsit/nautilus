"""Self-contained event/result types for the VWM long strategy.

Copied verbatim from ``strategies/vwm_short/signal_types.py`` so the long-side
strategy is self-contained and shares no runtime state with the short. Pure
Python, no dependencies.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class BarInput:
    """OHLCV bar the VWM feature engine and signal engine consume."""

    open: float = 0.0
    high: float = 0.0
    low: float = 0.0
    close: float = 0.0
    volume: float = 0.0
    instrument_id: str | None = None
    event_time_ns: int | None = None  # nanoseconds — exchange/source timestamp
    ts_event: int | None = None       # milliseconds (legacy field, kept for compat)
    bar_type: str | None = None


@dataclass(frozen=True)
class OrderIntent:
    instrument_id: str | None = None
    action: str = "submit"
    order_type: str | None = None
    side: str | None = None
    quantity: float | None = None
    price: float | None = None
    trigger_price: float | None = None
    reduce_only: bool = False
    reason: str | None = None
    tags: dict | None = None


@dataclass(frozen=True)
class SignalResult:
    """VWM signal output. ``reason`` is what the strategy adapter reads."""

    signal_name: str | None = None
    order_intents: list[OrderIntent] = field(default_factory=list)
    debug: dict | None = None
    state: dict | None = None
    reason: str | None = None
    entry_side: str | None = None
    entry_order_type: str | None = None
    entry_price: float | None = None
    exit_side: str | None = None
    cancel_entry: bool = False

    def __post_init__(self) -> None:
        if self.order_intents:
            return
        intents: list[OrderIntent] = []
        if self.cancel_entry:
            intents.append(OrderIntent(action="cancel_entry", reason=self.reason))
        if self.entry_side is not None:
            intents.append(
                OrderIntent(
                    action="submit",
                    order_type=self.entry_order_type or "market",
                    side=self.entry_side,
                    trigger_price=self.entry_price,
                    reason=self.reason,
                ),
            )
        if self.exit_side is not None:
            intents.append(
                OrderIntent(
                    action="submit",
                    order_type="market",
                    side=self.exit_side,
                    reduce_only=True,
                    reason=self.reason,
                ),
            )
        object.__setattr__(self, "order_intents", intents)
