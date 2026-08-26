#!/usr/bin/env python3
# ruff: noqa: E402,I001
"""Create the authorized, versioned Wave 3 manifest precision amendment."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import sys
from datetime import UTC
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from strategy_framework.parameter_adaptation import canonical_config_hash
from strategy_framework.parameter_adaptation import deterministic_candidate_id


AUDIT = ROOT / "outputs/internal_audit/strategy_workbook"
PARENT = AUDIT / "parameter_search_manifest.csv"
OUTPUT = AUDIT / "phase3b_wave3_parameter_search_manifest.csv"
AMENDMENT = AUDIT / "phase3b_wave3_manifest_amendment.json"
AMENDMENT_VERSION = "PHASE3B_WAVE3_MANIFEST_AMENDMENT_V1"
TARGETS = {
    "phase3a__xlsx_s2_0660__1m",
    "phase3a__xlsx_s2_0837__1m",
}
EXACT_THIRD = 0.3333333333333333
ROUNDED_THIRD = 0.33333333


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    os.replace(temporary, path)


def write_json(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def main() -> int:
    parent_hash = sha256(PARENT)
    with PARENT.open(newline="", encoding="utf-8-sig") as stream:
        rows = list(csv.DictReader(stream))
    changes: list[dict[str, Any]] = []
    found: set[str] = set()
    for row in rows:
        if row["search_id"] not in TARGETS:
            continue
        found.add(row["search_id"])
        space = json.loads(row["candidate_space"])
        values = list(space["layer_fraction"])
        if values.count(ROUNDED_THIRD) != 1 or EXACT_THIRD in values:
            raise ValueError(f"unexpected source space for {row['search_id']}: {values}")
        old_parameters = {
            **json.loads(row["fixed_parameters"]),
            "atr_window": 14,
            "layer_fraction": ROUNDED_THIRD,
        }
        baseline = json.loads(row["baseline_candidate"])
        if baseline["parameters"]["layer_fraction"] != EXACT_THIRD:
            raise ValueError(f"unexpected baseline for {row['search_id']}")
        space["layer_fraction"] = [EXACT_THIRD if value == ROUNDED_THIRD else value for value in values]
        row["candidate_space"] = json.dumps(space, ensure_ascii=False, sort_keys=True)
        changes.append(
            {
                "search_id": row["search_id"],
                "field": "candidate_space.layer_fraction",
                "old_value": ROUNDED_THIRD,
                "new_value": EXACT_THIRD,
                "old_candidate_id": deterministic_candidate_id(row["search_id"], old_parameters),
                "old_config_hash": canonical_config_hash(old_parameters),
                "new_candidate_id": baseline["candidate_id"],
                "new_config_hash": baseline["config_hash"],
                "reason": "authorized lossless preservation of canonical one-third baseline",
            }
        )
    if found != TARGETS:
        raise ValueError(f"missing amendment targets: {sorted(TARGETS - found)}")
    write_csv(OUTPUT, rows)
    write_json(
        AMENDMENT,
        {
            "amendment_version": AMENDMENT_VERSION,
            "parent_manifest": str(PARENT.relative_to(ROOT)),
            "parent_manifest_sha256": parent_hash,
            "amended_manifest": str(OUTPUT.relative_to(ROOT)),
            "amended_manifest_sha256": sha256(OUTPUT),
            "authorized_at_utc": datetime.now(UTC).isoformat(),
            "scope": "two lossless numeric-serialization corrections only",
            "candidate_count_before": 702,
            "candidate_count_after": 702,
            "logical_evaluation_count": 10318,
            "changes": changes,
            "parent_manifest_modified": False,
        },
    )
    print(AMENDMENT.read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
