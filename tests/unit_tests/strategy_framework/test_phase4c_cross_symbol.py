from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.internal.build_phase4c_cross_symbol import replication_label
from scripts.internal.prepare_phase4c_cross_symbol import CANDIDATES
from scripts.internal.prepare_phase4c_cross_symbol import COMMON_END_EXCLUSIVE
from scripts.internal.prepare_phase4c_cross_symbol import COMMON_START
from scripts.internal.prepare_phase4c_cross_symbol import REPLICATION
from scripts.internal.run_constant_notional_overlay import calculate_overlay


ROOT = Path(__file__).resolve().parents[3]
OUTPUT = ROOT / "outputs/baseline_evaluation/phase4c"
RUNS = ROOT / "outputs/batches/phase4c_cross_symbol"


def test_phase4c_candidate_and_universe_freeze() -> None:
    assert len(CANDIDATES) == 6
    assert len(set(CANDIDATES)) == 6
    assert REPLICATION == ("ETHUSDT", "SOLUSDT")
    assert COMMON_START == "2024-07-01"
    assert COMMON_END_EXCLUSIVE == "2026-06-30"
    if (OUTPUT / "phase4c_compute_plan.json").is_file():
        plan = json.loads((OUTPUT / "phase4c_compute_plan.json").read_text(encoding="utf-8"))
        assert plan["status"] == "FROZEN_PRE_PERFORMANCE"
        assert plan["primary_cases"] == 18
        assert plan["new_parameter_searches"] == 0
        assert plan["symbol_specific_parameter_changes"] == 0


def test_replication_classification_is_descriptive_and_deterministic() -> None:
    reference = pd.Series({"RETURN_AND_BE_POSITIVE": True})
    both = pd.DataFrame({"RETURN_AND_BE_POSITIVE": [True, True]})
    one = pd.DataFrame({"RETURN_AND_BE_POSITIVE": [True, False]})
    none = pd.DataFrame({"RETURN_AND_BE_POSITIVE": [False, False]})
    assert replication_label(reference, both) == "BROAD_REPLICATION"
    assert replication_label(reference, one) == "PARTIAL_REPLICATION"
    assert replication_label(reference, none) == "BTC_SPECIFIC"
    assert replication_label(pd.Series({"RETURN_AND_BE_POSITIVE": False}), none) == "CROSS_SYMBOL_NEGATIVE"


def test_strict_constant_notional_is_price_scale_invariant() -> None:
    times = np.array([1, 2, 3], dtype=np.int64)
    funding = pd.DataFrame(columns=["event_time_ns", "mark_price", "funding_rate"])
    for prices in (np.array([100.0, 101.0, 102.0]), np.array([10_000.0, 10_100.0, 10_200.0])):
        equity = pd.DataFrame({"event_time_ns": times, "close": prices, "position": [1, 1, 1]})
        result, summary = calculate_overlay(
            equity, funding, prices, notional_usdt=100_000.0, slippage_bps=0,
            vip9_fee_bps=1.7, vip0_fee_bps=5.0, position_policy="strict_constant_notional",
        )
        assert np.allclose(result.boundary_notional_usdt, 100_000.0)
        assert summary["max_boundary_notional_error_usdt"] < 1e-8


def test_phase4c_terminal_artifacts_and_financial_identities() -> None:
    result_path = OUTPUT / "phase4c_cross_symbol_results.csv"
    if not result_path.is_file():
        return
    results = pd.read_csv(result_path)
    assert len(results) == 18
    assert results.groupby("representative_strategy_id").symbol.nunique().eq(3).all()
    assert results.groupby("representative_strategy_id").strategy_config_hash.nunique().eq(1).all()
    assert results.be_formula_residual.max() <= 1e-10
    assert results.episode_be_formula_residual.max() <= 1e-10
    assert results.period_return_residual.max() <= 1e-10
    assert results.period_turnover_residual.max() <= 1e-8
    assert results.semantic_parameter_changes.sum() == 0
    validation = json.loads((OUTPUT / "phase4c_validation_summary.json").read_text(encoding="utf-8"))
    assert validation["status"] == "PASSED"
    assert validation["protected_artifact_changes"] == 0
    assert validation["parameter_search_runs"] == 0
    assert validation["production_configs_created"] == 0


def test_all_run_cases_have_machine_results() -> None:
    if not RUNS.is_dir() or not (RUNS / "phase4c_run_validation.json").is_file():
        return
    manifests = list(RUNS.glob("*/xlsx_*/**/summary.json"))
    assert len(manifests) == 18
    for summary_path in manifests:
        parent = summary_path.parent
        assert (parent / "timeseries.parquet").is_file()
        assert (parent / "per_trade_break_even.csv").is_file()
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        assert summary["symbol_specific_parameter_changes"] == 0
        assert summary["premium"] == "INCLUDED"
        assert summary["direction"] == "ORIGINAL"
