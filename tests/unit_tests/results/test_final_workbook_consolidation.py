from __future__ import annotations

import json
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
FINAL = ROOT / "outputs/deliverables/all_converted_workbook_strategies"
AUDIT = ROOT / "outputs/internal_audit/final_workbook_consolidation"


def validation() -> dict:
    return json.loads((AUDIT / "final_validation_summary.json").read_text(encoding="utf-8"))


def test_strategy_and_output_cardinality() -> None:
    report = validation()
    assert report["executable_workbook_identities"] == 280
    assert report["strategies_exported"] == 280
    assert report["actual_png"] == report["expected_png"] == 1120
    assert report["actual_csv"] == report["expected_csv"] == 281


def test_master_index_has_exactly_one_row_per_strategy() -> None:
    master = pd.read_csv(FINAL / "all_converted_workbook_strategies.csv")
    index = master.loc[master.record_type == "STRATEGY_INDEX"]
    assert len(index) == 280
    assert index.strategy_id.nunique() == 280


def test_only_normal_direction_and_two_lags() -> None:
    master = pd.read_csv(FINAL / "all_converted_workbook_strategies.csv")
    results = master.loc[master.record_type == "BASELINE_RESULT"]
    assert len(results) == 280 * 2 * 2
    assert set(results.premium_mode) == {"included", "excluded"}
    assert (results.groupby("strategy_id").lag.nunique() == 2).all()


def test_signed_be_and_turnover_display_identities() -> None:
    master = pd.read_csv(FINAL / "all_converted_workbook_strategies.csv")
    results = master.loc[master.record_type == "BASELINE_RESULT"].copy()
    assert ((results.turnover_percent - results.turnover_raw * 100).abs() < 1e-8).all()
    nonzero = results.turnover_raw > 0
    residual = results.loc[nonzero, "return_1x"] - results.loc[nonzero, "turnover_raw"] * results.loc[nonzero, "signed_be_bps"] / 10000
    assert residual.abs().max() < 1e-8


def test_episode_counts_and_histograms_reconcile() -> None:
    report = validation()
    assert report["histogram_accounting_failures"] == 0
    assert report["maximum_episode_be_residual"] < 1e-8
    assert report["maximum_premium_identity_residual"] < 1e-8


def test_final_folder_extension_allowlist() -> None:
    extensions = {path.suffix.lower() for path in FINAL.rglob("*") if path.is_file()}
    assert extensions == {".csv", ".png"}


def test_file_index_targets_exist() -> None:
    master = pd.read_csv(FINAL / "all_converted_workbook_strategies.csv")
    files = master.loc[master.record_type == "FILE_INDEX"]
    for column in files.columns[files.columns.str.endswith(("csv", "performance", "diagnostics"))]:
        assert files[column].dropna().map(lambda value: (FINAL / value).is_file()).all()


def test_sources_and_historical_semantics_unchanged() -> None:
    report = validation()
    assert report["protected_source_hash_changes"] == 0
    for key in (
        "new_strategy_registrations", "strategy_semantic_changes", "parameter_optimization",
        "new_semantic_policies", "new_cross_symbol_research", "new_forward_research",
    ):
        assert report[key] == 0
