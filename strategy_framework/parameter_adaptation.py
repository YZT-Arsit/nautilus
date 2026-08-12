"""Safe parameter-adaptation primitives for workbook strategy research.

This module defines candidate generation and time-split contracts only.  It does
not select parameters and deliberately has no access to the final test result.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
import math


class ParameterSemantic(str, Enum):
    LOOKBACK_BARS = "lookback_bars"
    PHYSICAL_DURATION_MINUTES = "physical_duration_minutes"
    DIMENSIONLESS_THRESHOLD = "dimensionless_threshold"
    CALENDAR_SEMANTIC = "calendar_semantic"


def duration_preserving_bars(source_duration_minutes: float, target_bar_minutes: int) -> int:
    """Convert an actual duration to bars without silently capping it."""
    if source_duration_minutes <= 0 or target_bar_minutes <= 0:
        raise ValueError("durations must be positive")
    return max(1, math.ceil(source_duration_minutes / target_bar_minutes))


def logarithmic_integer_candidates(seed: int, *, lower: int, upper: int, count: int = 7) -> tuple[int, ...]:
    """Return a compact deterministic candidate set centred on a prior seed."""
    if not 0 < lower <= seed <= upper or count < 2:
        raise ValueError("require 0 < lower <= seed <= upper and count >= 2")
    log_lo, log_hi = math.log(lower), math.log(upper)
    values = {
        int(round(math.exp(log_lo + index * (log_hi - log_lo) / (count - 1))))
        for index in range(count)
    }
    values.add(seed)
    return tuple(sorted(value for value in values if lower <= value <= upper))


def ordered_window_pairs(short_candidates: tuple[int, ...], long_candidates: tuple[int, ...]) -> tuple[tuple[int, int], ...]:
    """Generate only structurally valid ``short < long`` pairs."""
    return tuple((short, long) for short in short_candidates for long in long_candidates if short < long)


@dataclass(frozen=True)
class ResearchSplit:
    train_start: datetime
    train_end: datetime
    validation_start: datetime
    validation_end: datetime
    test_start: datetime
    test_end: datetime

    def __post_init__(self) -> None:
        boundaries = (
            self.train_start, self.train_end, self.validation_start,
            self.validation_end, self.test_start, self.test_end,
        )
        if any(left >= right for left, right in zip(boundaries, boundaries[1:])):
            raise ValueError("train, validation and test boundaries must be strictly ordered")

    def selection_periods(self) -> tuple[tuple[datetime, datetime], tuple[datetime, datetime]]:
        """Expose only train/validation to parameter selection code."""
        return (
            (self.train_start, self.train_end),
            (self.validation_start, self.validation_end),
        )


@dataclass(frozen=True)
class ValidationScore:
    parameters: tuple[tuple[str, float | int], ...]
    train_score: float
    validation_score: float


def select_on_validation(results: tuple[ValidationScore, ...]) -> ValidationScore:
    """Select deterministically without accepting or inspecting test scores."""
    if not results:
        raise ValueError("at least one validation result is required")
    if any(not math.isfinite(item.validation_score) for item in results):
        raise ValueError("validation scores must be finite")
    return max(results, key=lambda item: (item.validation_score, item.train_score, item.parameters))
