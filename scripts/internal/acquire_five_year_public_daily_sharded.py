#!/usr/bin/env python3
"""Shard checksum-validated daily Binance maker-data acquisition."""

from __future__ import annotations

import argparse
import atexit
import json
import os
import shutil
import subprocess
import sys
import zipfile
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.internal.acquire_l1_maker_pilot_data import (  # noqa: E402
    BOOK_COLUMNS,
    archive_urls,
    convert_book,
    convert_trades,
    expected_checksum,
    sha256,
)


START = date(2021, 7, 1)
END = date(2026, 7, 1)
PUBLIC_BOOK_START = date(2023, 5, 16)
PUBLIC_BOOK_END = date(2024, 3, 31)
PILOT_START = date(2024, 3, 1)
PILOT_END = date(2024, 3, 31)
MAY_EDGE_END = date(2023, 6, 1)


def convert_book_with_chronology_normalization(archive: Path, output: Path) -> dict:
    """Stable-sort a checksum-valid daily CSV whose time blocks are interleaved."""
    with zipfile.ZipFile(archive) as zipped:
        bad_member = zipped.testzip()
        if bad_member is not None:
            raise ValueError(f"corrupt ZIP member: {bad_member}")
        members = zipped.infolist()
        if len(members) != 1:
            raise ValueError("daily bookTicker ZIP must contain exactly one CSV")
        uncompressed = members[0].file_size
        with zipped.open(members[0]) as handle:
            frame = pd.read_csv(handle)
    if list(frame.columns) != BOOK_COLUMNS:
        raise ValueError(f"unexpected bookTicker columns: {list(frame.columns)}")
    transaction = frame.transaction_time.to_numpy(np.int64, copy=False)
    source_chronology_failures = int(np.count_nonzero(np.diff(transaction) < 0))
    bid = frame.best_bid_price.to_numpy(float, copy=False)
    ask = frame.best_ask_price.to_numpy(float, copy=False)
    bid_size = frame.best_bid_qty.to_numpy(float, copy=False)
    ask_size = frame.best_ask_qty.to_numpy(float, copy=False)
    event = frame.event_time.to_numpy(np.int64, copy=False)
    crossed = int(np.count_nonzero(bid > ask))
    bad_qty = int(np.count_nonzero((bid_size <= 0) | (ask_size <= 0)))
    bad_ts = int(np.count_nonzero((transaction <= 0) | (event <= 0)))
    if crossed or bad_qty or bad_ts:
        raise ValueError(
            f"book validation failed after source audit crossed={crossed} "
            f"bad_qty={bad_qty} bad_ts={bad_ts}"
        )
    frame.sort_values("transaction_time", kind="mergesort", inplace=True)
    sorted_ts = frame.transaction_time.to_numpy(np.int64, copy=False)
    if np.any(np.diff(sorted_ts) < 0):
        raise ValueError("chronology normalization failed")
    schema = pa.schema(
        [
            ("update_id", pa.int64()), ("bid_price", pa.float64()),
            ("bid_size", pa.float64()), ("ask_price", pa.float64()),
            ("ask_size", pa.float64()), ("ts_event_ns", pa.int64()),
            ("ts_init_ns", pa.int64()),
        ]
    )
    table = pa.table(
        {
            "update_id": frame.update_id.to_numpy(np.int64, copy=False),
            "bid_price": frame.best_bid_price.to_numpy(float, copy=False),
            "bid_size": frame.best_bid_qty.to_numpy(float, copy=False),
            "ask_price": frame.best_ask_price.to_numpy(float, copy=False),
            "ask_size": frame.best_ask_qty.to_numpy(float, copy=False),
            "ts_event_ns": sorted_ts * 1_000_000,
            "ts_init_ns": frame.event_time.to_numpy(np.int64, copy=False) * 1_000_000,
        },
        schema=schema,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.unlink(missing_ok=True)
    pq.write_table(table, temporary, compression="zstd", compression_level=6)
    os.replace(temporary, output)
    return {
        "rows": len(frame), "first_timestamp": int(sorted_ts[0]),
        "last_timestamp": int(sorted_ts[-1]), "uncompressed_bytes": uncompressed,
        "chronology_failures": 0, "source_chronology_failures": source_chronology_failures,
        "crossed_bbo_count": crossed, "nonpositive_quantity_count": bad_qty,
        "malformed_timestamp_count": bad_ts, "chronology_normalized": True,
    }


def days(begin: date, end: date) -> list[date]:
    return [begin + timedelta(days=index) for index in range((end - begin).days)]


def tasks() -> list[tuple[str, date]]:
    # The May edge and March pilot are already registered as validated daily
    # partitions. Only the still-missing public partitions are assigned.
    book = [("bookTicker", day) for day in days(MAY_EDGE_END, PILOT_START)]
    trades = [("trades", day) for day in days(START, END) if not PILOT_START <= day < PILOT_END]
    return book + trades


def atomic_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    frame.to_csv(temporary, index=False)
    os.replace(temporary, path)


def download(url: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".part")
    temporary.unlink(missing_ok=True)
    executable = "curl.exe" if os.name == "nt" else "curl"
    subprocess.run(
        [
            executable, "--fail", "--location", "--retry", "5", "--retry-all-errors",
            "--connect-timeout", "60", "--max-time", "1800", "--silent", "--show-error",
            "--output", str(temporary), url,
        ],
        check=True,
    )
    os.replace(temporary, destination)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--worker-index", type=int, required=True)
    parser.add_argument("--worker-count", type=int, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--report-root", type=Path, required=True)
    parser.add_argument("--temp-root", type=Path, required=True)
    parser.add_argument("--minimum-free-fraction", type=float, default=0.20)
    args = parser.parse_args()
    if not 0 <= args.worker_index < args.worker_count:
        raise ValueError("worker-index must be in [0, worker-count)")

    report = args.report_root.resolve()
    report.mkdir(parents=True, exist_ok=True)
    lock = report / f".daily_worker_{args.worker_index}_of_{args.worker_count}.lock"
    try:
        lock_fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise RuntimeError(f"worker already running or stale lock exists: {lock}") from exc
    os.write(lock_fd, f"pid={os.getpid()}\n".encode())

    def release() -> None:
        try:
            os.close(lock_fd)
        except OSError:
            pass
        lock.unlink(missing_ok=True)

    atexit.register(release)
    manifest_path = report / f"acquisition_manifest_daily_{args.worker_index}_of_{args.worker_count}.csv"
    prior = pd.read_csv(manifest_path) if manifest_path.exists() else pd.DataFrame()
    rows = {str(row.partition): row._asdict() for row in prior.itertuples(index=False)}
    assigned = [task for index, task in enumerate(tasks()) if index % args.worker_count == args.worker_index]
    output_root = args.output_root.resolve()
    disk_root = Path(output_root.anchor)

    for data_type, day in assigned:
        day_text = day.isoformat()
        partition = f"{data_type}:{day_text}"
        folder = "l1_quotes" if data_type == "bookTicker" else "raw_trades"
        destination = output_root / folder / f"symbol={args.symbol}" / f"year={day.year}" / f"date={day_text}" / "part.parquet"
        old = rows.get(partition)
        if old and old.get("validation_status") == "PASSED" and destination.is_file() and sha256(destination) == str(old.get("output_sha256")):
            continue
        usage = shutil.disk_usage(disk_root)
        reserve = int(usage.total * args.minimum_free_fraction)
        if usage.free - 8 * 1024**3 < reserve:
            raise RuntimeError(f"disk safety gate: free={usage.free}, reserve={reserve}")

        archive_name, archive_url, checksum_url = archive_urls(args.symbol, day_text, data_type)
        archive = args.temp_root.resolve() / f"worker={args.worker_index}" / data_type / archive_name
        checksum_path = archive.with_suffix(archive.suffix + ".CHECKSUM")
        source_reused = False
        if archive.is_file() and checksum_path.is_file():
            try:
                source_reused = sha256(archive) == expected_checksum(checksum_path)
            except (OSError, ValueError, IndexError):
                source_reused = False
        if not source_reused:
            download(archive_url, archive)
            download(checksum_url, checksum_path)
        expected = expected_checksum(checksum_path)
        actual = sha256(archive)
        if actual != expected:
            raise ValueError(f"source checksum mismatch: {archive_name}")
        chronology_normalized = False
        if data_type == "bookTicker":
            try:
                metrics = convert_book(archive, destination)
            except ValueError as exc:
                if "book validation failed order=" not in str(exc):
                    raise
                metrics = convert_book_with_chronology_normalization(archive, destination)
                chronology_normalized = True
        else:
            metrics = convert_trades(archive, destination)
        compressed = archive.stat().st_size
        row = {
            "symbol": args.symbol, "data_type": data_type, "date": day_text,
            "partition": partition, "coverage_start": day_text,
            "coverage_end": (day + timedelta(days=1)).isoformat(), "coverage_days": 1,
            "source": "Binance Vision USD-M Futures daily archive", "source_path_archive": archive_url,
            "source_checksum": expected, "rows": int(metrics["rows"]),
            "first_timestamp": metrics["first_timestamp"], "last_timestamp": metrics["last_timestamp"],
            "output_path": str(destination), "output_sha256": sha256(destination),
            "validation_status": "PASSED", "compressed_bytes": compressed,
            "uncompressed_bytes": int(metrics["uncompressed_bytes"]), "converted_bytes": destination.stat().st_size,
            "bytes_reclaimed": compressed, "provenance_mode": "DAILY_DOWNLOADED_CHECKSUM_VALIDATED_CONVERTED",
            "source_chronology_failures": int(metrics.get("source_chronology_failures", 0)),
            "chronology_normalized": chronology_normalized,
        }
        rows[partition] = row
        atomic_csv(pd.DataFrame(rows.values()).sort_values("partition"), manifest_path)
        archive.unlink(missing_ok=True)
        checksum_path.unlink(missing_ok=True)

    print(json.dumps({"worker": args.worker_index, "assigned": len(assigned), "completed": len(rows)}, indent=2))


if __name__ == "__main__":
    main()
