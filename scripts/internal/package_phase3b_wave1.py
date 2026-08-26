#!/usr/bin/env python3
"""Create the compact Mac/server review bundle for completed Wave 1 outputs."""
from __future__ import annotations

import argparse
import json
import shutil
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SOURCE = ROOT / "outputs/parameter_search/phase3b_wave1"
DEFAULT_DESTINATION = ROOT / "outputs/deliverables/phase3b_wave1"
SUMMARY_FILES = (
    "phase3b_wave1_run_manifest.csv",
    "phase3b_wave1_candidate_metrics.csv",
    "phase3b_wave1_fold_selections.csv",
    "phase3b_wave1_fold_test_metrics.csv",
    "phase3b_wave1_oos_summary.csv",
    "phase3b_wave1_parameter_stability.csv",
    "phase3b_wave1_baseline_comparison.csv",
    "phase3b_wave1_validation_summary.json",
    "baseline_integrity_before.json",
    "baseline_integrity_after.json",
    "preflight_validation.json",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--destination", type=Path, default=DEFAULT_DESTINATION)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    validation_path = args.source / "phase3b_wave1_validation_summary.json"
    validation = json.loads(validation_path.read_text(encoding="utf-8"))
    if validation.get("status") != "PASSED":
        raise RuntimeError("refusing to package an unvalidated Wave 1 run")
    args.destination.mkdir(parents=True, exist_ok=True)
    for name in SUMMARY_FILES:
        shutil.copy2(args.source / name, args.destination / name)
    amendment = ROOT / "outputs/internal_audit/strategy_workbook/phase3b_wave1_protocol_amendment.json"
    shutil.copy2(amendment, args.destination / amendment.name)
    figures = args.destination / "figures"
    figures.mkdir(exist_ok=True)
    for source in sorted(args.source.glob("phase3a__*/oos_selected_vs_baseline.png")):
        shutil.copy2(source, figures / f"{source.parent.name}_oos_selected_vs_baseline.png")
    archive = args.destination.with_suffix(".zip")
    temporary = archive.with_suffix(".zip.tmp")
    with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as handle:
        for path in sorted(args.destination.rglob("*")):
            if path.is_file():
                handle.write(path, path.relative_to(args.destination.parent))
    temporary.replace(archive)
    print(archive)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
