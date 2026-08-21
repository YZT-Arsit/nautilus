from __future__ import annotations

import csv
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
AUDIT = ROOT / "outputs/internal_audit/strategy_workbook"


def rows(name: str) -> list[dict[str, str]]:
    with (AUDIT / name).open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def test_all_strategies_modules_parameters_and_targets_are_reconciled() -> None:
    summary = json.loads((AUDIT / "phase3a_validation_summary.json").read_text())
    assert summary["standalone_strategies_audited"] == 131
    assert summary["modules_audited"] == 72
    assert summary["strategy_target_decisions"] == 131 * 4
    assert summary["unclassified_parameters"] == 0
    assert sum(summary["parameter_reconciliation"].values()) == summary["parameter_instances"]


def test_every_ready_search_has_baseline_bounded_space_and_no_forbidden_dimensions() -> None:
    manifest = rows("parameter_search_manifest.csv")
    assert manifest
    ready = [item for item in manifest if item["status"] == "READY"]
    assert ready
    for item in ready:
        baseline = json.loads(item["baseline_candidate"])
        assert baseline["candidate_id"]
        assert baseline["config_hash"]
        assert int(item["estimated_candidate_count"]) <= 128
        assert item["target_timeframe"] == "1m"
        text = item["searchable_parameters"].lower()
        assert "lag" not in text
        assert "premium" not in text
        assert "direction" not in text
    assert all(not item["baseline_candidate"] for item in manifest if item["status"] == "UNSAFE")


def test_walk_forward_is_expanding_chronological_and_test_is_never_selection_input() -> None:
    protocol = json.loads((AUDIT / "phase3a_walk_forward_protocol.json").read_text())
    assert protocol["window_type"] == "EXPANDING_WINDOW"
    assert len(protocol["folds"]) == 7
    for fold in protocol["folds"]:
        assert fold["train"]["end_exclusive"] == fold["validation"]["start_inclusive"]
        assert fold["validation"]["end_exclusive"] == fold["test"]["start_inclusive"]
        assert fold["train"]["start_inclusive"] == "2021-07-01"
    assert "test" not in protocol["selection_timing"].split("freeze")[0].lower()


def test_phase3a_has_no_optimization_or_baseline_side_effects() -> None:
    summary = json.loads((AUDIT / "phase3a_validation_summary.json").read_text())
    integrity = json.loads((AUDIT / "phase3a_baseline_integrity.json").read_text())
    assert summary["optimization_executed"] == 0
    assert summary["production_searches_executed"] == 0
    assert summary["canonical_configs_modified"] == 0
    assert summary["canonical_phase2_results_modified"] == 0
    assert integrity["optimization_output_directories_created"] == 0
    assert integrity["selected_production_parameter_files_created"] == 0
