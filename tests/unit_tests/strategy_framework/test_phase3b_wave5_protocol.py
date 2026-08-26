from __future__ import annotations

import csv
import json
from pathlib import Path

from scripts.internal.preflight_phase3b_wave5 import MANIFEST
from scripts.internal.preflight_phase3b_wave5 import PARENT_MANIFEST
from scripts.internal.preflight_phase3b_wave5 import enumerate_candidates
from scripts.internal.preflight_phase3b_wave5 import equivalence_rows
from scripts.internal.preflight_phase3b_wave5 import wave5_specs
from scripts.internal.run_phase3b_wave5 import neighborhood_diagnostics
from strategy_framework.parameter_search import generate_candidates


ROOT = Path(__file__).resolve().parents[3]
PROVENANCE = ROOT / "outputs/internal_audit/strategy_workbook/phase3b_wave5_manifest_provenance.json"


def read(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as stream:
        return list(csv.DictReader(stream))


def test_wave5_subset_is_exactly_the_phase3a_remaining_ready_set() -> None:
    rows = wave5_specs(read(MANIFEST))
    provenance = json.loads(PROVENANCE.read_text(encoding="utf-8"))
    assert len(rows) == provenance["spec_count"] == 7
    assert sum(int(row["estimated_candidate_count"]) for row in rows) == 30
    assert [row["search_id"] for row in rows] == provenance["search_ids"]
    parent = {row["search_id"]: row for row in read(PARENT_MANIFEST)}
    assert all(row == parent[row["search_id"]] for row in rows)
    assert provenance["candidate_spaces_modified"] is False


def test_wave5_candidates_baselines_and_logical_accounting() -> None:
    specs = wave5_specs(read(MANIFEST))
    groups = [enumerate_candidates(spec) for spec in specs]
    generated = [generate_candidates(spec) for spec in specs]
    assert sum(len(candidates) for candidates, _, _ in groups) == 30
    assert sum(rejected for _, _, rejected in groups) == 0
    assert sum(
        sum(candidate.candidate_role == "BASELINE" for candidate in group)
        for group in generated
    ) == 7
    assert 30 * 7 == 210
    assert 30 * 7 == 210
    assert 7 * 7 == 49
    assert 7 * 7 == 49
    assert 2 * 30 * 7 + 2 * 7 * 7 == 518


def test_wave5_equivalence_audit_preserves_source_identities() -> None:
    specs = wave5_specs(read(MANIFEST))
    rows = equivalence_rows(specs)
    assert len(rows) == 7
    assert {row["search_id"] for row in rows} == {spec["search_id"] for spec in specs}
    assert len({row["strategy_id"] for row in rows}) == 7


def test_neighborhood_optimum_requires_two_immediate_neighbors() -> None:
    spec = {
        "search_id": "search",
        "strategy_id": "strategy",
        "searchable_parameters": '["window"]',
        "candidate_space": '{"window": [1, 2, 3]}',
    }
    candidates = [
        {
            "search_id": "search",
            "fold_id": "wf01",
            "split": "VALIDATION",
            "candidate_id": f"c{window}",
            "parameters": json.dumps({"window": window}),
            "eligible": "True",
            "return_1x": str(value),
        }
        for window, value in ((1, 1.0), (2, 3.0), (3, 2.0))
    ]
    middle = neighborhood_diagnostics(
        spec,
        [{"fold_id": "wf01", "selected_candidate_id": "c2"}],
        candidates,
    )[0]
    edge = neighborhood_diagnostics(
        spec,
        [{"fold_id": "wf01", "selected_candidate_id": "c1"}],
        candidates,
    )[0]
    assert middle["isolated_validation_optimum"] is True
    assert middle["eligible_immediate_neighbor_count"] == 2
    assert edge["isolated_validation_optimum"] is False
    assert edge["eligible_immediate_neighbor_count"] == 1
