from __future__ import annotations

import json
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
OUTPUT = ROOT / "outputs" / "deliverables" / "phase7a_final_research_review"


def test_final_reconciliation_counts() -> None:
    summary = json.loads((OUTPUT / "phase7a_final_summary.json").read_text(encoding="utf-8"))
    counts = summary["counts"]
    assert counts["workbook_rows"] == 1715
    assert counts["workbook_executable_identities"] == 280
    assert counts["total_executable_identities"] == 344
    assert counts["independent_semantic_groups"] == 191


def test_evidence_ledger_is_one_row_per_semantic_group() -> None:
    ledger = pd.read_csv(OUTPUT / "phase7a_strategy_evidence_ledger.csv")
    assert len(ledger) == 191
    assert ledger.semantic_group_id.nunique() == 191
    assert ledger.final_research_disposition.notna().all()


def test_funnel_counts_are_authoritative() -> None:
    funnel = pd.read_csv(OUTPUT / "phase7a_research_funnel.csv").set_index("stage")
    assert funnel.loc["PHASE6A_QUALITY", "count"] == 28
    assert funnel.loc["PHASE6B", "count"] == 11
    assert funnel.loc["PHASE6C", "count"] == 7
    assert funnel.loc["PHASE6D", "count"] == 1
    assert funnel.loc["PHASE6E", "count"] == 0


def test_provenance_reconciles() -> None:
    provenance = pd.read_csv(OUTPUT / "phase7a_provenance_summary.csv")
    assert provenance.identities.sum() == 344
    assert provenance.independent_groups.sum() == 191
    assert provenance.Phase6B_conditional_candidates.sum() == 11
    assert provenance.Phase6C_broad_replication.sum() == 7
    assert provenance.Phase6D_survivors.sum() == 1
    assert set(provenance.provenance_tier) == {"P0_SOURCE_DIRECT", "P1_STANDARDIZED", "P2_DEFAULTED", "P3_MODELLED_LOW", "P4_MODELLED_MEDIUM"}


def test_method_corrections_are_complete() -> None:
    corrections = pd.read_csv(OUTPUT / "phase7a_method_corrections.csv")
    assert set(corrections.correction_id) == {f"{letter}_{name}" for letter, name in zip("ABCDEFGH", ["DIRECTION_MODEL", "RESULT_CARDINALITY", "EPISODE_COUNT", "SOURCE_TIMEFRAME", "TURNOVER_DISPLAY", "SIGNED_BE", "EXECUTION_MODEL", "SLIPPAGE"])}


def test_forward_result_is_exact_and_terminal() -> None:
    summary = json.loads((OUTPUT / "phase7a_final_summary.json").read_text(encoding="utf-8"))
    assert summary["forward_status"] == "FORWARD_WEAK"
    assert summary["phase6f_decision"] == "NO_FURTHER_AUTOMATIC_RESEARCH"
    assert abs(summary["forward_result"]["BTCUSDT"]["net_return"] + 0.17447169118538156) < 1e-12
    assert abs(summary["forward_result"]["ETHUSDT"]["net_return"] - 0.25778084728405615) < 1e-12
    assert abs(summary["forward_result"]["SOLUSDT"]["net_return"] + 0.290170880050079) < 1e-12


def test_no_new_experiments_or_deletions() -> None:
    summary = json.loads((OUTPUT / "phase7a_final_summary.json").read_text(encoding="utf-8"))
    validation = json.loads((OUTPUT / "phase7a_validation_summary.json").read_text(encoding="utf-8"))
    assert all(value == 0 for value in summary["new_experiments"].values())
    assert validation["deletions"] == 0
    assert validation["unexpected_protected_hash_changes"] == []


def test_required_artifacts_exist() -> None:
    required = {
        "phase7a_research_funnel.csv", "phase7a_strategy_evidence_ledger.csv",
        "phase7a_provenance_summary.csv", "phase7a_method_corrections.csv",
        "phase7a_metric_definitions.csv", "phase7a_execution_assumptions.csv",
        "phase7a_data_inventory_summary.csv", "phase7a_validation_ledger.csv",
        "phase7a_artifact_ledger.csv", "phase7a_archival_recommendations.csv",
        "phase7a_final_summary.json", "phase7a_final_research_review.html",
        "phase7a_validation_summary.json",
    }
    assert not [name for name in required if not (OUTPUT / name).is_file()]
    assert len(list((OUTPUT / "figures").glob("*.png"))) == 3
