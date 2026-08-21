"""Small, typed state operators shared by strategy plugins.

These helpers deliberately contain no indicator maths and no execution state.
They make previous-value, crossing and completed-bar persistence semantics
explicit, so workbook strategies do not each reimplement them.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Comparison(str, Enum):
    GT = ">"
    GE = ">="
    LT = "<"
    LE = "<="
    EQ = "=="


def compare(left: float, operator: Comparison, right: float) -> bool:
    if operator is Comparison.GT:
        return left > right
    if operator is Comparison.GE:
        return left >= right
    if operator is Comparison.LT:
        return left < right
    if operator is Comparison.LE:
        return left <= right
    return left == right


def cross_above(previous_left: float, previous_right: float, left: float, right: float) -> bool:
    """True only on the completed observation that changes ``left <= right`` to ``left > right``."""
    return previous_left <= previous_right and left > right


def cross_below(previous_left: float, previous_right: float, left: float, right: float) -> bool:
    """True only on the completed observation that changes ``left >= right`` to ``left < right``."""
    return previous_left >= previous_right and left < right


@dataclass
class ConsecutiveState:
    """Count consecutive completed observations satisfying one condition."""

    required: int
    count: int = 0

    def __post_init__(self) -> None:
        if self.required <= 0:
            raise ValueError("required must be positive")

    def update(self, condition: bool) -> bool:
        self.count = self.count + 1 if condition else 0
        return self.count >= self.required


@dataclass
class PreviousValues:
    """Bounded completed-observation history with explicit ``t-n`` lookup."""

    capacity: int

    def __post_init__(self) -> None:
        if self.capacity <= 0:
            raise ValueError("capacity must be positive")
        self._values: list[float] = []

    def push(self, value: float) -> None:
        self._values.append(float(value))
        if len(self._values) > self.capacity:
            del self._values[0]

    def previous(self, bars: int = 1) -> float | None:
        if bars <= 0:
            raise ValueError("bars must be positive")
        return self._values[-bars] if len(self._values) >= bars else None


def rising(previous: float, current: float) -> bool:
    return current > previous


def falling(previous: float, current: float) -> bool:
    return current < previous

