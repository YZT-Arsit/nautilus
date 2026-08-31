#!/usr/bin/env python3
"""Validate and promote a fully rendered workbook deliverable staging tree.

This recovery path performs no strategy execution and no figure generation. It
exists so a post-render audit failure can be corrected without recomputing the
already validated presentation artifacts.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pandas as pd
from PIL import Image

from scripts.internal.build_all_converted_workbook_results import (
    ARCHIVE,
    AUDIT,
    BUILDING,
    FINAL,
    atomic_json,
    parquet_protected_fingerprint,
    protected_fingerprint_changes,
    zip_final,
)


def main() -> None:
    if not BUILDING.is_dir() or FINAL.exists() or ARCHIVE.exists():
        raise RuntimeError("expected staging tree only; refusing unsafe promotion")
    master_path = BUILDING / "all_converted_workbook_strategies.csv"
    master = pd.read_csv(master_path)
    strategy_ids = set(master.loc[master.record_type == "STRATEGY_INDEX", "strategy_id"])
    baseline = master.loc[master.record_type == "BASELINE_RESULT"]
    if len(strategy_ids) != 280 or len(baseline) != 1120:
        raise AssertionError("staging master cardinality mismatch")
    folders = {path.name for path in (BUILDING / "strategies").iterdir() if path.is_dir()}
    if folders != strategy_ids:
        raise AssertionError("strategy folder identity mismatch")
    pngs = sorted(BUILDING.rglob("*.png"))
    csvs = sorted(BUILDING.rglob("*.csv"))
    extensions = {path.suffix.lower() for path in BUILDING.rglob("*") if path.is_file()}
    if len(pngs) != 1120 or len(csvs) != 281 or extensions != {".png", ".csv"}:
        raise AssertionError("staging file cardinality/extension mismatch")
    for path in pngs:
        with Image.open(path) as image:
            image.verify()
    for path in csvs:
        if path.stat().st_size == 0:
            raise AssertionError(f"empty CSV: {path}")

    before = pd.read_csv(AUDIT / "protected_source_hashes_before.csv")
    cases = pd.read_csv(AUDIT / "final_case_manifest.csv")
    after = pd.DataFrame([
        {"path": path, **parquet_protected_fingerprint(Path(path))}
        for path in sorted(set(cases.timeseries))
    ])
    changes = protected_fingerprint_changes(before, after)
    if len(changes):
        raise AssertionError(f"protected source changes: {len(changes)}")

    validation_path = AUDIT / "final_validation_summary.json"
    validation = json.loads(validation_path.read_text(encoding="utf-8"))
    validation.update({
        "status": "PASSED",
        "protected_source_hash_changes": 0,
        "invalid_png": 0,
        "invalid_csv": 0,
        "unexpected_extensions": [],
        "source_schema_address_normalization_applied": True,
    })
    atomic_json(validation_path, validation)
    os.replace(BUILDING, FINAL)
    digest, members, size = zip_final(FINAL, ARCHIVE)
    delivery = {
        **validation,
        "folder": str(FINAL),
        "zip": str(ARCHIVE),
        "sha256": digest,
        "zip_members": members,
        "zip_size_bytes": size,
        "zip_integrity": "PASSED",
    }
    atomic_json(AUDIT / "delivery_summary.json", delivery)
    print(json.dumps(delivery, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
