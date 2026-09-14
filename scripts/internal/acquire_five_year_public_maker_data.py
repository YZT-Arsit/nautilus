#!/usr/bin/env python3
"""Resumably acquire all public Binance BTC maker-data partitions.

Daily source ZIPs are checksum-validated, streamed into Parquet and deleted.
Existing validated pilot Parquet is reused via NTFS hard links where possible.
"""

from __future__ import annotations

import argparse
import atexit
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.internal.acquire_l1_maker_pilot_data import (  # noqa: E402
    archive_urls,
    convert_book,
    convert_trades,
    expected_checksum,
    sha256,
)
from scripts.internal.audit_long_horizon_5y_data import list_prefix, parse_objects  # noqa: E402


START = date(2021, 7, 1)
END = date(2026, 7, 1)


def atomic_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    frame.to_csv(temporary, index=False)
    os.replace(temporary, path)


def append_logical_manifest(rows: dict[tuple[str, str], dict[str, object]], path: Path) -> None:
    ordered = pd.DataFrame(rows.values()).sort_values(["date", "data_type"])
    atomic_csv(ordered, path)


def pilot_rows(pilot_root: Path, symbol: str) -> dict[tuple[str, str], dict[str, object]]:
    path = pilot_root / f"ingest_manifest_{symbol}.csv"
    if not path.exists():
        return {}
    frame = pd.read_csv(path)
    return {(str(row.date), str(row.data_type)): row._asdict() for row in frame.itertuples(index=False)}


def hardlink_or_copy(source: Path, destination: Path) -> str:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.unlink(missing_ok=True)
    try:
        os.link(source, temporary)
        mode = "HARDLINK_REUSE_VALIDATED_PILOT"
    except OSError:
        shutil.copy2(source, temporary)
        mode = "COPY_REUSE_VALIDATED_PILOT"
    os.replace(temporary, destination)
    return mode


def download(url: str, destination: Path) -> None:
    """Download atomically with curl, which is reliable on the Windows host."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".part")
    temporary.unlink(missing_ok=True)
    executable = "curl.exe" if os.name == "nt" else "curl"
    subprocess.run(
        [
            executable, "--fail", "--location", "--retry", "5",
            "--retry-all-errors", "--connect-timeout", "60",
            "--max-time", "1800", "--silent", "--show-error",
            "--output", str(temporary), url,
        ],
        check=True,
    )
    os.replace(temporary, destination)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--data-type", choices=["bookTicker", "trades", "all"], default="all")
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--report-root", type=Path, required=True)
    parser.add_argument("--pilot-root", type=Path, required=True)
    parser.add_argument("--temp-root", type=Path, required=True)
    parser.add_argument("--minimum-free-fraction", type=float, default=0.20)
    args = parser.parse_args()

    symbol = args.symbol
    output_root = args.output_root.resolve()
    report_root = args.report_root.resolve()
    pilot_root = args.pilot_root.resolve()
    temp_root = args.temp_root.resolve()
    report_root.mkdir(parents=True, exist_ok=True)
    lock_path = report_root / f".acquire_{symbol}_{args.data_type}.lock"
    try:
        lock_fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise RuntimeError(f"acquisition already running or stale lock exists: {lock_path}") from exc
    os.write(lock_fd, f"pid={os.getpid()}\n".encode())

    def release_lock() -> None:
        try:
            os.close(lock_fd)
        except OSError:
            pass
        lock_path.unlink(missing_ok=True)

    atexit.register(release_lock)
    manifest_path = report_root / "acquisition_manifest.csv"
    prior_frame = pd.read_csv(manifest_path) if manifest_path.exists() else pd.DataFrame()
    rows = {
        (str(row.date), str(row.data_type)): row._asdict()
        for row in prior_frame.itertuples(index=False)
        if str(row.symbol) == symbol
    }
    reusable = pilot_rows(pilot_root, symbol)
    data_types = ["bookTicker", "trades"] if args.data_type == "all" else [args.data_type]
    disk_root = Path(output_root.anchor or str(output_root))
    peak_temp = 0
    downloaded = converted = reclaimed = 0

    for data_type in data_types:
        prefix = f"data/futures/um/daily/{data_type}/{symbol}/"
        try:
            objects = list_prefix(prefix)
            archives, checksums = parse_objects(symbol, data_type, objects)
            target_days = sorted(day for day in archives if START <= day < END)
        except Exception:
            # Some server networks can reach the CloudFront archive host but
            # not the regional S3 listing endpoint. The date intervals below
            # are frozen from the prior successful S3 metadata audit.
            begin = date(2023, 5, 16) if data_type == "bookTicker" else START
            finish = date(2024, 3, 31) if data_type == "bookTicker" else END
            target_days = [begin + timedelta(days=index) for index in range((finish - begin).days)]
            archives = {day: 0 for day in target_days}
            checksums = set(target_days)
        for day in target_days:
            day_text = day.isoformat()
            destination = (
                output_root
                / ("l1_quotes" if data_type == "bookTicker" else "raw_trades")
                / f"symbol={symbol}"
                / f"year={day.year}"
                / f"date={day_text}"
                / "part.parquet"
            )
            prior = rows.get((day_text, data_type))
            if prior and str(prior.get("validation_status")) == "PASSED" and destination.exists():
                if prior.get("output_sha256") == sha256(destination):
                    continue

            pilot = reusable.get((day_text, data_type))
            pilot_path = Path(str(pilot.get("converted_path", ""))) if pilot else Path()
            if pilot and str(pilot.get("status")) == "PASSED" and bool(pilot.get("checksum_valid")) and pilot_path.is_file():
                provenance = hardlink_or_copy(pilot_path, destination)
                row = {
                    "symbol": symbol, "data_type": data_type, "date": day_text,
                    "source": "Binance Vision USD-M Futures daily archive",
                    "source_path_archive": str(pilot.get("filename", "")),
                    "source_checksum": str(pilot.get("converted_sha256", "")),
                    "rows": int(pilot.get("rows", 0)),
                    "first_timestamp": pilot.get("first_timestamp", ""),
                    "last_timestamp": pilot.get("last_timestamp", ""),
                    "output_path": str(destination), "output_sha256": sha256(destination),
                    "validation_status": "PASSED",
                    "compressed_bytes": int(pilot.get("compressed_bytes", 0)),
                    "uncompressed_bytes": int(pilot.get("uncompressed_bytes", 0)),
                    "converted_bytes": destination.stat().st_size,
                    "bytes_reclaimed": 0, "provenance_mode": provenance,
                }
                rows[(day_text, data_type)] = row
                append_logical_manifest(rows, manifest_path)
                continue

            usage = shutil.disk_usage(disk_root)
            reserve = int(usage.total * args.minimum_free_fraction)
            # Three GiB is deliberately conservative when listing metadata is
            # unavailable; actual largest public daily source is much smaller.
            source_bytes = int(archives[day]) or 3 * 1024**3
            if usage.free - source_bytes * 3 < reserve:
                raise RuntimeError(
                    f"disk safety gate: free={usage.free}, source={source_bytes}, reserve={reserve}"
                )

            archive_name, archive_url, checksum_url = archive_urls(symbol, day_text, data_type)
            archive = temp_root / data_type / archive_name
            checksum_path = archive.with_suffix(archive.suffix + ".CHECKSUM")
            download(archive_url, archive)
            download(checksum_url, checksum_path)
            expected = expected_checksum(checksum_path)
            actual = sha256(archive)
            if actual != expected:
                raise ValueError(f"checksum mismatch: {archive_name}")
            metrics = convert_book(archive, destination) if data_type == "bookTicker" else convert_trades(archive, destination)
            peak_temp = max(peak_temp, archive.stat().st_size)
            downloaded += archive.stat().st_size
            converted += destination.stat().st_size
            compressed_bytes = archive.stat().st_size
            archive.unlink(missing_ok=True)
            checksum_path.unlink(missing_ok=True)
            reclaimed += compressed_bytes
            row = {
                "symbol": symbol, "data_type": data_type, "date": day_text,
                "source": "Binance Vision USD-M Futures daily archive",
                "source_path_archive": archive_url, "source_checksum": expected,
                "rows": int(metrics["rows"]), "first_timestamp": metrics["first_timestamp"],
                "last_timestamp": metrics["last_timestamp"], "output_path": str(destination),
                "output_sha256": sha256(destination), "validation_status": "PASSED",
                "compressed_bytes": compressed_bytes, "uncompressed_bytes": int(metrics["uncompressed_bytes"]),
                "converted_bytes": destination.stat().st_size, "bytes_reclaimed": compressed_bytes,
                "provenance_mode": "DOWNLOADED_CHECKSUM_VALIDATED_CONVERTED",
            }
            rows[(day_text, data_type)] = row
            append_logical_manifest(rows, manifest_path)

    final = pd.DataFrame(rows.values()).sort_values(["date", "data_type"])
    atomic_csv(final, report_root / "five_year_maker_data_manifest.csv")
    status = {
        "symbol": symbol,
        "partitions_passed": int(final.validation_status.eq("PASSED").sum()),
        "bookTicker_partitions": int((final.data_type.eq("bookTicker") & final.validation_status.eq("PASSED")).sum()),
        "trade_partitions": int((final.data_type.eq("trades") & final.validation_status.eq("PASSED")).sum()),
        "bytes_downloaded_this_run": downloaded,
        "converted_bytes_this_run": converted,
        "peak_temporary_bytes_this_run": peak_temp,
        "bytes_reclaimed_this_run": reclaimed,
        "disk_free_bytes": shutil.disk_usage(disk_root).free,
        "full_maker_backtest_started": False,
    }
    (report_root / "acquisition_runtime_status.json").write_text(json.dumps(status, indent=2), encoding="utf-8")
    print(json.dumps(status, indent=2))


if __name__ == "__main__":
    main()
