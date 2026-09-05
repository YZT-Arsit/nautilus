from __future__ import annotations

import numpy as np
import pandas as pd

from scripts.internal.finalize_l1_maker_policy_study import episode_rows


def test_episode_attribution_separates_partial_and_complete_miss() -> None:
    timestamps = np.arange(6, dtype=np.int64) * 60_000_000_000
    path = pd.DataFrame(
        {
            "timestamp_ns": timestamps,
            "target_position": [1.0, 1.0, 0.0, -1.0, -1.0, 0.0],
            "actual_position": [0.0, 0.5, 0.5, 0.5, 0.5, 0.5],
            "cumulative_return_gross": [0.0, 0.01, 0.01, 0.0, -0.01, -0.01],
            "first_tick_cumulative_return": [0.02, 0.04, 0.04, 0.02, 0.00, 0.00],
        }
    )
    rows = episode_rows("S", "BTCUSDT", "P", path)
    assert [row["episode_type"] for row in rows] == ["PARTIALLY_FILLED", "COMPLETELY_MISSED"]
    assert rows[0]["fill_fraction"] == 0.5
    assert rows[1]["fill_fraction"] == 0.0


def test_episode_attribution_does_not_emit_fully_achieved_target() -> None:
    path = pd.DataFrame(
        {
            "timestamp_ns": np.arange(3, dtype=np.int64) * 60_000_000_000,
            "target_position": [1.0, 1.0, 0.0],
            "actual_position": [1.0, 1.0, 1.0],
            "cumulative_return_gross": [0.0, 0.1, 0.1],
            "first_tick_cumulative_return": [0.0, 0.1, 0.1],
        }
    )
    assert episode_rows("S", "BTCUSDT", "P", path) == []
