#!/usr/bin/env python3
"""Create a review-sized Phase 2.2C archive while retaining full Parquet on server."""
from __future__ import annotations

import argparse
import json
import os
import zipfile
from datetime import UTC, datetime
from pathlib import Path


AUDIT_NAMES = {
    "strategy_workbook_conversion_manifest.csv",
    "strategy_conversion_review.csv",
    "registered_strategy_manifest.csv",
    "strategy_family_manifest.csv",
    "parameter_search_manifest.csv",
    "phase2_2c_blocker_set_signatures.csv",
    "phase2_2c_dormant_contract_audit.csv",
    "phase2_2c_strategy_closure.csv",
    "phase2_2c_contract_bundle_impact.csv",
    "phase2_2c_status_transitions.csv",
    "phase2_2c_backtest_summary.csv",
    "phase2_2c_validation_summary.json",
    "validation_summary.json",
}


def atomic_json(path: Path, value: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deliverable-root", type=Path, required=True)
    parser.add_argument("--audit-root", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--status", type=Path)
    args = parser.parse_args()

    pngs = list(args.deliverable_root.rglob("*.png"))
    if len(pngs) != 656:
        raise ValueError(f"expected 656 figures, found {len(pngs)}")
    selected = [
        path for path in args.deliverable_root.rglob("*")
        if path.is_file()
        and (
            path.suffix.lower() in {".png", ".json", ".yaml", ".html"}
            or path.parent == args.deliverable_root and path.suffix.lower() == ".csv"
        )
    ]
    audit = [args.audit_root / name for name in sorted(AUDIT_NAMES)]
    missing = [str(path) for path in audit if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"missing required audit artifacts: {missing}")

    args.archive.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.archive.with_suffix(args.archive.suffix + ".tmp")
    with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(selected):
            archive.write(path, Path("deliverables") / path.relative_to(args.deliverable_root))
        for path in audit:
            archive.write(path, Path("audit") / path.name)
    os.replace(temporary, args.archive)

    result = {
        "status": "complete",
        "stage": "complete",
        "strategy_count": 41,
        "five_year_case_count": 82,
        "figure_count": len(pngs),
        "review_archive_file_count": len(selected) + len(audit),
        "review_archive": str(args.archive),
        "review_archive_bytes": args.archive.stat().st_size,
        "full_backtest_root": str(args.deliverable_root.parent.parent / "batches" / args.deliverable_root.name),
        "deliverable_root": str(args.deliverable_root),
        "audit_root": str(args.audit_root),
        "finished_at_utc": datetime.now(UTC).isoformat(),
    }
    if args.status:
        atomic_json(args.status, result)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
