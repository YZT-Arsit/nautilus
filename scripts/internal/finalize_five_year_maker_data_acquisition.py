#!/usr/bin/env python3
"""Reconcile five-year maker acquisition progress and validate reports."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from datetime import date, timedelta
from pathlib import Path

import pandas as pd


START = date(2021, 7, 1)
END = date(2026, 7, 1)
EXPECTED = (END - START).days
PUBLIC_BOOK_START = date(2023, 5, 16)
PUBLIC_BOOK_END = date(2024, 3, 31)


def atomic_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    frame.to_csv(temporary, index=False)
    os.replace(temporary, path)


def atomic_json(value: object, path: Path) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(temporary, path)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report-root", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, required=True)
    args = parser.parse_args()
    report = args.report_root.resolve()
    data = args.data_root.resolve()
    manifest_path = report / "acquisition_manifest.csv"
    manifest_files = [manifest_path, *sorted(report.glob("acquisition_manifest_monthly_*.csv"))]
    frames = [pd.read_csv(path) for path in manifest_files if path.exists() and path.stat().st_size]
    manifest = pd.concat(frames, ignore_index=True, sort=False) if frames else pd.DataFrame()
    if len(manifest):
        manifest["partition"] = manifest.get("partition", manifest.data_type.astype(str) + ":" + manifest.date.astype(str))
        manifest["partition"] = manifest.partition.fillna(manifest.data_type.astype(str) + ":" + manifest.date.astype(str))
        manifest["coverage_start"] = manifest.get("coverage_start", manifest.date).fillna(manifest.date)
        manifest["coverage_end"] = manifest.get("coverage_end", "")
        missing_end = manifest.coverage_end.isna() | manifest.coverage_end.astype(str).eq("")
        manifest.loc[missing_end, "coverage_end"] = manifest.loc[missing_end, "date"].map(
            lambda value: (date.fromisoformat(str(value)) + timedelta(days=1)).isoformat()
        )
        if "coverage_days" not in manifest:
            manifest["coverage_days"] = 1
        else:
            manifest["coverage_days"] = manifest.coverage_days.fillna(1).astype(int)
    if len(manifest):
        manifest = manifest.drop_duplicates(["symbol", "data_type", "partition"], keep="last")
        manifest = manifest.sort_values(["symbol", "data_type", "coverage_start"])
    atomic_csv(manifest, manifest_path)
    atomic_csv(manifest, report / "five_year_maker_data_manifest.csv")

    passed = manifest[manifest.validation_status.eq("PASSED")].copy() if len(manifest) else manifest.copy()
    output_exists = []
    hash_matches = []
    for row in passed.itertuples(index=False):
        path = Path(str(row.output_path))
        exists = path.is_file()
        output_exists.append(exists)
        hash_matches.append(exists and sha256(path) == str(row.output_sha256))
    if len(passed):
        passed["output_exists"] = output_exists
        passed["output_hash_valid"] = hash_matches
    valid = passed[passed.output_exists & passed.output_hash_valid] if len(passed) else passed

    book = valid[valid.data_type.eq("bookTicker")]
    trades = valid[valid.data_type.eq("trades")]
    expected_book_dates = {
        (PUBLIC_BOOK_START + timedelta(days=i)).isoformat()
        for i in range((PUBLIC_BOOK_END - PUBLIC_BOOK_START).days)
    }
    expected_trade_dates = {(START + timedelta(days=i)).isoformat() for i in range(EXPECTED)}
    def covered_dates(frame: pd.DataFrame) -> set[str]:
        result: set[str] = set()
        for row in frame.itertuples(index=False):
            begin = date.fromisoformat(str(row.coverage_start))
            finish = date.fromisoformat(str(row.coverage_end))
            result.update((begin + timedelta(days=index)).isoformat() for index in range((finish - begin).days))
        return result

    valid_book_dates = covered_dates(book)
    valid_trade_dates = covered_dates(trades)
    public_complete = expected_book_dates.issubset(valid_book_dates) and expected_trade_dates.issubset(valid_trade_dates)
    five_year_ready = EXPECTED == len(valid_book_dates.intersection(expected_trade_dates))

    status = pd.read_csv(report / "five_year_maker_data_status.csv")
    status.loc[:, "quote_days_downloaded_validated"] = len(valid_book_dates)
    status.loc[:, "trade_days_downloaded_validated"] = len(valid_trade_dates)
    status.loc[:, "public_partitions_fully_acquired"] = public_complete
    status.loc[:, "five_year_maker_ready"] = five_year_ready
    atomic_csv(status, report / "five_year_maker_data_status.csv")

    run_manifest = pd.read_csv(report / "five_year_maker_run_manifest.csv")
    run_manifest.loc[:, "quote_data_ready"] = five_year_ready
    run_manifest.loc[:, "trade_data_ready"] = len(valid_trade_dates) == EXPECTED
    atomic_csv(run_manifest, report / "five_year_maker_run_manifest.csv")

    disk = shutil.disk_usage(data.anchor or str(data))
    locks = list(report.glob(".acquire_*.lock"))
    downloaded_mask = valid.provenance_mode.astype(str).isin(
        ["DOWNLOADED_CHECKSUM_VALIDATED_CONVERTED", "MONTHLY_DOWNLOADED_CHECKSUM_VALIDATED_CONVERTED"]
    ) if len(valid) else pd.Series(dtype=bool)
    downloaded_now = valid[downloaded_mask] if len(valid) else valid
    persistent_paths = {Path(str(value)) for value in downloaded_now.output_path} if len(downloaded_now) else set()
    persistent_bytes = sum(path.stat().st_size for path in persistent_paths if path.is_file())
    summary = {
        "status": "DATA_READY" if five_year_ready else ("PARTIAL_DATA_ACQUIRED" if len(valid) else "USER_ACTION_REQUIRED"),
        "frozen_start": START.isoformat(),
        "frozen_end_exclusive": END.isoformat(),
        "required_days": EXPECTED,
        "required_symbols": ["BTCUSDT"],
        "required_symbol_count": 1,
        "public_bookTicker_days_offered": len(expected_book_dates),
        "public_bookTicker_days_downloaded_validated": len(valid_book_dates),
        "public_trade_days_offered": EXPECTED,
        "public_trade_days_downloaded_validated": len(valid_trade_dates),
        "five_year_ready_symbols": 1 if five_year_ready else 0,
        "partial_symbols": 0 if five_year_ready else 1,
        "missing_symbols": 0,
        "compressed_source_bytes_processed": int(valid.compressed_bytes.sum()) if len(valid) else 0,
        "data_downloaded_bytes_this_task": int(downloaded_now.compressed_bytes.sum()) if len(downloaded_now) else 0,
        "converted_bytes_including_reused_pilot": int(valid.converted_bytes.sum()) if len(valid) else 0,
        "converted_bytes_persisted_this_task": persistent_bytes,
        "final_persistent_storage_bytes_this_task": persistent_bytes,
        "bytes_reclaimed": int(valid.bytes_reclaimed.sum()) if len(valid) else 0,
        "peak_temporary_bytes": int(valid.compressed_bytes.max()) if len(valid) else 0,
        "disk_total_bytes": disk.total,
        "disk_free_bytes": disk.free,
        "disk_free_fraction": disk.free / disk.total,
        "minimum_free_fraction": 0.20,
        "disk_safety_passed": disk.free / disk.total >= 0.20,
        "acquisition_process_lock_present": bool(locks),
        "public_portion_complete": public_complete,
        "paid_or_authenticated_source_required": not five_year_ready,
        "user_action_required": "NONE" if five_year_ready else "Provide/authorize a Binance Futures API key whitelisted for historical order-book downloads, or approve and provide a complete vendor L1/L2 entitlement",
        "full_maker_backtest_started": False,
        "live_trading_used": False,
        "validation": "PASSED" if all(hash_matches) and disk.free / disk.total >= 0.20 else "PARTIAL",
    }
    atomic_json(summary, report / "validation_summary.json")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
