#!/usr/bin/env python3
"""Allowlist-only cleanup of superseded Phase 5/6 generated outputs."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
FINAL_NAME = "all_converted_workbook_strategies"
AUDIT = ROOT / "outputs" / "internal_audit" / "final_workbook_consolidation"
PHASE5_DELIVERABLES = [f"workbook_strategies_phase5{letter}{suffix}" for letter in "abcdef" for suffix in ("", ".zip", ".zip.sha256")]
PHASE6_DELIVERABLES = [
    "phase6a_expanded_strategy_review", "phase6a_expanded_strategy_review.zip",
    "phase6b_cost_episode_review", "phase6b_cost_episode_review.zip",
    "phase6c_cross_symbol_falsification", "phase6c_cross_symbol_falsification.zip",
    "phase6d_execution_realism", "phase6d_execution_realism.zip",
    "phase6e_forward_holdout", "phase6e_forward_holdout.zip",
]
PHASE6_BASELINE = [f"phase6{letter}" for letter in "abcde"]
DELETABLE_BATCHES = ["phase5a_smoke", "workbook_strategies_phase5c_smoke", "workbook_strategies_phase5c_smoke_daily", "phase6c_cross_symbol"]
PRESERVED_CANONICAL_BATCHES = [
    "workbook_strategies_phase5a", "workbook_strategies_phase5b", "workbook_strategies_phase5c",
    "workbook_strategies_phase5c_daily", "workbook_strategies_phase5e", "workbook_strategies_phase5e_daily",
    "workbook_strategies_phase5f", "workbook_strategies_phase5f_daily",
]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def tree_stats(path: Path) -> tuple[int, int]:
    if not path.exists():
        return 0, 0
    if path.is_file():
        return 1, path.stat().st_size
    files = [item for item in path.rglob("*") if item.is_file()]
    return len(files), sum(item.stat().st_size for item in files)


def final_valid(deliverables: Path, expected_sha: str | None) -> tuple[bool, str]:
    folder = deliverables / FINAL_NAME
    archive = deliverables / f"{FINAL_NAME}.zip"
    if not folder.is_dir() or not archive.is_file():
        return False, ""
    extensions = {path.suffix.lower() for path in folder.rglob("*") if path.is_file()}
    master = pd.read_csv(folder / f"{FINAL_NAME}.csv")
    strategy_count = int((master.record_type == "STRATEGY_INDEX").sum())
    digest = sha256(archive)
    valid = (
        strategy_count > 0
        and extensions <= {".png", ".csv"}
        and len(list(folder.rglob("*.png"))) == strategy_count * 4
        and len(list(folder.rglob("*.csv"))) == strategy_count + 1
    )
    if expected_sha:
        valid = valid and digest == expected_sha
    return valid, digest


def candidates(machine: str) -> list[dict[str, Any]]:
    deliverables = ROOT / "outputs" / "deliverables"
    rows: list[dict[str, Any]] = []
    for name in PHASE5_DELIVERABLES:
        rows.append({"path": deliverables / name, "phase": name.split("_")[-1].split(".")[0].upper(), "artifact_type": "DELIVERABLE", "delete_allowed": True})
    for name in PHASE6_DELIVERABLES:
        rows.append({"path": deliverables / name, "phase": name.split("_")[0].upper(), "artifact_type": "DELIVERABLE", "delete_allowed": True})
    if machine == "server":
        baseline = ROOT / "outputs" / "baseline_evaluation"
        batches = ROOT / "outputs" / "batches"
        for name in PHASE6_BASELINE:
            rows.append({"path": baseline / name, "phase": name.upper(), "artifact_type": "GENERATED_PHASE_ANALYSIS", "delete_allowed": True})
        for name in DELETABLE_BATCHES:
            rows.append({"path": batches / name, "phase": name.split("_")[0].upper(), "artifact_type": "SMOKE_OR_SUPERSEDED_RESEARCH_BATCH", "delete_allowed": True})
        for name in PRESERVED_CANONICAL_BATCHES:
            rows.append({"path": batches / name, "phase": name.split("_")[-1].upper(), "artifact_type": "REQUIRED_CANONICAL_DATA", "delete_allowed": False})
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--machine", choices=("server", "mac"), required=True)
    parser.add_argument("--expected-sha")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    deliverables = ROOT / "outputs" / "deliverables"
    valid, digest = final_valid(deliverables, args.expected_sha)
    rows: list[dict[str, Any]] = []
    for item in candidates(args.machine):
        path = item.pop("path")
        file_count, size = tree_stats(path)
        rows.append({
            "path": str(path), **item, "exists_before": path.exists(), "file_count": file_count,
            "size_bytes": size, "superseded_by": str(deliverables / FINAL_NAME),
            "final_replacement_validated": valid,
            "delete_allowed": bool(item["delete_allowed"] and valid and path.exists()),
        })
    manifest = pd.DataFrame(rows)
    AUDIT.mkdir(parents=True, exist_ok=True)
    manifest_path = AUDIT / f"final_{args.machine}_cleanup_manifest.csv"
    manifest.to_csv(manifest_path, index=False, encoding="utf-8-sig")
    deleted_files = deleted_directories = reclaimed = 0
    if args.execute:
        if not valid:
            raise AssertionError("final package did not pass cleanup gate")
        for row in manifest.loc[manifest.delete_allowed].itertuples(index=False):
            path = Path(row.path)
            if path.is_dir():
                shutil.rmtree(path)
                deleted_directories += 1
            elif path.is_file():
                path.unlink()
                deleted_files += 1
            reclaimed += int(row.size_bytes)
        remaining = [row.path for row in manifest.loc[manifest.delete_allowed].itertuples(index=False) if Path(row.path).exists()]
        if remaining:
            raise AssertionError(f"allowlisted cleanup incomplete: {remaining}")
    summary = {
        "machine": args.machine, "mode": "EXECUTE" if args.execute else "DRY_RUN",
        "final_package_valid": valid, "final_zip_sha256": digest,
        "allowlisted_items": int(manifest.delete_allowed.sum()),
        "preserved_required_canonical_data": int((manifest.artifact_type == "REQUIRED_CANONICAL_DATA").sum()),
        "deleted_files": deleted_files, "deleted_directories": deleted_directories,
        "bytes_reclaimed": reclaimed,
    }
    (AUDIT / f"final_{args.machine}_cleanup_summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary), flush=True)


if __name__ == "__main__":
    main()
