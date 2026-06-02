from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field


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

    @staticmethod
    def from_legacy(
        entry_side: str | None = None,
        entry_order_type: str | None = None,
        entry_price: float | None = None,
        exit_side: str | None = None,
        cancel_entry: bool = False,
        reason: str | None = None,
        debug: dict | None = None,
    ) -> "SignalResult":
        return SignalResult(
            entry_side=entry_side,
            entry_order_type=entry_order_type,
            entry_price=entry_price,
            exit_side=exit_side,
            cancel_entry=cancel_entry,
            reason=reason,
            debug=debug,
        )

    def to_legacy(self) -> dict:
        return {
            "entry_side": self.entry_side,
            "entry_order_type": self.entry_order_type,
            "entry_price": self.entry_price,
            "exit_side": self.exit_side,
            "cancel_entry": self.cancel_entry,
            "reason": self.reason,
            "debug": self.debug,
        }
