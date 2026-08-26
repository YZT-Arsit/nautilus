from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.internal.build_phase4a_baseline_evaluation import TOLERANCE
from scripts.internal.build_phase4a_baseline_evaluation import baseline_tier
from scripts.internal.build_phase4a_baseline_evaluation import directional_diagnostic
from scripts.internal.build_phase4a_baseline_evaluation import drawdown


def test_arithmetic_drawdown_and_directional_diagnostic() -> None:
    assert drawdown(np.array([0.10, -0.20, 0.05])) < 0
    assert directional_diagnostic(0.1, -0.1) == "DIRECTIONALLY_CONSISTENT"
    assert directional_diagnostic(-0.1, -0.2) == "BOTH_NEGATIVE"


def test_baseline_tier_a_and_integrity_precedence() -> None:
    row = {
        "return_realistic_lag": 0.1, "be_realistic_lag": 1.0, "return_positive_majority_periods": True,
        "be_positive_majority_periods": True, "baseline_single_period_dominated": False,
        "baseline_lopo_return_robust": True, "completed_episode_count": 5, "sign_flips_under_lag": False,
        "episode_be_positive_fraction": 0.6, "positive_at_lag0_only": False,
    }
    assert baseline_tier(row, True)[0] == "A"
    assert baseline_tier(row, False)[0] == "F"


def test_canonical_phase4a_outputs_when_present() -> None:
    root = Path("outputs/baseline_evaluation/phase4a")
    if not root.is_dir():
        return
    universe = pd.read_csv(root / "phase4a_strategy_universe.csv")
    master = pd.read_csv(root / "phase4a_strategy_master.csv")
    periods = pd.read_csv(root / "phase4a_period_robustness.csv")
    coverage = pd.read_csv(root / "phase4a_result_coverage.csv")
    summary = json.loads((root / "phase4a_validation_summary.json").read_text(encoding="utf-8"))
    assert len(universe) == len(master) == len(coverage) == 195
    assert universe.source_group.value_counts().to_dict() == {"WORKBOOK": 131, "PRE_WORKBOOK": 64}
    assert periods.groupby("strategy_id").size().eq(10).all()
    assert master.baseline_only_selection.eq(True).all()
    assert master.baseline_tier_reasons.fillna("").str.len().gt(0).all()
    assert master.result_integrity_passed.eq(True).all()
    assert master.be_formula_residual.abs().dropna().max() <= TOLERANCE
    assert master.period_return_residual.abs().max() <= TOLERANCE
    allowed_turnover_error = np.maximum(TOLERANCE, master.turnover_realistic_lag.abs() * 2e-12)
    assert (master.period_turnover_residual.abs() <= allowed_turnover_error).all()
    assert summary["new_parameter_search_runs"] == 0
    assert summary["new_five_year_backtests"] == 0
    assert summary["production_configs_generated"] == 0
    assert summary["protected_hash_changes"] == 0
