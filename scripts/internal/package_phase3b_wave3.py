#!/usr/bin/env python3
"""Package completed Phase 3B Wave 3 review artifacts on the server."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SOURCE = ROOT / "outputs/parameter_search/phase3b_wave3"
DEFAULT_DESTINATION = ROOT / "outputs/deliverables/phase3b_wave3"
TOP_LEVEL = (
    "phase3b_wave3_equivalence_manifest.csv",
    "phase3b_wave3_run_manifest.csv",
    "phase3b_wave3_candidate_metrics.csv",
    "phase3b_wave3_fold_selections.csv",
    "phase3b_wave3_fold_test_metrics.csv",
    "phase3b_wave3_oos_summary.csv",
    "phase3b_wave3_baseline_comparison.csv",
    "phase3b_wave3_parameter_stability.csv",
    "phase3b_wave3_boundary_drift.csv",
    "phase3b_wave3_fold_consistency.csv",
    "phase3b_wave3_validation_summary.json",
    "preflight_validation.json",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:  # noqa: C901 - explicit packaging validation steps
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--destination", type=Path, default=DEFAULT_DESTINATION)
    args = parser.parse_args()
    summary = json.loads((args.source / "phase3b_wave3_validation_summary.json").read_text())
    if summary.get("status") != "PASSED" or summary.get("release_decision") != "WAVE5_READY":
        raise RuntimeError("refusing to package an unvalidated Wave 3 result")
    if args.destination.exists():
        raise FileExistsError(f"deliverable already exists; refusing to replace: {args.destination}")
    args.destination.mkdir(parents=True)
    for name in TOP_LEVEL:
        source = args.source / name
        if not source.is_file():
            raise FileNotFoundError(source)
        shutil.copy2(source, args.destination / name)
    audit = args.destination / "audit"
    audit.mkdir()
    for name in ("phase3b_wave3_manifest_amendment.json", "phase3b_wave3_parameter_search_manifest.csv"):
        shutil.copy2(ROOT / "outputs/internal_audit/strategy_workbook" / name, audit / name)
    figures = args.destination / "figures"
    figures.mkdir()
    for spec_root in sorted(path for path in args.source.iterdir() if path.is_dir() and path.name.startswith("phase3a__")):
        for name, suffix in (("oos_selected_vs_baseline.png", "oos"), ("parameter_path.png", "parameters")):
            source = spec_root / name
            if not source.is_file():
                raise FileNotFoundError(source)
            shutil.copy2(source, figures / f"{spec_root.name}__{suffix}.png")
    zip_path = args.destination.with_suffix(".zip")
    temporary = zip_path.with_suffix(".zip.tmp")
    with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(args.destination.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(args.destination.parent))
    temporary.replace(zip_path)
    checksum = sha256(zip_path)
    zip_path.with_suffix(".zip.sha256").write_text(f"{checksum}  {zip_path.name}\n")
    print(
        json.dumps(
            {
                "zip": str(zip_path),
                "sha256": checksum,
                "files": sum(1 for path in args.destination.rglob("*") if path.is_file()),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
