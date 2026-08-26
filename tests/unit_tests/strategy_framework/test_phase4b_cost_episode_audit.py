from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.internal.build_phase4b_cost_episode_audit import COST_GRID
from scripts.internal.build_phase4b_cost_episode_audit import cost_adjusted_increments
from scripts.internal.build_phase4b_cost_episode_audit import exact_be
from scripts.internal.build_phase4b_cost_episode_audit import remove_top_episodes


def test_cost_grid_and_break_even_identity() -> None:
    assert COST_GRID == (0.0, 0.05, 0.10, 0.20, 0.30, 0.50, 1.0, 2.0, 5.0)
    gross=np.array([0.02,-0.01,0.03]); turnover=np.array([1.0,2.0,1.0]); be=exact_be(float(gross.sum()),float(turnover.sum()))
    assert abs(cost_adjusted_increments(gross,turnover,be).sum()) < 1e-14
    np.testing.assert_array_equal(cost_adjusted_increments(gross,turnover,0),gross)


def test_remove_top_episode_removes_matching_return_and_turnover() -> None:
    frame=pd.DataFrame({"delta_gross_return":[.3,.1,-.2],"delta_turnover":[3.,2.,1.]})
    result,turnover,be=remove_top_episodes(frame,1)
    assert result == -.1 and turnover == 3.
    assert abs(result-turnover*be/10_000)<1e-14


def test_canonical_phase4b_outputs_when_present() -> None:
    root=Path("outputs/baseline_evaluation/phase4b")
    if not root.is_dir(): return
    scope=pd.read_csv(root/"phase4b_strategy_scope.csv"); stress=pd.read_csv(root/"phase4b_cost_stress.csv"); margin=pd.read_csv(root/"phase4b_episode_cost_margin.csv"); concentration=pd.read_csv(root/"phase4b_episode_concentration.csv"); summary=json.loads((root/"phase4b_validation_summary.json").read_text())
    assert len(scope)==scope.semantic_group_id.nunique()==13
    assert stress.groupby("semantic_group_id").size().eq(len(COST_GRID)).all()
    assert len(margin)==len(concentration)==13
    assert summary["new_strategy_backtests"]==summary["new_parameter_searches"]==summary["production_configs_generated"]==0
    assert summary["protected_hash_changes"]==0
    assert summary["maximum_be_zero_return_residual"] <= 1e-9
    assert summary["maximum_cost0_return_residual"] <= 1e-9
    assert summary["maximum_cost0_mdd_residual"] <= 1e-9
    assert summary["maximum_episode_removal_be_residual"] <= 1e-9
