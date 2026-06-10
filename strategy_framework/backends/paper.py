"""Placeholder paper-trading backend.

Logs the *intended* order for each actionable signal. It is intentionally not a
trading engine: there are no fills, no positions, and no PnL — that work is
deferred to a future real backend. It exists so the backend interface can be
exercised end-to-end without any exchange or network dependency.
"""
from __future__ import annotations

from typing import Any

# Signals that would translate into an order intent (HOLD is a no-op).
_ACTIONABLE = ("BUY", "SELL")


class PaperBackend:
    """Records and logs order intents derived from BUY/SELL signals."""

    def __init__(self, spec_names: list[str] | None = None) -> None:
        self._spec_names = list(spec_names or [])
        self._intents: list[dict[str, Any]] = []

    def on_signal(self, event: Any, snapshot: Any, signal: str) -> None:
        if signal not in _ACTIONABLE:
            return
        intent = {
            "side": signal,
            "instrument_id": getattr(event, "instrument_id", None),
            "price": getattr(event, "close", None),
            "event_time_ns": getattr(event, "event_time_ns", None),
        }
        self._intents.append(intent)
        print(
            f"[paper] intent: {intent['side']} {intent['instrument_id']} "
            f"@ {intent['price']} (t={intent['event_time_ns']})"
        )

    def intents(self) -> list[dict[str, Any]]:
        return list(self._intents)

    def close(self) -> None:
        print(f"[paper] {len(self._intents)} intended order(s) logged (no fills, no PnL)")
