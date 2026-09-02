from __future__ import annotations

import pandas as pd

from scripts.internal.build_boss_final_review import (
    breadth_table,
    timeframe_preference,
    validate_frozen_membership,
)


def frozen_fixture() -> pd.DataFrame:
    ten_levels = ["LEVEL_A_BROAD_PERSISTENT_ECONOMIC"] * 7 + [
        "LEVEL_B_MULTI_SYMBOL_PERSISTENT_ECONOMIC"
    ] * 4
    fifteen_levels = ["LEVEL_A_BROAD_PERSISTENT_ECONOMIC"] * 5 + [
        "LEVEL_B_MULTI_SYMBOL_PERSISTENT_ECONOMIC"
    ] * 8
    rows = [
        {"semantic_execution_hash": f"shared-{index}" if index < 10 else "10m-only",
         "timeframe": "10m", "shortlist_level": level}
        for index, level in enumerate(ten_levels)
    ] + [
        {"semantic_execution_hash": f"shared-{index}" if index < 10 else f"15m-only-{index}",
         "timeframe": "15m", "shortlist_level": level}
        for index, level in enumerate(fifteen_levels)
    ]
    return pd.DataFrame(rows)


def test_frozen_membership_reconciles_24_rows_14_groups() -> None:
    frame = frozen_fixture()
    assert len(frame) == 24
    assert frame.semantic_execution_hash.nunique() == 14
    validate_frozen_membership(frame)


def test_breadth_distinguishes_profitability_from_persistent_profitability() -> None:
    symbols = [f"S{i}" for i in range(9)]
    detail = pd.DataFrame(
        {
            "representative_strategy_id": ["s"] * 9,
            "equivalent_source_ids": ["s"] * 9,
            "semantic_execution_hash": ["h"] * 9,
            "timeframe": ["10m"] * 9,
            "candidate_level": ["LEVEL_A_BROAD_PERSISTENT_ECONOMIC"] * 9,
            "symbol": symbols,
            "persistent_flag": [True] * 5 + [False] * 4,
            "Return_BE_positive": [False, False] + [True] * 7,
            "Return_5bp_positive": [True] * 6 + [False] * 3,
            "Return": list(range(-4, 5)),
            "BE": list(range(-4, 5)),
            "Return_5bp": list(range(-4, 5)),
            "turnover_percent": [100.0] * 9,
            "nonflat_fraction_v2": [0.95] * 9,
            "median_directional_run_hours": [10.0] * 9,
            "P90_directional_run_hours": [20.0] * 9,
            "sign_switches_per_day": [0.1] * 9,
            "directional_bias_class": ["RELATIVELY_BALANCED"] * 9,
        }
    )
    # The production symbol order is irrelevant to the intersection counts.
    import scripts.internal.build_boss_final_review as module
    original = module.SYMBOLS
    module.SYMBOLS = tuple(symbols)
    try:
        result = breadth_table(detail).iloc[0]
    finally:
        module.SYMBOLS = original
    assert result.Return_BE_positive_symbol_count == 7
    assert result.persistent_Return_BE_positive_symbol_count == 3
    assert result["5bp_positive_symbol_count"] == 6
    assert result.persistent_5bp_positive_symbol_count == 5


def test_identical_timeframes_are_both_similar_not_artificially_preferred() -> None:
    values = pd.Series(
        {
            "persistent_symbol_count": 9,
            "persistent_Return_BE_positive_symbol_count": 3,
            "persistent_5bp_positive_symbol_count": 3,
            "median_BE": -4.0,
            "median_turnover_pct": 80_000.0,
            "median_median_run_hours": 286.0,
            "median_switches_per_day": 0.06,
        }
    )
    status, _ = timeframe_preference(values, values.copy())
    assert status == "BOTH_SIMILAR"
