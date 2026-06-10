"""Placeholder paper-trading backend.

Logs the *intended* order for each actionable signal, using the shared
:class:`SignalToOrderPolicy` so the signal->intent mapping is identical across
backends. It is intentionally not a trading engine: there are no fills, no
positions, and no PnL. No exchange/network dependency.
"""
from __future__ import annotations

from typing import Any

from strategy_framework.execution.intents import OrderIntent, PositionIntent
from strategy_framework.execution.signal_policy import SignalToOrderPolicy


class PaperBackend:
    """Records and logs order/position intents derived from signals."""

    def __init__(self, spec_names: list[str] | None = None, execution_config: dict[str, Any] | None = None) -> None:
        self._spec_names = list(spec_names or [])
        cfg = execution_config or {}
        self._policy = SignalToOrderPolicy(
            quantity=float(cfg.get("quantity", 1.0)),
            sell_means=cfg.get("sell_means", "flat"),
            spec_names=self._spec_names,
        )
        self._intents: list[OrderIntent | PositionIntent] = []

    def on_signal(self, event: Any, snapshot: Any, signal: str) -> None:
        intent = self._policy.on_signal(event, snapshot, signal)
        if intent is None:
            return
        self._intents.append(intent)
        action = getattr(intent, "side", None) or getattr(intent, "target", None)
        print(
            f"[paper] intent: {action} {intent.instrument_id} "
            f"qty={intent.quantity} (t={intent.event_time_ns}) — {intent.reason}"
        )

    def intents(self) -> list[OrderIntent | PositionIntent]:
        return list(self._intents)

    def close(self) -> None:
        print(f"[paper] {len(self._intents)} intended order(s) logged (no fills, no PnL)")
