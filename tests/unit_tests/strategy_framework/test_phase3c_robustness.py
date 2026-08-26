from __future__ import annotations

import json
import math
from pathlib import Path

import pandas as pd

from scripts.internal.build_phase3c_robustness import classify_tier
from scripts.internal.build_phase3c_robustness import compare
from scripts.internal.build_phase3c_robustness import dominant_fold
from scripts.internal.build_phase3c_robustness import lofo
from scripts.internal.build_phase3c_robustness import stability_severity


def test_comparison_and_lofo_are_deterministic() -> None:
    assert compare(1.0, 1.0 + 1e-13) == "EQUAL"
    assert compare(1.0, 0.0) == "BETTER"
    assert compare(-1.0, 0.0) == "WORSE"
    assert compare(math.nan, 0.0) == "NOT_COMPARABLE"
    result = lofo([1.0, 1.0, 1.0, 1.0, 1.0, 1.0, -0.5])
    assert result["positive"] == 7
    assert result["robust"] is True


def test_dominant_fold_and_disappearing_improvement() -> None:
    result = dominant_fold([0.1, -0.02, -0.02, -0.02, -0.02, -0.01, -0.005], [f"wf{i}" for i in range(7)])
    assert result["single_fold_dominated"] is True
    assert result["improvement_disappears_without_best_fold"] is True
    assert result["dominant_fold"] == "wf0"


def test_stability_and_tier_precedence() -> None:
    flags = {
        "full_range_drift": True,
        "isolated_validation_optimum": True,
        "joint_config_transition_rate": 0.2,
        "unique_selected_config_count": 3,
    }
    assert stability_severity(flags) == "HIGHLY_UNSTABLE"
    row = {
        "absolute_return_positive": True,
        "absolute_be_positive": True,
        "return_beats_baseline": True,
        "be_beats_baseline": True,
        "return_improves_majority_folds": True,
        "full_range_drift": True,
        "single_fold_dominated": True,
        "isolated_validation_optimum": False,
        "lofo_robust_return": True,
        "return_equals_baseline": False,
    }
    assert classify_tier(row)[0] == "F"


def test_tier_a_requires_all_contract_conditions() -> None:
    row = {
        "absolute_return_positive": True,
        "absolute_be_positive": True,
        "return_beats_baseline": True,
        "be_beats_baseline": True,
        "return_improves_majority_folds": True,
        "full_range_drift": False,
        "single_fold_dominated": False,
        "isolated_validation_optimum": False,
        "lofo_robust_return": True,
        "return_equals_baseline": False,
    }
    assert classify_tier(row)[0] == "A"


def test_canonical_phase3c_outputs_reconcile_when_present() -> None:
    root = Path("outputs/parameter_search/phase3c")
    if not root.is_dir():
        return
    master = pd.read_csv(root / "phase3c_master_robustness_table.csv")
    folds = pd.read_csv(root / "phase3c_fold_consistency.csv")
    summary = json.loads((root / "phase3c_validation_summary.json").read_text(encoding="utf-8"))
    assert len(master) == master.search_id.nunique() == 65
    assert master.wave.value_counts().to_dict() == {3: 35, 1: 23, 5: 7}
    assert len(folds) == 65 * 7
    assert folds.groupby("search_id").size().eq(7).all()
    assert master.tier_reasons.fillna("").str.len().gt(0).all()
    assert int(master.return_beats_baseline.sum()) == 38
    assert int(master.be_beats_baseline.sum()) == 29
    assert int(master.full_range_drift.sum()) == 37
    assert int(master.single_fold_dominated.sum()) == 47
    assert int(master.isolated_validation_optimum.sum()) == 28
    assert summary["protected_hash_changes"] == 0
    assert summary["new_parameter_search_backtests"] == 0
    assert summary["test_informed_reselection"] == 0
    assert summary["production_configs_generated"] == 0
