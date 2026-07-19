"""Point-in-time crossover helpers shared by strategy adapters."""
from __future__ import annotations


def cross_over(prev: float | None, curr: float | None, threshold: float = 0.0) -> bool:
    return prev is not None and curr is not None and prev <= threshold < curr


def cross_under(prev: float | None, curr: float | None, threshold: float = 0.0) -> bool:
    return prev is not None and curr is not None and prev >= threshold > curr
