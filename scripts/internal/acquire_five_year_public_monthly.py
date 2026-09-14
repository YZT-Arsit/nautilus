#!/usr/bin/env python3
"""Parallel resumable acquisition of official monthly BTC maker data.

Public bookTicker months cover June-2023 through February-2024 here; the May
edge is acquired daily and the validated March pilot is reused. Raw trades are
acquired monthly for the complete frozen five-year window.
"""

from __future__ import annotations

import argparse
import atexit
import json
import os
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.internal.acquire_l1_maker_pilot_data import (  # noqa: E402
    convert_book,
    convert_trades,
    expected_checksum,
    sha256,
)


def atomic_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    frame.to_csv(temporary, index=False)
    os.replace(temporary, path)


def month_range(start: date, end_exclusive: date) -> list[date]:
    result: list[date] = []
    current = start.replace(day=1)
    while current < end_exclusive:
        result.append(current)
        current = date(current.year + (current.month == 12), current.month % 12 + 1, 1)
    return result


def next_month(value: date) -> date:
    return date(value.year + (value.month == 12), value.month % 12 + 1, 1)


def download(url: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".part")
    temporary.unlink(missing_ok=True)
    executable = "curl.exe" if os.name == "nt" else "curl"
    subprocess.run(
        [
            executable, "--fail", "--location", "--retry", "5", "--retry-all-errors",
            "--connect-timeout", "60", "--max-time", "7200", "--silent", "--show-error",
            "--output", str(temporary), url,
        ],
        check=True,
    )
    os.replace(temporary, destination)


def tasks() -> list[tuple[str, date]]:
    book = [("bookTicker", month) for month in month_range(date(2023, 6, 1), date(2024, 3, 1))]
    trades = [("trades", month) for month in month_range(date(2021, 7, 1), date(2026, 7, 1))]
    return book + trades


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
    lock = report / f".monthly_worker_{args.worker_index}_of_{args.worker_count}.lock"
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
    manifest_path = report / f"acquisition_manifest_monthly_{args.worker_index}_of_{args.worker_count}.csv"
    prior = pd.read_csv(manifest_path) if manifest_path.exists() else pd.DataFrame()
    rows = {str(row.partition): row._asdict() for row in prior.itertuples(index=False)}
    assigned = [task for index, task in enumerate(tasks()) if index % args.worker_count == args.worker_index]
    disk_root = Path(args.output_root.resolve().anchor)

    for data_type, month in assigned:
        partition = f"{data_type}:{month:%Y-%m}"
        folder = "l1_quotes" if data_type == "bookTicker" else "raw_trades"
        destination = args.output_root.resolve() / folder / f"symbol={args.symbol}" / f"year={month.year}" / f"month={month.month:02d}" / "part.parquet"
        old = rows.get(partition)
        if old and old.get("validation_status") == "PASSED" and destination.is_file() and sha256(destination) == str(old.get("output_sha256")):
            continue
        usage = shutil.disk_usage(disk_root)
        reserve = int(usage.total * args.minimum_free_fraction)
        if usage.free - 20 * 1024**3 < reserve:
            raise RuntimeError(f"disk safety gate: free={usage.free}, reserve={reserve}")

        archive_name = f"{args.symbol}-{data_type}-{month:%Y-%m}.zip"
        base = f"https://data.binance.vision/data/futures/um/monthly/{data_type}/{args.symbol}/{archive_name}"
        temp = args.temp_root.resolve() / f"worker={args.worker_index}" / data_type / archive_name
        checksum_path = temp.with_suffix(temp.suffix + ".CHECKSUM")
        download(base, temp)
        download(base + ".CHECKSUM", checksum_path)
        expected = expected_checksum(checksum_path)
        actual = sha256(temp)
        if actual != expected:
            raise ValueError(f"source checksum mismatch: {archive_name}")
        metrics = convert_book(temp, destination) if data_type == "bookTicker" else convert_trades(temp, destination)
        compressed = temp.stat().st_size
        coverage_end = next_month(month)
        row = {
            "symbol": args.symbol, "data_type": data_type, "date": month.isoformat(),
            "partition": partition, "coverage_start": month.isoformat(), "coverage_end": coverage_end.isoformat(),
            "coverage_days": (coverage_end - month).days,
            "source": "Binance Vision USD-M Futures monthly archive", "source_path_archive": base,
            "source_checksum": expected, "rows": int(metrics["rows"]),
            "first_timestamp": metrics["first_timestamp"], "last_timestamp": metrics["last_timestamp"],
            "output_path": str(destination), "output_sha256": sha256(destination),
            "validation_status": "PASSED", "compressed_bytes": compressed,
            "uncompressed_bytes": int(metrics["uncompressed_bytes"]), "converted_bytes": destination.stat().st_size,
            "bytes_reclaimed": compressed, "provenance_mode": "MONTHLY_DOWNLOADED_CHECKSUM_VALIDATED_CONVERTED",
        }
        rows[partition] = row
        atomic_csv(pd.DataFrame(rows.values()).sort_values("partition"), manifest_path)
        temp.unlink(missing_ok=True)
        checksum_path.unlink(missing_ok=True)

    print(json.dumps({"worker": args.worker_index, "assigned": len(assigned), "completed": len(rows)}, indent=2))


if __name__ == "__main__":
    main()
