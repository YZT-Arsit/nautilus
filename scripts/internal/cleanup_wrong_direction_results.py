#!/usr/bin/env python3
"""Allowlist cleanup for the superseded four-mode boss result universe."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

import pandas as pd


SUPERSEDED_RUN_PATTERNS = (
    "*_episode_diagnostics.png",
    "*_performance.png",
    "*_per_trade_be.png",
    "episode_metrics.parquet",
    "episode_distribution_bins.csv",
    "episode_metric_summary.json",
)
SUPERSEDED_ROOT_NAMES = (
    "episode_diagnostics_summary.html",
    "episode_diagnostics_validation.json",
    "episode_diagnostics_artifact_manifest.json",
    "episode_distribution_bins.csv",
    "episode_metric_summary.csv",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--machine", required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--corrected-root", type=Path, required=True)
    parser.add_argument("--partial-wrong-root", type=Path)
    parser.add_argument("--old-zip", type=Path, action="append", default=[])
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--entire-source-copy", action="store_true")
    parser.add_argument("--apply", action="store_true")
    return parser.parse_args()


def validate_corrected(root: Path) -> dict[str, Any]:
    validation = json.loads((root / "episode_diagnostics_validation.json").read_text())
    audit = json.loads((root / "boss_direction_model_audit.json").read_text())
    canonical = pd.read_csv(root / "canonical_summary.csv")
    if validation.get("status") != "passed" or audit.get("status") != "passed":
        raise RuntimeError("corrected boss result has not passed release validation")
    if int(validation.get("plot_validation_failure_count", -1)) != 0:
        raise RuntimeError("corrected boss result has unexpected blank panels")
    if set(canonical["variant"].unique()) != {"normal", "strict_reverse"}:
        raise RuntimeError("corrected boss result contains an invalid direction mode")
    if len(canonical) // 2 != int(audit["expected_corrected_units"]):
        raise RuntimeError("corrected boss result cardinality mismatch")
    return {**validation, **audit}


def replacement_case(corrected_root: Path, old_case: Path, source_root: Path) -> Path:
    parts = list(old_case.relative_to(source_root).parts)
    old_mode = parts[-1]
    parts[-1] = "strict_reverse" if old_mode == "strict_reverse" else "normal"
    return corrected_root.joinpath(*parts)


def replacement_anchor(case: Path) -> Path:
    diagnostics = list(case.glob("*_episode_diagnostics.png"))
    if len(diagnostics) != 1:
        raise RuntimeError(f"missing validated corrected replacement in {case}")
    return diagnostics[0]


def add_row(
    rows: list[dict[str, Any]],
    *,
    path: Path,
    old_mode: str,
    reason: str,
    replacement: Path,
) -> None:
    if not path.is_file() or not replacement.exists():
        return
    rows.append(
        {
            "path": str(path.resolve()),
            "size": path.stat().st_size,
            "old_mode": old_mode,
            "reason": reason,
            "replacement_path": str(replacement.resolve()),
            "replacement_validated": True,
            "delete_allowed": True,
            "deleted": False,
        }
    )


def build_rows(args: argparse.Namespace) -> list[dict[str, Any]]:
    source_root = args.source_root.resolve()
    corrected_root = args.corrected_root.resolve()
    validate_corrected(corrected_root)
    rows: list[dict[str, Any]] = []
    if args.entire_source_copy:
        anchor = corrected_root / "episode_diagnostics_validation.json"
        for path in sorted(item for item in source_root.rglob("*") if item.is_file()):
            parts = path.relative_to(source_root).parts
            old_mode = next((part for part in parts if part in {"original", "long_only", "short_only", "strict_reverse"}), "root")
            add_row(
                rows,
                path=path,
                old_mode=old_mode,
                reason="superseded extracted four-mode boss deliverable",
                replacement=anchor,
            )
    else:
        for old_mode in ("long_only", "short_only"):
            for case in source_root.glob(f"*/BTCUSDT/*/lag*m/{old_mode}"):
                anchor = replacement_anchor(replacement_case(corrected_root, case, source_root))
                for path in sorted(item for item in case.rglob("*") if item.is_file()):
                    add_row(
                        rows,
                        path=path,
                        old_mode=old_mode,
                        reason="deprecated redundant secondary direction filter",
                        replacement=anchor,
                    )
        for old_mode in ("original", "strict_reverse"):
            for case in source_root.glob(f"*/BTCUSDT/*/lag*m/{old_mode}"):
                corrected_case = replacement_case(corrected_root, case, source_root)
                anchor = replacement_anchor(corrected_case)
                for pattern in SUPERSEDED_RUN_PATTERNS:
                    for path in case.glob(pattern):
                        add_row(
                            rows,
                            path=path,
                            old_mode=old_mode,
                            reason="superseded old boss-facing figure or derived diagnostic",
                            replacement=anchor,
                        )
        root_anchor = corrected_root / "episode_diagnostics_validation.json"
        for name in SUPERSEDED_ROOT_NAMES:
            add_row(
                rows,
                path=source_root / name,
                old_mode="root",
                reason="superseded 512-unit central boss artifact",
                replacement=root_anchor,
            )
        for path in source_root.glob("episode_diagnostics_*.log"):
            add_row(
                rows,
                path=path,
                old_mode="root",
                reason="superseded 512-unit renderer log",
                replacement=root_anchor,
            )
        if args.partial_wrong_root and args.partial_wrong_root.exists():
            for path in sorted(
                item for item in args.partial_wrong_root.resolve().rglob("*") if item.is_file()
            ):
                add_row(
                    rows,
                    path=path,
                    old_mode="partial_four_mode_v2",
                    reason="abandoned 512-unit renderer output",
                    replacement=root_anchor,
                )
    zip_anchor = corrected_root / "episode_diagnostics_validation.json"
    for archive in args.old_zip:
        add_row(
            rows,
            path=archive.resolve(),
            old_mode="archive",
            reason="superseded four-mode boss archive",
            replacement=zip_anchor,
        )
    unique = {row["path"]: row for row in rows}
    return [unique[key] for key in sorted(unique)]


def write_manifest(rows: list[dict[str, Any]], machine: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    columns = (
        "machine",
        "path",
        "size",
        "old_mode",
        "reason",
        "replacement_path",
        "replacement_validated",
        "delete_allowed",
        "deleted",
    )
    with temporary.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({"machine": machine, **row})
    temporary.replace(path)


def remove_empty_directories(roots: list[Path]) -> int:
    removed = 0
    for root in roots:
        if not root.exists():
            continue
        directories = sorted(
            (item for item in root.rglob("*") if item.is_dir()),
            key=lambda item: len(item.parts),
            reverse=True,
        )
        for directory in directories:
            try:
                directory.rmdir()
                removed += 1
            except OSError:
                pass
        try:
            root.rmdir()
            removed += 1
        except OSError:
            pass
    return removed


def main() -> int:
    args = parse_args()
    rows = build_rows(args)
    write_manifest(rows, args.machine, args.manifest)
    removed_directories = 0
    if args.apply:
        for row in rows:
            if row["delete_allowed"] and row["replacement_validated"]:
                path = Path(row["path"])
                if path.is_file():
                    path.unlink()
                row["deleted"] = not path.exists()
        roots = []
        if args.entire_source_copy:
            roots.append(args.source_root.resolve())
        else:
            roots.extend(args.source_root.resolve().glob("*/BTCUSDT/*/lag*m/long_only"))
            roots.extend(args.source_root.resolve().glob("*/BTCUSDT/*/lag*m/short_only"))
            if args.partial_wrong_root:
                roots.append(args.partial_wrong_root.resolve())
        removed_directories = remove_empty_directories(list(roots))
        write_manifest(rows, args.machine, args.manifest)
    print(
        json.dumps(
            {
                "candidate_files": len(rows),
                "candidate_bytes": sum(int(row["size"]) for row in rows),
                "deleted_files": sum(bool(row["deleted"]) for row in rows),
                "removed_directories": removed_directories,
                "manifest": str(args.manifest.resolve()),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
