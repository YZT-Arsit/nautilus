from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from scripts.internal.run_phase6c_conditional_replication import (
    CANDIDATES,
    COMMON_END,
    COMMON_START,
    PHASE4C_OVERLAP,
    REPLICATION,
    label_candidate,
)


ROOT = Path(__file__).resolve().parents[3]
OUTPUT = ROOT / "outputs/baseline_evaluation/phase6c"


def test_phase6c_freezes_candidates_symbols_and_window() -> None:
    assert len(CANDIDATES) == 11
    assert len(set(CANDIDATES)) == 11
    assert REPLICATION == ("ETHUSDT", "SOLUSDT")
    assert PHASE4C_OVERLAP == {"xlsx_s1_0003", "xlsx_s1_0453", "xlsx_s2_0435"}
    assert COMMON_START == "2024-07-01"
    assert COMMON_END == "2026-06-30"


def _group(eth_return: float, sol_return: float, eth_cost: float, sol_cost: float) -> pd.DataFrame:
    rows = []
    for symbol, ret, cost in (("BTCUSDT", 1.0, 0.9), ("ETHUSDT", eth_return, eth_cost), ("SOLUSDT", sol_return, sol_cost)):
        rows.append({"representative_strategy_id":"x", "symbol":symbol, "Return":ret, "BE":ret, "Return_0_10":cost, "episode_BE_median":1.0, "episode_BE_positive_fraction":.6, "Return_without_top5pct":-1.0, "BE_without_top5pct":-1.0, "Episode_Count":10, "semantic_group_id":"g", "provenance_tier":"P1", "phase6a_quality_tier":"A", "phase6b_label":"BROAD_BUT_LOW_MARGIN"})
    return pd.DataFrame(rows)


def test_phase6c_replication_labels_are_deterministic() -> None:
    assert label_candidate(_group(1, 2, .9, 1.9))["replication_label"] == "CONDITIONAL_BROAD_REPLICATION"
    assert label_candidate(_group(1, 2, -.1, -.2))["replication_label"] == "BOTH_MARKETS_POSITIVE_COST_UNSUPPORTED"
    assert label_candidate(_group(1, -2, .9, -2.1))["replication_label"] == "PARTIAL_REPLICATION"
    assert label_candidate(_group(-1, -2, -.9, -2.1))["replication_label"] == "BTC_SPECIFIC_OR_NONREPLICATING"


def test_phase6c_terminal_outputs_and_invariants() -> None:
    path = OUTPUT / "phase6c_validation_summary.json"
    if not path.is_file():
        return
    validation = json.loads(path.read_text(encoding="utf-8"))
    assert validation["status"] == "PASSED"
    assert validation["candidate_groups"] == 11
    assert validation["logical_nonBTC_replication_cases"] == 22
    assert validation["phase4c_reused_cases"] == 6
    assert validation["new_BTC_backtests"] == 0
    assert validation["parameter_optimization_runs"] == 0
    assert validation["target_market_parameter_changes"] == 0
    assert validation["new_semantic_policies"] == 0
    assert validation["production_configs_created"] == 0
    assert validation["protected_artifact_changes"] == 0
    assert validation["maximum_BE_crossing_residual"] <= 1e-10
    assert validation["maximum_episode_BE_residual"] <= 1e-10
    master = pd.read_csv(OUTPUT / "phase6c_cross_symbol_master.csv")
    assert len(master) == 33
    assert master.groupby("representative_strategy_id").symbol.nunique().eq(3).all()
    assert master.groupby("representative_strategy_id").strategy_parameter_hash.nunique().eq(1).all()
    assert master.groupby("representative_strategy_id").strategy_ir_hash.nunique().eq(1).all()
