"""Streaming momentum primitive shared by strategy adapters."""
from __future__ import annotations

from collections import deque


class Momentum:
    """``x[t] - x[t-period]`` with explicit reset/checkpoint support."""

    def __init__(self, period: int) -> None:
        if period <= 0:
            raise ValueError("period must be > 0")
        self.period = period
        self.values: deque[float] = deque(maxlen=period + 1)

    def reset(self) -> None:
        self.values.clear()

    def update(self, value: float) -> float | None:
        self.values.append(value)
        if len(self.values) <= self.period:
            return None
        return self.values[-1] - self.values[0]

    def state_dict(self) -> dict:
        return {"period": self.period, "values": list(self.values)}

    def load_state_dict(self, state: dict) -> None:
        if int(state["period"]) != self.period:
            raise ValueError("momentum period does not match checkpoint")
        values = [float(value) for value in state.get("values", [])]
        if len(values) > self.period + 1:
            raise ValueError("momentum checkpoint contains too many values")
        self.values.clear()
        self.values.extend(values)
