from __future__ import annotations

import math

import numpy as np
import pandas as pd

from scripts.internal.build_10m15m_tick_be_sharpe_review import daily_sharpe
from scripts.internal.build_10m15m_tick_be_sharpe_review import selection_class


def test_daily_sharpe_uses_utc_daily_arithmetic_increments_and_sample_sd():
    timestamps = pd.to_datetime([
        "2026-01-01T00:00:00Z", "2026-01-01T12:00:00Z",
        "2026-01-02T00:00:00Z", "2026-01-02T12:00:00Z",
        "2026-01-03T00:00:00Z", "2026-01-03T23:59:00Z",
    ])
    frame = pd.DataFrame({
        "event_time_ns": timestamps.as_unit("ns").asi8,
        "cumulative_return_with_premium": [0.00, 0.004, 0.01, 0.015, 0.03, 0.00],
    })
    actual, count = daily_sharpe(frame)
    daily = np.array([0.01, 0.02, -0.03])
    expected = daily.mean() / daily.std(ddof=1) * math.sqrt(365.0)
    assert count == 3
    assert actual == expected


def test_daily_sharpe_zero_std_is_nan():
    timestamps = pd.to_datetime([
        "2026-01-01T00:00:00Z", "2026-01-01T12:00:00Z",
        "2026-01-02T00:00:00Z", "2026-01-02T23:59:00Z",
    ])
    frame = pd.DataFrame({
        "event_time_ns": timestamps.as_unit("ns").asi8,
        "cumulative_return_with_premium": [0.0, 0.004, 0.01, 0.02],
    })
    actual, count = daily_sharpe(frame)
    assert count == 2
    assert np.isnan(actual)


def test_selection_classes_are_transparent_and_deterministic():
    assert selection_class(True, False, False) == "BE_SELECTED_ONLY"
    assert selection_class(False, True, False) == "SHARPE_SELECTED_ONLY"
    assert selection_class(True, True, False) == "BE_AND_SHARPE_SELECTED"
    assert selection_class(False, False, True) == "PREVIOUS_SELECTED_ONLY"
    assert selection_class(True, True, True) == "PREVIOUS_PLUS_BE_AND_SHARPE"
