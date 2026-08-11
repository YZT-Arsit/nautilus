#!/usr/bin/env python3
"""Audit whether the canonical BTCUSDT aggTrades backfill is implementation-ready."""

from __future__ import annotations

import argparse
import json
import shutil
import time
from datetime import UTC
from datetime import date
from datetime import datetime
from datetime import timedelta
from pathlib import Path

import pyarrow.parquet as pq


MIN_FREE_BYTES = 50 * 1024**3


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default="2021-07-01")
    parser.add_argument("--end", default="2026-06-30")
    parser.add_argument("--tick-root", type=Path, required=True)
    parser.add_argument("--progress-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--watch-seconds", type=int, default=0)
    return parser.parse_args()


def expected_dates(start: date, end: date) -> list[date]:
    result: list[date] = []
    cursor = start
    while cursor <= end:
        result.append(cursor)
        cursor += timedelta(days=1)
    return result


def schema_signature(path: Path) -> tuple[tuple[str, str], ...]:
    schema = pq.ParquetFile(path).schema_arrow
    return tuple((field.name, str(field.type)) for field in schema)


def timestamp_bounds(path: Path) -> tuple[datetime | None, datetime | None]:
    parquet = pq.ParquetFile(path)
    names = parquet.schema_arrow.names
    if "ts" not in names:
        return None, None
    index = names.index("ts")
    minima: list[datetime] = []
    maxima: list[datetime] = []
    for row_group_index in range(parquet.metadata.num_row_groups):
        statistics = parquet.metadata.row_group(row_group_index).column(index).statistics
        if statistics is None or not statistics.has_min_max:
            return None, None
        minima.append(statistics.min)
        maxima.append(statistics.max)
    return min(minima), max(maxima)


def worker_statuses(root: Path) -> list[dict]:
    paths = [root / "progress.json", *sorted(root.glob("worker_*/progress.json"))]
    result: list[dict] = []
    for path in paths:
        if not path.is_file():
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        result.append(
            {
                "path": str(path),
                "processed_days": payload.get("processed_days"),
                "total_days": payload.get("total_days"),
                "last_day": payload.get("last_day"),
                "last_status": payload.get("last_status"),
                "failure_count": payload.get("failure_count", 0),
            }
        )
    return result


def audit(args: argparse.Namespace) -> dict:
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    expected = expected_dates(start, end)
    missing: list[str] = []
    invalid: list[dict] = []
    schema_signatures: dict[tuple[tuple[str, str], ...], int] = {}
    total_files = 0
    total_rows = 0
    total_bytes = 0

    for day in expected:
        partition = args.tick_root / f"date={day.isoformat()}"
        files = sorted(partition.glob("*.parquet"))
        if not files:
            missing.append(day.isoformat())
            continue
        for path in files:
            total_files += 1
            size = path.stat().st_size
            total_bytes += size
            if size <= 0:
                invalid.append({"date": day.isoformat(), "path": str(path), "issue": "empty"})
                continue
            try:
                parquet = pq.ParquetFile(path)
                total_rows += parquet.metadata.num_rows
                signature = schema_signature(path)
                minimum, maximum = timestamp_bounds(path)
            except Exception as exc:  # noqa: BLE001 - external file may still be in-flight
                invalid.append(
                    {
                        "date": day.isoformat(),
                        "path": str(path),
                        "issue": "unreadable_parquet",
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    }
                )
                continue
            schema_signatures[signature] = schema_signatures.get(signature, 0) + 1
            if minimum is None or maximum is None:
                invalid.append(
                    {"date": day.isoformat(), "path": str(path), "issue": "missing_ts_stats"}
                )
                continue
            day_start = datetime.combine(day, datetime.min.time(), tzinfo=UTC).replace(tzinfo=None)
            day_end = day_start + timedelta(days=1)
            if minimum < day_start or maximum >= day_end:
                invalid.append(
                    {
                        "date": day.isoformat(),
                        "path": str(path),
                        "issue": "timestamp_outside_partition",
                        "min": minimum.isoformat(),
                        "max": maximum.isoformat(),
                    }
                )

    workers = worker_statuses(args.progress_root)
    worker_failure_count = sum(int(item["failure_count"] or 0) for item in workers)
    workers_complete = len(workers) >= 3 and all(
        item["processed_days"] == item["total_days"] for item in workers
    )
    disk = shutil.disk_usage(args.tick_root)
    ready = (
        not missing
        and not invalid
        and len(schema_signatures) == 1
        and worker_failure_count == 0
        and workers_complete
        and disk.free >= MIN_FREE_BYTES
    )
    return {
        "checked_at_utc": datetime.now(UTC).isoformat(),
        "ready": ready,
        "start": args.start,
        "end": args.end,
        "expected_partition_count": len(expected),
        "present_partition_count": len(expected) - len(missing),
        "missing_partition_count": len(missing),
        "missing_partitions": missing,
        "invalid_file_count": len(invalid),
        "invalid_files": invalid,
        "parquet_file_count": total_files,
        "total_rows": total_rows,
        "total_bytes": total_bytes,
        "schema_variant_count": len(schema_signatures),
        "schema_variants": [
            {"file_count": count, "fields": list(signature)}
            for signature, count in schema_signatures.items()
        ],
        "worker_failure_count": worker_failure_count,
        "workers_complete": workers_complete,
        "workers": workers,
        "disk_free_bytes": disk.free,
        "minimum_required_free_bytes": MIN_FREE_BYTES,
    }


def write_payload(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    args = parse_args()
    while True:
        payload = audit(args)
        write_payload(args.output, payload)
        print(
            f"ready={payload['ready']} "
            f"partitions={payload['present_partition_count']}/"
            f"{payload['expected_partition_count']} "
            f"invalid={payload['invalid_file_count']} "
            f"worker_failures={payload['worker_failure_count']}",
            flush=True,
        )
        if payload["ready"] or args.watch_seconds <= 0:
            return 0 if payload["ready"] else 1
        time.sleep(args.watch_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
