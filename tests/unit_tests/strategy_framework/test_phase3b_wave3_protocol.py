from __future__ import annotations

import csv
import json
from pathlib import Path

from scripts.internal.preflight_phase3b_wave3 import MANIFEST
from scripts.internal.preflight_phase3b_wave3 import PARENT_MANIFEST
from scripts.internal.preflight_phase3b_wave3 import enumerate_candidates
from scripts.internal.preflight_phase3b_wave3 import equivalence_rows
from scripts.internal.preflight_phase3b_wave3 import file_hash
from scripts.internal.preflight_phase3b_wave3 import wave3_specs
from strategy_framework.parameter_search import generate_candidates


ROOT = Path(__file__).resolve().parents[3]
AMENDMENT = ROOT / "outputs/internal_audit/strategy_workbook/phase3b_wave3_manifest_amendment.json"


def _rows() -> list[dict[str, str]]:
    with MANIFEST.open(newline="", encoding="utf-8-sig") as stream:
        return wave3_specs(list(csv.DictReader(stream)))


def test_versioned_amendment_preserves_parent_and_locked_denominators() -> None:
    amendment = json.loads(AMENDMENT.read_text(encoding="utf-8"))
    assert amendment["amendment_version"] == "PHASE3B_WAVE3_MANIFEST_AMENDMENT_V1"
    assert amendment["parent_manifest_modified"] is False
    assert file_hash(PARENT_MANIFEST) == amendment["parent_manifest_sha256"]
    assert file_hash(MANIFEST) == amendment["amended_manifest_sha256"]
    assert len(amendment["changes"]) == 2
    assert amendment["candidate_count_after"] == 702
    assert amendment["logical_evaluation_count"] == 10_318


def test_wave3_candidates_constraints_and_baselines_are_exact() -> None:
    specs = _rows()
    enumerated = [enumerate_candidates(spec) for spec in specs]
    generated = [generate_candidates(spec) for spec in specs]
    assert len(specs) == 35
    assert sum(len(candidates) for candidates, _, _ in enumerated) == 702
    assert sum(rejected for _, _, rejected in enumerated) == 0
    assert sum(sum(candidate.candidate_role == "BASELINE" for candidate in group) for group in generated) == 35
    assert 2 * 702 * 7 + 2 * 35 * 7 == 10_318


def test_equivalence_audit_preserves_every_search_identity() -> None:
    specs = _rows()
    rows = equivalence_rows(specs)
    assert len(rows) == 35
    assert {row["search_id"] for row in rows} == {spec["search_id"] for spec in specs}
    assert len({row["strategy_id"] for row in rows}) == 35
    assert sum(row["equivalent_for_physical_compute"] for row in rows) == 31
