#!/usr/bin/env python3
"""Validate NTFS-restored tick-review Parquet files against pre-delete hashes."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import shutil
from pathlib import Path


PREFIX = "baseline_evaluation/boss_multitimeframe_tick_screen/matrix_cases/"
SUFFIX = "/review_timeseries.parquet"


def win_long(path: Path) -> str:
    value = str(path)
    return value if value.startswith("\\\\?\\") else "\\\\?\\" + value


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with open(win_long(path), "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", type=Path, required=True)
    ap.add_argument("--promote", action="store_true")
    ap.add_argument("--output", type=Path)
    args = ap.parse_args()
    repo = args.repo.resolve()
    outputs = repo / "outputs"
    inventory = outputs / "internal_audit" / "final_manifests" / "cleanup_inventory_Server.csv"
    recovery = outputs / "recovered_tick_review" / "nautilus" / "outputs"
    canonical = outputs / "baseline_evaluation" / "boss_multitimeframe_tick_screen" / "matrix_cases"

    expected: dict[str, tuple[int, str]] = {}
    with inventory.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            rel = row["relative_path"].replace("\\", "/")
            if rel.startswith(PREFIX) and rel.endswith(SUFFIX):
                expected[rel] = (int(row["size_bytes"]), row["sha256_or_status"])

    missing: list[str] = []
    mismatch: list[dict[str, object]] = []
    recovered_for: dict[str, Path] = {}
    matched = 0
    timeframes: dict[str, int] = {}
    for rel, (size, want_hash) in expected.items():
        rel_path = Path(rel)
        recovered_parts = []
        for part in rel_path.parent.parts:
            recovered_parts.append(
                part.replace("symbol=", "symbol_")
                .replace("timeframe=", "timeframe_")
                .replace("semantic=", "semantic_")
            )
        parent = recovery.joinpath(*recovered_parts)
        candidates = [
            p for p in parent.glob("review_timeseries*")
            if p.name.startswith("review_timeseries")
        ] if parent.exists() else []
        if len(candidates) != 1:
            missing.append(rel)
            continue
        candidate = candidates[0]
        got_size = os.stat(win_long(candidate)).st_size
        got_hash = digest(candidate)
        if got_size != size or got_hash != want_hash:
            mismatch.append({
                "relative_path": rel, "expected_size": size, "actual_size": got_size,
                "expected_sha256": want_hash, "actual_sha256": got_hash,
            })
            continue
        matched += 1
        recovered_for[rel] = candidate
        tf = next((part.split("=", 1)[1] for part in Path(rel).parts if part.startswith("timeframe=")), "unknown")
        timeframes[tf] = timeframes.get(tf, 0) + 1

    promoted = False
    if args.promote:
        if missing or mismatch or matched != len(expected):
            raise SystemExit("refusing promotion: recovery did not exactly match pre-delete inventory")
        if canonical.exists():
            raise SystemExit(f"refusing promotion: canonical path already exists: {canonical}")
        for rel, candidate in recovered_for.items():
            dest_rel = Path(rel).relative_to(PREFIX.rstrip("/"))
            destination = canonical / dest_rel
            destination.parent.mkdir(parents=True, exist_ok=True)
            with open(win_long(candidate), "rb") as src, destination.open("wb") as dst:
                shutil.copyfileobj(src, dst, length=1024 * 1024)
        promoted = True

    result = {
        "status": "PASSED" if not missing and not mismatch and matched == len(expected) else "FAILED",
        "expected": len(expected), "matched": matched, "missing": len(missing),
        "hash_or_size_mismatch": len(mismatch), "timeframe_counts": timeframes,
        "promoted_to_canonical": promoted, "canonical_path": str(canonical),
        "missing_examples": missing[:10], "mismatch_examples": mismatch[:10],
    }
    rendered = json.dumps(result, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
