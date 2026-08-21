"""
Safe parameter-adaptation primitives for workbook strategy research.

This module defines candidate generation and time-split contracts only.  It does
not select parameters and deliberately has no access to the final test result.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import math
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any


class ParameterSemantic(str, Enum):
    BAR_LOOKBACK = "BAR_LOOKBACK"
    PHYSICAL_DURATION = "PHYSICAL_DURATION"
    DIMENSIONLESS_THRESHOLD = "DIMENSIONLESS_THRESHOLD"
    VOLATILITY_MULTIPLIER = "VOLATILITY_MULTIPLIER"
    POSITION_FRACTION = "POSITION_FRACTION"
    INTEGER_STATE_PARAMETER = "INTEGER_STATE_PARAMETER"
    PRICE_DISTANCE = "PRICE_DISTANCE"
    CALENDAR_PARAMETER = "CALENDAR_PARAMETER"
    SESSION_PARAMETER = "SESSION_PARAMETER"
    EXECUTION_PARAMETER = "EXECUTION_PARAMETER"
    BOOLEAN_ENUM_SEMANTICS = "BOOLEAN_ENUM_SEMANTICS"

    # Compatibility aliases for the Phase 2 API.
    LOOKBACK_BARS = BAR_LOOKBACK
    PHYSICAL_DURATION_MINUTES = PHYSICAL_DURATION
    CALENDAR_SEMANTIC = CALENDAR_PARAMETER


class AdaptationMode(str, Enum):
    DIRECT_INTRADAY = "DIRECT_INTRADAY"
    DURATION_PRESERVING = "DURATION_PRESERVING"
    SEARCH_ADAPTED = "SEARCH_ADAPTED"
    UNSAFE_TO_CONVERT = "UNSAFE_TO_CONVERT"
    NOT_APPLICABLE = "NOT_APPLICABLE"


def classify_parameter(name: str, value: Any) -> ParameterSemantic:  # noqa: C901
    """Classify a config parameter without treating every number as searchable."""
    key = name.lower()
    if isinstance(value, (bool, str)):
        return ParameterSemantic.BOOLEAN_ENUM_SEMANTICS
    if any(token in key for token in ("lag", "latency", "slippage", "fee", "fill_timing")):
        return ParameterSemantic.EXECUTION_PARAMETER
    if any(token in key for token in ("month", "week", "calendar", "previous_day", "day_of_")):
        return ParameterSemantic.CALENDAR_PARAMETER
    if any(token in key for token in ("session", "opening_range", "daily_trade")):
        return ParameterSemantic.SESSION_PARAMETER
    if any(token in key for token in ("duration", "minutes", "hours", "holding_time")):
        return ParameterSemantic.PHYSICAL_DURATION
    if any(token in key for token in ("fraction", "exposure", "risk_ratio", "position_ratio")):
        return ParameterSemantic.POSITION_FRACTION
    if (
        any(token in key for token in ("multiple", "multiplier"))
        and any(token in key for token in ("atr", "volatility", "stop", "band"))
    ) or (
        (key.endswith("_atr") or key.startswith("atr_"))
        and not any(token in key for token in ("window", "length"))
    ):
        return ParameterSemantic.VOLATILITY_MULTIPLIER
    if any(token in key for token in ("distance", "ticks", "price_offset")):
        return ParameterSemantic.PRICE_DISTANCE
    if any(
        token in key
        for token in (
            "consecutive",
            "persistence",
            "stages",
            "layers",
            "side_bars",
            "n_entries",
            "holding_bars",
            "max_units",
        )
    ):
        return ParameterSemantic.INTEGER_STATE_PARAMETER
    if any(
        token in key
        for token in (
            "window",
            "lookback",
            "length",
            "period",
            "fast",
            "slow",
            "recent_extreme",
            "breakout_len",
            "exit_len",
            "atr_length",
        )
    ):
        return ParameterSemantic.BAR_LOOKBACK
    return ParameterSemantic.DIMENSIONLESS_THRESHOLD


def duration_preserving_bars(source_duration_minutes: float, target_bar_minutes: int) -> int:
    """Convert an actual duration to bars without silently capping it."""
    if source_duration_minutes <= 0 or target_bar_minutes <= 0:
        raise ValueError("durations must be positive")
    return max(1, math.ceil(source_duration_minutes / target_bar_minutes))


def logarithmic_integer_candidates(
    seed: int, *, lower: int, upper: int, count: int = 7
) -> tuple[int, ...]:
    """Return a compact deterministic candidate set centred on a prior seed."""
    if not 0 < lower <= seed <= upper or count < 2:
        raise ValueError("require 0 < lower <= seed <= upper and count >= 2")
    log_lo, log_hi = math.log(lower), math.log(upper)
    values = {
        round(math.exp(log_lo + index * (log_hi - log_lo) / (count - 1))) for index in range(count)
    }
    values.add(seed)
    return tuple(sorted(value for value in values if lower <= value <= upper))


def ordered_window_pairs(
    short_candidates: tuple[int, ...], long_candidates: tuple[int, ...]
) -> tuple[tuple[int, int], ...]:
    """Generate only structurally valid ``short < long`` pairs."""
    return tuple(
        (short, long) for short in short_candidates for long in long_candidates if short < long
    )


def validate_parameter_constraints(parameters: dict[str, Any]) -> tuple[bool, tuple[str, ...]]:
    """Reject structurally invalid candidates before they reach a backtester."""
    failures: list[str] = []
    for name, value in parameters.items():
        semantic = classify_parameter(name, value)
        if semantic in {
            ParameterSemantic.BAR_LOOKBACK,
            ParameterSemantic.INTEGER_STATE_PARAMETER,
        } and (not isinstance(value, int) or isinstance(value, bool) or value < 1):
            failures.append(f"{name}:positive_integer")
        if semantic is ParameterSemantic.POSITION_FRACTION and not 0 < float(value) <= 1:
            failures.append(f"{name}:fraction_range")
    for fast_name, slow_name in (
        ("fast_window", "slow_window"),
        ("macd_fast", "macd_slow"),
        ("lower_threshold", "upper_threshold"),
    ):
        if (
            fast_name in parameters
            and slow_name in parameters
            and float(parameters[fast_name]) >= float(parameters[slow_name])
        ):
            failures.append(f"{fast_name}<{slow_name}")
    layers = parameters.get("max_layers", parameters.get("grid_layers"))
    fraction = parameters.get("layer_fraction")
    cap = parameters.get("max_exposure", parameters.get("exposure_cap"))
    if (
        layers is not None
        and fraction is not None
        and cap is not None
        and float(layers) * float(fraction) > float(cap) + 1e-12
    ):
        failures.append("layer_fraction*layers<=exposure_cap")
    return not failures, tuple(failures)


def canonical_config_hash(parameters: dict[str, Any]) -> str:
    payload = json.dumps(parameters, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()


def deterministic_candidate_id(search_id: str, parameters: dict[str, Any]) -> str:
    return f"{search_id}__{canonical_config_hash(parameters)[:16]}"


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
            self.train_start,
            self.train_end,
            self.validation_start,
            self.validation_end,
            self.test_start,
            self.test_end,
        )
        if any(left >= right for left, right in itertools.pairwise(boundaries)):
            raise ValueError("train, validation and test boundaries must be strictly ordered")

    def selection_periods(self) -> tuple[tuple[datetime, datetime], tuple[datetime, datetime]]:
        """Expose only train/validation to parameter selection code."""
        return (
            (self.train_start, self.train_end),
            (self.validation_start, self.validation_end),
        )

    def contains_no_selection_leakage(self) -> bool:
        return self.train_end < self.validation_start and self.validation_end < self.test_start


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
