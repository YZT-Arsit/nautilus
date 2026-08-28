from __future__ import annotations

import math

import pandas as pd

from scripts.internal.build_phase6a_expanded_screen import baseline_tier
from scripts.internal.build_phase6a_expanded_screen import episode_evidence
from scripts.internal.build_phase6a_expanded_screen import provenance_tier


def _tier_row(**updates):
    row = {
        "Return": 0.2,
        "BE": 2.0,
        "Episode_Count": 12,
        "RETURN_POSITIVE_MAJORITY_PERIODS": True,
        "BE_POSITIVE_MAJORITY_PERIODS": True,
        "BASELINE_SINGLE_PERIOD_DOMINATED": False,
        "BASELINE_LOPO_RETURN_ROBUST": True,
        "WINNER_CONCENTRATED": False,
        "return_lag_sign_flip": False,
        "BE_lag_sign_flip": False,
        "lag0_return": 0.2,
        "episode_BE_median": 1.0,
    }
    row.update(updates)
    return row


def test_provenance_tiers_preserve_model_risk():
    assert provenance_tier("SOURCE_EXACT", "PRE_PHASE5") == "P0_SOURCE_DIRECT"
    assert provenance_tier("STANDARD_CONTRACT_RESOLVED", "PHASE5C") == "P1_STANDARDIZED"
    assert provenance_tier("PARAMETER_DEFAULTED", "PHASE5A") == "P2_DEFAULTED"
    assert provenance_tier("MODELLED_BASELINE_INTERPRETATION", "PHASE5C") == "P3_MODELLED_LOW"
    assert provenance_tier("MODELLED_BASELINE_INTERPRETATION", "PHASE5F") == "P4_MODELLED_MEDIUM"


def test_quality_a_contract_is_transparent():
    tier, *_ = baseline_tier(_tier_row(), True)
    assert tier == "A"
    tier, *_ = baseline_tier(_tier_row(BASELINE_LOPO_RETURN_ROBUST=False), True)
    assert tier == "B"
    tier, *_ = baseline_tier(_tier_row(Return=-0.1, BE=-1.0, lag0_return=-0.1), True)
    assert tier == "E"
    tier, *_ = baseline_tier(_tier_row(), False)
    assert tier == "F"


def test_winner_concentration_matches_phase4b_rule():
    frame = pd.DataFrame(
        {
            "break_even_bps": [100.0, -5.0, -5.0, -5.0],
            "delta_gross_return": [1.0, -0.1, -0.1, -0.1],
            "delta_turnover": [100.0, 200.0, 200.0, 200.0],
            "start_timestamp": pd.date_range("2024-01-01", periods=4, tz="UTC"),
            "completion_timestamp": pd.date_range("2024-01-02", periods=4, tz="UTC"),
        }
    )
    result = episode_evidence(frame)
    assert result["WINNER_CONCENTRATED"] is True
    assert result["return_without_top5pct"] < 0
    assert math.isfinite(result["BE_without_top5pct"])
