#!/usr/bin/env python3
"""Create and optionally apply an allowlisted episode-result cleanup manifest."""

from __future__ import annotations

import argparse
import csv
import json
import zipfile
from pathlib import Path


ROOT_GENERATED = (
    "episode_diagnostics_summary.html",
    "episode_metric_summary.csv",
    "episode_distribution_bins.csv",
    "episode_diagnostics_validation.json",
    "episode_diagnostics_artifact_manifest.json",
)
RUN_GENERATED = (
    "*_episode_diagnostics.png",
    "episode_metrics.parquet",
    "episode_distribution_bins.csv",
    "episode_metric_summary.json",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--machine", required=True)
    parser.add_argument("--old-root", type=Path, required=True)
    parser.add_argument("--revised-root", type=Path)
    parser.add_argument("--replacement-zip", type=Path)
    parser.add_argument("--old-zip", type=Path)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--packaged-copy", action="store_true")
    parser.add_argument("--apply", action="store_true")
    return parser.parse_args()


def validate_revised(revised_root: Path) -> dict:
    validation_path = revised_root / "episode_diagnostics_validation.json"
    validation = json.loads(validation_path.read_text(encoding="utf-8"))
    if validation.get("status") != "passed":
        raise RuntimeError("revised episode diagnostics have not passed validation")
    if int(validation.get("plot_validation_failure_count", -1)) != 0:
        raise RuntimeError("revised episode diagnostics contain plot validation failures")
    return validation


def canonical_candidates(old_root: Path, revised_root: Path) -> list[dict[str, object]]:
    validate_revised(revised_root)
    candidates: list[Path] = [old_root / name for name in ROOT_GENERATED]
    candidates.extend(old_root.glob("episode_diagnostics_*.log"))
    for pattern in RUN_GENERATED:
        candidates.extend(old_root.glob(f"*/BTCUSDT/*/lag*m/*/{pattern}"))
    rows: list[dict[str, object]] = []
    for path in sorted({item.resolve() for item in candidates if item.is_file()}):
        relative = path.relative_to(old_root.resolve())
        if path.suffix == ".log":
            replacement = revised_root.resolve() / (
                "episode_diagnostics_server_stderr.log"
                if "stderr" in path.name
                else "episode_diagnostics_server_stdout.log"
            )
        else:
            replacement = revised_root.resolve() / relative
        if not replacement.is_file():
            raise RuntimeError(f"no validated replacement for cleanup candidate: {path}")
        rows.append(
            {
                "path": str(path),
                "size_bytes": path.stat().st_size,
                "reason": "superseded boss-facing episode diagnostic",
                "replacement": str(replacement),
                "classification": "ALLOWLISTED_SUPERSEDED_GENERATED_RESULT",
            }
        )
    return rows


def packaged_candidates(old_root: Path, replacement_zip: Path, old_zip: Path | None) -> list[dict[str, object]]:
    if not replacement_zip.is_file():
        raise RuntimeError("validated replacement ZIP does not exist")
    with zipfile.ZipFile(replacement_zip) as archive:
        if archive.testzip() is not None:
            raise RuntimeError("replacement ZIP failed integrity validation")
        members = set(archive.namelist())
    rows: list[dict[str, object]] = []
    for path in sorted(item.resolve() for item in old_root.rglob("*") if item.is_file()):
        relative = path.relative_to(old_root.resolve()).as_posix()
        if relative not in members:
            raise RuntimeError(f"old packaged result has no replacement ZIP member: {relative}")
        rows.append(
            {
                "path": str(path),
                "size_bytes": path.stat().st_size,
                "reason": "superseded extracted deliverable member",
                "replacement": f"{replacement_zip.resolve()}::{relative}",
                "classification": "ALLOWLISTED_DUPLICATE_PACKAGED_RESULT",
            }
        )
    if old_zip and old_zip.is_file() and old_zip.resolve() != replacement_zip.resolve():
        rows.append(
            {
                "path": str(old_zip.resolve()),
                "size_bytes": old_zip.stat().st_size,
                "reason": "superseded episode diagnostics archive",
                "replacement": str(replacement_zip.resolve()),
                "classification": "ALLOWLISTED_SUPERSEDED_ARCHIVE",
            }
        )
    return rows


def write_manifest(machine: str, rows: list[dict[str, object]], destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    with temporary.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=(
                "machine",
                "path",
                "size_bytes",
                "reason",
                "replacement",
                "classification",
                "deleted",
            ),
        )
        writer.writeheader()
        for row in rows:
            writer.writerow({"machine": machine, **row, "deleted": False})
    temporary.replace(destination)


def apply_manifest(machine: str, rows: list[dict[str, object]], destination: Path) -> None:
    applied: list[dict[str, object]] = []
    for row in rows:
        path = Path(str(row["path"]))
        if path.is_file():
            path.unlink()
        applied.append({**row, "deleted": not path.exists()})
    with destination.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=(
                "machine",
                "path",
                "size_bytes",
                "reason",
                "replacement",
                "classification",
                "deleted",
            ),
        )
        writer.writeheader()
        for row in applied:
            writer.writerow({"machine": machine, **row})


def remove_empty_tree(root: Path) -> None:
    """Remove only directories proven empty after allowlisted file deletion."""
    for directory in sorted(
        (path for path in root.rglob("*") if path.is_dir()),
        key=lambda path: len(path.parts),
        reverse=True,
    ):
        try:
            directory.rmdir()
        except OSError:
            pass
    try:
        root.rmdir()
    except OSError:
        pass


def main() -> int:
    args = parse_args()
    if args.packaged_copy:
        if args.replacement_zip is None:
            raise ValueError("--replacement-zip is required with --packaged-copy")
        rows = packaged_candidates(args.old_root, args.replacement_zip, args.old_zip)
    else:
        if args.revised_root is None:
            raise ValueError("--revised-root is required for canonical in-place cleanup")
        rows = canonical_candidates(args.old_root, args.revised_root)
    write_manifest(args.machine, rows, args.manifest)
    if args.apply:
        apply_manifest(args.machine, rows, args.manifest)
        if args.packaged_copy:
            remove_empty_tree(args.old_root)
    print(
        json.dumps(
            {
                "machine": args.machine,
                "candidate_count": len(rows),
                "bytes": sum(int(row["size_bytes"]) for row in rows),
                "applied": bool(args.apply),
                "manifest": str(args.manifest.resolve()),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
