#!/usr/bin/env python3
"""
Backfill Binance USD-M aggTrades one UTC day at a time into canonical Hive data.

Each date runs through the existing ``ingest_binance_vision.py`` entrypoint in
an isolated subprocess. Existing non-empty date partitions are skipped.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import date
from datetime import timedelta
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--market-root", type=Path, required=True)
    parser.add_argument("--progress-dir", type=Path, required=True)
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--newest-first", action="store_true")
    return parser.parse_args()


def dates_between(start: date, end: date, newest_first: bool) -> list[date]:
    values: list[date] = []
    cursor = start
    while cursor <= end:
        values.append(cursor)
        cursor += timedelta(days=1)
    return list(reversed(values)) if newest_first else values


def target_partition(root: Path, symbol: str, day: date) -> Path:
    return (
        root
        / "asset_class=crypto"
        / "exchange=BINANCE"
        / "venue_type=futures_um"
        / f"symbol={symbol}"
        / "data_type=trade"
        / "freq=tick"
        / f"date={day.isoformat()}"
    )


def partition_complete(path: Path) -> bool:
    return any(file.stat().st_size > 0 for file in path.glob("*.parquet"))


def write_progress(path: Path, payload: dict) -> None:
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    args = parse_args()
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    if end < start:
        raise ValueError("--end must be on or after --start")
    args.progress_dir.mkdir(parents=True, exist_ok=True)
    log_path = args.progress_dir / "download.log"
    progress_path = args.progress_dir / "progress.json"
    days = dates_between(start, end, args.newest_first)
    completed: list[str] = []
    skipped: list[str] = []
    failures: dict[str, str] = {}

    with log_path.open("a", encoding="utf-8") as log:
        for index, day in enumerate(days, start=1):
            day_text = day.isoformat()
            partition = target_partition(args.market_root, args.symbol, day)
            if partition_complete(partition):
                skipped.append(day_text)
                status = "existing"
            else:
                command = [
                    sys.executable,
                    "scripts/ingest_binance_vision.py",
                    "--market",
                    "futures_um",
                    "--symbol",
                    args.symbol,
                    "--data-type",
                    "aggTrades",
                    "--frequency",
                    "daily",
                    "--start",
                    day_text,
                    "--end",
                    day_text,
                    "--output",
                    str(args.market_root),
                    "--timeout",
                    str(args.timeout),
                    "--overwrite",
                ]
                status = "failed"
                for attempt in range(1, args.retries + 1):
                    log.write(f"[{day_text}] attempt {attempt}: {' '.join(command)}\n")
                    log.flush()
                    result = subprocess.run(  # noqa: S603 - argv list, no shell
                        command,
                        stdout=log,
                        stderr=subprocess.STDOUT,
                        check=False,
                    )
                    if result.returncode == 0 and partition_complete(partition):
                        completed.append(day_text)
                        failures.pop(day_text, None)
                        status = "downloaded"
                        break
                    failures[day_text] = f"returncode={result.returncode}, attempt={attempt}"
                    time.sleep(min(30, attempt * 5))

            parquet_files = list(partition.glob("*.parquet"))
            bytes_written = sum(file.stat().st_size for file in parquet_files)
            payload = {
                "symbol": args.symbol,
                "market": "futures_um",
                "data_type": "aggTrades",
                "canonical_data_type": "trade",
                "canonical_frequency": "tick",
                "start": args.start,
                "end": args.end,
                "newest_first": args.newest_first,
                "total_days": len(days),
                "processed_days": index,
                "last_day": day_text,
                "last_status": status,
                "last_partition": str(partition),
                "last_partition_bytes": bytes_written,
                "downloaded_count_this_run": len(completed),
                "existing_count_this_run": len(skipped),
                "failure_count": len(failures),
                "failures": failures,
            }
            write_progress(progress_path, payload)
            print(
                f"[{index}/{len(days)}] {day_text}: {status}; "
                f"partition_bytes={bytes_written}",
                flush=True,
            )

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
