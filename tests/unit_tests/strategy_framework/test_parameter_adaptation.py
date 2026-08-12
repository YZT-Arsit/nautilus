from datetime import UTC, datetime

import pytest

from strategy_framework.parameter_adaptation import (
    ResearchSplit,
    ValidationScore,
    duration_preserving_bars,
    logarithmic_integer_candidates,
    ordered_window_pairs,
    select_on_validation,
)


def test_duration_conversion_is_not_silently_capped() -> None:
    assert duration_preserving_bars(20 * 24 * 60, 1) == 28_800


def test_candidate_pairs_preserve_short_long_order() -> None:
    candidates = logarithmic_integer_candidates(20, lower=5, upper=100)
    pairs = ordered_window_pairs(candidates, candidates)
    assert pairs and all(short < long for short, long in pairs)


def test_research_split_rejects_overlap_or_reversed_boundaries() -> None:
    dt = lambda day: datetime(2024, 1, day, tzinfo=UTC)
    split = ResearchSplit(dt(1), dt(5), dt(6), dt(10), dt(11), dt(15))
    assert split.selection_periods() == ((dt(1), dt(5)), (dt(6), dt(10)))
    with pytest.raises(ValueError):
        ResearchSplit(dt(1), dt(6), dt(5), dt(10), dt(11), dt(15))


def test_selection_uses_validation_and_has_no_test_score_input() -> None:
    candidates = (
        ValidationScore((("window", 10),), train_score=9.0, validation_score=1.0),
        ValidationScore((("window", 20),), train_score=2.0, validation_score=3.0),
    )
    assert select_on_validation(candidates).parameters == (("window", 20),)
