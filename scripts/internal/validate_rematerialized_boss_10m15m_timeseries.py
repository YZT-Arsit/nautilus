#!/usr/bin/env python3
"""Validate re-materialized 10m/15m review series against pre-delete hashes."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    args = parser.parse_args()
    repo = args.repo.resolve()
    inventory = repo / "outputs/internal_audit/final_manifests/cleanup_inventory_Server.csv"
    expected = {}
    with inventory.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            rel = row["relative_path"].replace("\\", "/")
            if "/matrix_cases/" in f"/{rel}" and any(f"/timeframe={tf}/" in rel for tf in ("10m", "15m")) and rel.endswith("/review_timeseries.parquet"):
                expected[rel] = row["sha256_or_status"]
    missing = []
    mismatched = []
    for rel, wanted in expected.items():
        path = repo / "outputs" / rel
        if not path.is_file():
            missing.append(rel)
        else:
            actual = sha256(path)
            if actual != wanted:
                mismatched.append({"path": rel, "expected": wanted, "actual": actual})
    result = {
        "status": "PASSED" if len(expected) == 2142 and not missing and not mismatched else "FAILED",
        "expected": len(expected), "matched": len(expected) - len(missing) - len(mismatched),
        "missing": len(missing), "mismatched": len(mismatched),
        "missing_examples": missing[:5], "mismatch_examples": mismatched[:5],
    }
    target = repo / "outputs/internal_audit/final_manifests/rematerialized_10m15m_timeseries_validation.json"
    target.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0 if result["status"] == "PASSED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
