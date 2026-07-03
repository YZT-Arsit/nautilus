"""Signal -> intent policy.

Turns a strategy signal into an :class:`OrderIntent` / :class:`PositionIntent`.
This is where the "what does a SELL mean?" decision lives — never in strategy
code. Dependency-free: **no Nautilus Trader imports**.
"""
from __future__ import annotations

from typing import Any, Iterable, Literal

from strategy_framework.execution.intents import (
    OrderIntent,
    PositionIntent,
    TradeAction,
)

_BUY, _SELL, _HOLD = "BUY", "SELL", "HOLD"


def plan_to_intents(
    actions: Iterable[TradeAction],
    event: Any,
) -> list[OrderIntent | PositionIntent]:
    """Translate a rich :class:`TradeAction` plan into execution intents.

    The sizing decision has already been made by the strategy (this is the
    escape hatch for risk-sized / pyramiding strategies), so — unlike
    :class:`SignalToOrderPolicy` — this does no fixed-quantity sizing. Each
    action becomes one intent, preserving order:

    * ``close_all=True`` -> ``PositionIntent(target="FLAT")`` (flatten fully);
    * otherwise          -> ``OrderIntent(side, quantity)``.

    A ``fill_price`` is threaded through ``metadata["fill_price"]`` so the
    simulated fill model can honour the strategy's intended fill price. Instrument
    id / event time are read off the event, matching :class:`SignalToOrderPolicy`.
    """
    instrument_id = getattr(event, "instrument_id", None)
    event_time_ns = getattr(event, "event_time_ns", 0)
    intents: list[OrderIntent | PositionIntent] = []
    for action in actions:
        metadata = dict(action.metadata or {})
        if action.fill_price is not None:
            metadata["fill_price"] = float(action.fill_price)
        if action.close_all:
            intents.append(
                PositionIntent(
                    instrument_id=instrument_id,
                    target="FLAT",
                    quantity=0.0,
                    event_time_ns=event_time_ns,
                    reason=action.reason,
                    metadata=metadata,
                )
            )
        else:
            intents.append(
                OrderIntent(
                    instrument_id=instrument_id,
                    side=action.side,
                    quantity=float(action.quantity),
                    event_time_ns=event_time_ns,
                    reason=action.reason,
                    metadata=metadata,
                )
            )
    return intents


class SignalToOrderPolicy:
    """Map ``(event, snapshot, signal)`` to a single intent (or ``None``).

    * ``HOLD`` -> ``None``
    * ``BUY``  -> ``OrderIntent(side="BUY")``
    * ``SELL`` -> ``OrderIntent(side="SELL")`` when ``sell_means="short"``,
      otherwise ``PositionIntent(target="FLAT")`` when ``sell_means="flat"``.

    ``instrument_id`` and ``event_time_ns`` are read off the event. Named feature
    values from the snapshot are attached to ``metadata`` when ``spec_names`` is
    given (cheap, stable: uses ``snapshot.value(name)`` which defaults to None).
    """

    def __init__(
        self,
        quantity: float = 1.0,
        sell_means: Literal["flat", "short"] = "flat",
        spec_names: list[str] | None = None,
    ) -> None:
        if sell_means not in ("flat", "short"):
            raise ValueError(f"sell_means must be 'flat' or 'short', got {sell_means!r}")
        self.quantity = float(quantity)
        self.sell_means = sell_means
        self._spec_names = list(spec_names or [])

    def _metadata(self, event: Any, snapshot: Any) -> dict[str, Any]:
        meta: dict[str, Any] = {}
        price = getattr(event, "close", None)
        if price is not None:
            meta["price"] = price
        for name in self._spec_names:
            value = getattr(snapshot, "value", lambda *_: None)(name)
            if value is not None:
                meta[name] = value
        return meta

    def on_signal(self, event: Any, snapshot: Any, signal: str):
        if signal == _HOLD:
            return None

        instrument_id = getattr(event, "instrument_id", None)
        event_time_ns = getattr(event, "event_time_ns", 0)
        metadata = self._metadata(event, snapshot)

        if signal == _BUY:
            return OrderIntent(
                instrument_id=instrument_id,
                side="BUY",
                quantity=self.quantity,
                event_time_ns=event_time_ns,
                reason="signal=BUY",
                metadata=metadata,
            )

        if signal == _SELL:
            if self.sell_means == "short":
                return OrderIntent(
                    instrument_id=instrument_id,
                    side="SELL",
                    quantity=self.quantity,
                    event_time_ns=event_time_ns,
                    reason="signal=SELL (short)",
                    metadata=metadata,
                )
            return PositionIntent(
                instrument_id=instrument_id,
                target="FLAT",
                quantity=0.0,
                event_time_ns=event_time_ns,
                reason="signal=SELL (flatten)",
                metadata=metadata,
            )

        # Unknown signal: ignore rather than guess.
        return None
