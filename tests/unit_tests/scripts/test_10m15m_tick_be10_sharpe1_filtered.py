from __future__ import annotations

import pandas as pd

from scripts.internal.build_10m15m_tick_be10_sharpe1_filtered import qualifies


def test_combined_filter_requires_both_absolute_thresholds_strictly():
    frame = pd.DataFrame({
        "Signed_BE_bps": [15.0, -15.0, 15.0, 5.0, -20.0, 10.0, 10.0001],
        "Sharpe": [1.4, -1.4, 0.8, 1.8, 1.2, 2.0, -1.0001],
    })
    assert qualifies(frame).tolist() == [True, True, False, False, True, False, True]


def test_combined_filter_preserves_signed_source_values():
    frame = pd.DataFrame({"Signed_BE_bps": [-20.0], "Sharpe": [-1.2]})
    before = frame.copy(deep=True)
    assert qualifies(frame).iloc[0]
    pd.testing.assert_frame_equal(frame, before)
