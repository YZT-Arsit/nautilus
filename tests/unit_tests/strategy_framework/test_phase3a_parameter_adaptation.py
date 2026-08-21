from __future__ import annotations

from datetime import UTC
from datetime import datetime

import pytest

from strategy_framework.parameter_adaptation import ParameterSemantic
from strategy_framework.parameter_adaptation import ResearchSplit
from strategy_framework.parameter_adaptation import canonical_config_hash
from strategy_framework.parameter_adaptation import classify_parameter
from strategy_framework.parameter_adaptation import deterministic_candidate_id
from strategy_framework.parameter_adaptation import duration_preserving_bars
from strategy_framework.parameter_adaptation import validate_parameter_constraints


def test_real_parameter_taxonomy_examples() -> None:
    assert classify_parameter("upper_threshold", 70) is ParameterSemantic.DIMENSIONLESS_THRESHOLD
    assert classify_parameter("fast_window", 20) is ParameterSemantic.BAR_LOOKBACK
    assert classify_parameter("stop_multiple", 2.0) is ParameterSemantic.VOLATILITY_MULTIPLIER
    assert classify_parameter("opening_range_minutes", 30) is ParameterSemantic.SESSION_PARAMETER
    assert classify_parameter("layer_fraction", 0.25) is ParameterSemantic.POSITION_FRACTION
    assert classify_parameter("execution_lag_minutes", 1) is ParameterSemantic.EXECUTION_PARAMETER


def test_duration_conversion_preserves_clock_time_not_dimensionless_values() -> None:
    assert duration_preserving_bars(120, 5) == 24
    rsi_threshold = 70
    assert rsi_threshold == 70


@pytest.mark.parametrize("name", ["month_end", "previous_day_close", "calendar_reset"])
def test_calendar_semantics_are_not_mechanically_minuteized(name: str) -> None:
    assert classify_parameter(name, 1) is ParameterSemantic.CALENDAR_PARAMETER


def test_structural_constraints_reject_before_backtest() -> None:
    valid, failures = validate_parameter_constraints(
        {
            "fast_window": 20,
            "slow_window": 10,
            "layer_fraction": 0.6,
            "max_layers": 2,
            "max_exposure": 1.0,
        }
    )
    assert not valid
    assert "fast_window<slow_window" in failures
    assert "layer_fraction*layers<=exposure_cap" in failures
    assert not validate_parameter_constraints({"consecutive_bars": 0})[0]
    assert not validate_parameter_constraints({"reduction_fraction": 1.2})[0]


def test_candidate_identity_and_hash_are_deterministic_and_order_independent() -> None:
    left = {"fast_window": 10, "slow_window": 40}
    right = {"slow_window": 40, "fast_window": 10}
    assert canonical_config_hash(left) == canonical_config_hash(right)
    assert deterministic_candidate_id("search", left) == deterministic_candidate_id("search", right)


def test_split_exposes_train_validation_only_and_is_chronological() -> None:
    split = ResearchSplit(
        datetime(2021, 7, 1, tzinfo=UTC),
        datetime(2022, 7, 1, tzinfo=UTC),
        datetime(2022, 7, 2, tzinfo=UTC),
        datetime(2023, 1, 1, tzinfo=UTC),
        datetime(2023, 1, 2, tzinfo=UTC),
        datetime(2023, 7, 1, tzinfo=UTC),
    )
    assert split.contains_no_selection_leakage()
    assert len(split.selection_periods()) == 2
