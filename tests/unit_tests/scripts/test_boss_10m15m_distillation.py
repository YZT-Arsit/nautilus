from __future__ import annotations

import pandas as pd

from scripts.internal.build_boss_10m15m_distillation import (
    classify_level,
    collapse_semantic,
    select_top,
    timeframe_paths,
)


def test_shortlist_levels_keep_persistence_and_economics_separate() -> None:
    assert classify_level(5, 5, False).startswith("LEVEL_A")
    assert classify_level(3, 3, False).startswith("LEVEL_B")
    assert classify_level(9, 2, False).startswith("LEVEL_C")
    assert classify_level(2, 9, False).startswith("LEVEL_C")


def test_semantic_collapse_preserves_source_ids_and_preperformance_key() -> None:
    frame = pd.DataFrame(
        {
            "strategy_id": ["s2", "s1"],
            "timeframe": ["10m", "10m"],
            "semantic_execution_hash": ["hash", "hash"],
            "representative_strategy_id": ["s1", "s1"],
            "persistent_symbol_count": [5, 5],
            "persistent_and_Return_BE_positive_symbols": [4, 4],
            "persistent_and_5bp_positive_symbols": [3, 3],
            "all_Return_BE_positive_symbols": [5, 5],
            "all_5bp_positive_symbols": [4, 4],
            "shortlist_level": ["LEVEL_A_BROAD_PERSISTENT_ECONOMIC"] * 2,
            "median_BE": [3.0, 3.0],
            "median_Turnover_pct": [200.0, 200.0],
        }
    )
    result = collapse_semantic(frame)
    assert len(result) == 1
    assert result.iloc[0].representative_strategy_id == "s1"
    assert result.iloc[0].equivalent_source_ids == "s1;s2"
    assert result.iloc[0].independence_contract == "pre-performance semantic_execution_hash"


def test_transparent_top_sort_uses_cost_breadth_before_return() -> None:
    frame = pd.DataFrame(
        {
            "representative_strategy_id": ["high_return", "broad_cost"],
            "timeframe": ["10m", "15m"],
            "shortlist_level": ["LEVEL_A_BROAD_PERSISTENT_ECONOMIC"] * 2,
            "persistent_and_5bp_positive_symbols": [1, 4],
            "persistent_and_Return_BE_positive_symbols": [8, 5],
            "all_5bp_positive_symbols": [1, 4],
            "all_Return_BE_positive_symbols": [8, 5],
            "persistent_symbol_count": [9, 5],
            "median_BE": [20.0, 2.0],
            "median_Turnover_pct": [100.0, 200.0],
        }
    )
    result = select_top(frame)
    assert result.iloc[0].representative_strategy_id == "broad_cost"
    assert "no score" in result.iloc[0].ordering_contract


def test_timeframe_path_joint_flag_requires_all_three_dimensions() -> None:
    rows = []
    for timeframe, run, switches, turnover, be in (
        ("1m", 1.0, 4.0, 400.0, 1.0),
        ("5m", 2.0, 3.0, 300.0, 1.5),
        ("10m", 3.0, 2.0, 250.0, 2.0),
        ("15m", 4.0, 1.0, 450.0, 3.0),
    ):
        rows.append(
            {
                "strategy_id": "s", "symbol": "BTCUSDT", "timeframe": timeframe,
                "Return": 0.1, "BE": be, "Return_5bp": 0.0,
                "turnover_percent": turnover, "nonflat_fraction_v2": 0.95,
                "median_directional_run_hours": run, "sign_switches_per_day": switches,
            }
        )
    result = timeframe_paths(pd.DataFrame(rows), {"s"}).iloc[0]
    assert result["10m_JOINT_IMPROVEMENT"]
    assert result["15m_PERSISTENCE_IMPROVED"]
    assert not result["15m_JOINT_IMPROVEMENT"]
