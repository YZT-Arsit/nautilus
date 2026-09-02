from __future__ import annotations

import numpy as np
import pandas as pd

from scripts.internal.build_boss_persistence_v2 import directional_persistence_metrics


MINUTE_NS = 60 * 1_000_000_000


def sampled(states: list[int], run_minutes: list[int]) -> pd.DataFrame:
    assert len(states) == len(run_minutes)
    times = []
    values = []
    current = 0
    for state, minutes in zip(states, run_minutes):
        times.extend([current, current + (minutes - 1) * MINUTE_NS])
        values.extend([state, state])
        current += minutes * MINUTE_NS
    frame = pd.DataFrame({"event_time_ns": times, "executed_position": values})
    return frame.drop_duplicates("event_time_ns", keep="last").sort_values("event_time_ns")


def test_always_in_market_high_switching_is_not_directionally_persistent() -> None:
    frame = sampled([1, -1] * 100, [60] * 200)
    result = directional_persistence_metrics(frame)
    assert result["nonflat_fraction_v2"] == 1.0
    assert result["always_in_market"]
    assert not result["directionally_persistent"]
    assert result["persistence_class_v2"] == "ALWAYS_IN_MARKET_HIGH_SWITCHING_OR_SHORT_RUNS"
    assert result["direct_reversal_count_v2"] == 199
    assert result["sign_switch_count_v2"] == 199


def test_long_runs_and_low_switch_rate_are_directionally_persistent() -> None:
    frame = sampled([1, -1, 1], [3 * 1440, 4 * 1440, 3 * 1440])
    result = directional_persistence_metrics(frame)
    assert result["nonflat_fraction_v2"] == 1.0
    assert result["median_directional_run_duration"] == 3 * 86_400
    assert result["sign_switch_count_v2"] == 2
    assert result["sign_switches_per_day"] == 0.2
    assert result["directionally_persistent"]


def test_flat_mediated_side_change_is_switch_but_not_direct_reversal() -> None:
    frame = sampled([1, 0, -1], [1440, 120, 1440])
    result = directional_persistence_metrics(frame)
    assert result["directional_run_count"] == 2
    assert result["sign_switch_count_v2"] == 1
    assert result["direct_reversal_count_v2"] == 0
    assert np.isclose(
        result["long_fraction_v2"] + result["short_fraction_v2"] + result["flat_fraction_v2"],
        1.0,
    )
