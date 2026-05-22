from __future__ import annotations
from collections import deque

class MomentumState:
    def __init__(self, period: int):
        if period <= 0:
            raise ValueError("period must be > 0.")
        self.period = period
        self.values = deque(maxlen=period + 1)

    def reset(self) -> None:
        self.values.clear()

    def update(self, value: float) -> float | None:
        self.values.append(value)
        if len(self.values) <= self.period:
            return None
        return self.values[-1] - self.values[0]


def cross_over(prev: float | None, curr: float | None, threshold: float = 0.0) -> bool:
    return prev is not None and curr is not None and prev <= threshold < curr

def cross_under(prev: float | None, curr: float | None, threshold: float = 0.0) -> bool:
    return prev is not None and curr is not None and prev >= threshold > curr