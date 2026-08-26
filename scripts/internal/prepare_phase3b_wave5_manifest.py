#!/usr/bin/env python3
"""Freeze the exact Phase 3A Wave 5 subset without changing candidate values."""

from __future__ import annotations

import csv
import hashlib
import json
import os
from pathlib import Path

from strategy_framework.parameter_search import is_wave1_spec


ROOT = Path(__file__).resolve().parents[2]
AUDIT = ROOT / "outputs/internal_audit/strategy_workbook"
PARENT = AUDIT / "parameter_search_manifest.csv"
PLAN = AUDIT / "phase3a_search_execution_plan.csv"
OUTPUT = AUDIT / "phase3b_wave5_parameter_search_manifest.csv"
PROVENANCE = AUDIT / "phase3b_wave5_manifest_provenance.json"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as stream:
        return list(csv.DictReader(stream))


def main() -> int:
    parent_rows = read_csv(PARENT)
    rows = [
        row
        for row in parent_rows
        if row["status"] == "READY"
        and len(json.loads(row["searchable_parameters"])) == 1
        and not is_wave1_spec(row)
    ]
    plan = next(row for row in read_csv(PLAN) if row["wave"] == "WAVE_5_REMAINING_READY")
    candidate_count = sum(int(row["estimated_candidate_count"]) for row in rows)
    checks = {
        "phase3a_wave5_spec_count": len(rows) == int(plan["search_spec_count"]) == 7,
        "phase3a_wave5_candidate_count": candidate_count == int(plan["candidate_count"]) == 30,
        "phase3a_wave5_fold_count": int(plan["fold_count"]) == 7,
        "all_specs_ready": all(row["status"] == "READY" for row in rows),
        "disjoint_from_wave1": all(not is_wave1_spec(row) for row in rows),
    }
    if not all(checks.values()):
        raise RuntimeError(checks)
    temporary = OUTPUT.with_suffix(OUTPUT.suffix + ".tmp")
    with temporary.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    os.replace(temporary, OUTPUT)
    payload = {
        "source": str(PARENT.relative_to(ROOT)),
        "source_sha256": sha256(PARENT),
        "phase3a_execution_plan": str(PLAN.relative_to(ROOT)),
        "phase3a_execution_plan_sha256": sha256(PLAN),
        "selection_rule": "READY one-parameter specs not assigned to Wave 1",
        "spec_count": len(rows),
        "candidate_count": candidate_count,
        "fold_count": 7,
        "logical_evaluation_count": 518,
        "search_ids": [row["search_id"] for row in rows],
        "candidate_spaces_modified": False,
        "checks": checks,
        "manifest_sha256": sha256(OUTPUT),
    }
    temporary = PROVENANCE.with_suffix(PROVENANCE.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, PROVENANCE)
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
