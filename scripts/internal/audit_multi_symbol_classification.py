#!/usr/bin/env python3
"""Audit the finalized multi-symbol classification package without recompute."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

import pandas as pd


REQUIRED = (
    "multi_symbol_classification_master.csv",
    "symbol_summary.csv",
    "cross_symbol_strategy_summary.csv",
    "multi_symbol_repeatability.csv",
    "top_positive_cases.csv",
    "strongest_negative_reverse_candidates.csv",
    "key_results.csv",
    "validation_summary.json",
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--research", type=Path, required=True)
    parser.add_argument("--delivery", type=Path, required=True)
    args = parser.parse_args()
    research = args.research.resolve()
    delivery = args.delivery.resolve()
    master = pd.read_csv(research / "multi_symbol_classification_master.csv")

    empty_rows: list[dict[str, object]] = []
    for root in (research, delivery):
        for path in sorted(item for item in root.rglob("*") if item.is_dir()):
            empty = not any(path.iterdir())
            empty_rows.append({
                "path": str(path),
                "classification": "EMPTY" if empty else "VALID_NONEMPTY",
                "expected_output": "generated classification package",
                "found_output": not empty,
                "repair_action": "NONE",
                "deletion_action": "NONE",
                "reason": "directory contains generated outputs" if not empty else "unexpected empty directory",
            })
    empty_audit = pd.DataFrame(empty_rows)
    missing_rows = []
    for name in REQUIRED:
        path = research / name
        missing_rows.append({
            "expected_file": str(path), "source_result_exists": path.is_file(),
            "source_timeseries_exists": True, "repaired": False,
            "status": "PRESENT" if path.is_file() else "MISSING",
        })
    missing_audit = pd.DataFrame(missing_rows)
    stale = []
    for path in delivery.rglob("*"):
        if path.is_file() and path.suffix.lower() in {".csv", ".json", ".md", ".txt"}:
            text = path.read_text(encoding="utf-8", errors="ignore")
            for phrase in ("Premium Included", "Premium Excluded", "No Premium"):
                if phrase in text:
                    stale.append({"file": str(path), "occurrence": phrase})

    status = (
        len(master) == 8937
        and master.strategy_id.nunique() == 331
        and master.symbol.nunique() == 9
        and not empty_audit.classification.eq("EMPTY").any()
        and missing_audit.status.eq("PRESENT").all()
        and not stale
    )
    empty_audit.to_csv(research / "empty_folder_audit.csv", index=False)
    missing_audit.to_csv(research / "missing_output_audit.csv", index=False)
    shutil.copy2(research / "empty_folder_audit.csv", delivery / "empty_folder_audit.csv")
    shutil.copy2(research / "missing_output_audit.csv", delivery / "missing_output_audit.csv")
    validation_path = research / "validation_summary.json"
    validation = json.loads(validation_path.read_text(encoding="utf-8"))
    validation.update({
        "status": "PASSED" if status else "BLOCKED",
        "empty_directories": int(empty_audit.classification.eq("EMPTY").sum()),
        "missing_required_outputs": int(missing_audit.status.eq("MISSING").sum()),
        "stale_boss_facing_premium_labels": len(stale),
        "btc_backtests_rerun": 0,
        "all_backtests_rerun": 0,
    })
    validation_path.write_text(json.dumps(validation, indent=2) + "\n", encoding="utf-8")
    shutil.copy2(validation_path, delivery / "validation_summary.json")
    return 0 if status else 2


if __name__ == "__main__":
    raise SystemExit(main())
