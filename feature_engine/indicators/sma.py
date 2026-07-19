"""Simple moving-average primitive shared by feature and strategy adapters."""
from __future__ import annotations

from collections.abc import Sequence


def sma(window: Sequence[float]) -> float:
    """Return the arithmetic mean of a non-empty caller-managed window."""
    if not window:
        raise ValueError("sma requires a non-empty window")
    return sum(window) / len(window)


def sma_last(values: Sequence[float], period: int) -> float | None:
    """Return the mean of the latest ``period`` values, or ``None``."""
    if period <= 0:
        raise ValueError("period must be > 0")
    if len(values) < period:
        return None
    return sma(list(values)[-period:])
