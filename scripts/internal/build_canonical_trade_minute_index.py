#!/usr/bin/env python3
"""Build an exact minute-boundary index from canonical daily trade Parquet.

The source partitions are already-ingested official Binance raw trades. This
utility performs no network access and persists only 1,440 execution records
per UTC day.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq


COLS = ["ts", "trade_id", "price", "quantity", "quote_quantity", "is_buyer_maker"]
OUT_SCHEMA = pa.schema([
    ("minute_boundary_timestamp", pa.timestamp("ms", tz="UTC")),
    ("first_trade_timestamp", pa.timestamp("ms", tz="UTC")),
    ("first_trade_id", pa.int64()),
    ("price", pa.float64()),
    ("quantity", pa.float64()),
    ("quote_quantity", pa.float64()),
    ("is_buyer_maker", pa.bool_()),
    ("wait_ms", pa.int64()),
    ("source_date", pa.string()),
    ("source_archive_name", pa.string()),
    ("source_checksum", pa.string()),
    ("source_row_index", pa.int64()),
])


def days(start: date, end_exclusive: date) -> list[date]:
    return [start + timedelta(days=i) for i in range((end_exclusive - start).days)]


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, default=str), encoding="utf-8")
    os.replace(tmp, path)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_path(root: Path, day: date) -> Path:
    partition = root / f"date={day.isoformat()}"
    paths = sorted(partition.glob("*.parquet"))
    if len(paths) != 1:
        raise ValueError(f"expected exactly one source partition for {day}, found {len(paths)}")
    return paths[0]


def optional_source_path(root: Path, day: date) -> Path | None:
    partition = root / f"date={day.isoformat()}"
    paths = sorted(partition.glob("*.parquet"))
    return paths[0] if len(paths) == 1 else None


def read_day(path: Path) -> dict[str, np.ndarray]:
    table = pq.read_table(path, columns=COLS)
    frame = table.to_pandas(split_blocks=True, self_destruct=True)
    ts_us = frame.ts.to_numpy(dtype="datetime64[us]").astype(np.int64, copy=False)
    if len(ts_us) == 0 or np.any(np.diff(ts_us) < 0):
        raise ValueError(f"empty or nonchronological source: {path}")
    return {
        "ts_us": ts_us,
        "trade_id": frame.trade_id.to_numpy(np.int64, copy=False),
        "price": frame.price.to_numpy(np.float64, copy=False),
        "quantity": frame.quantity.to_numpy(np.float64, copy=False),
        "quote_quantity": frame.quote_quantity.to_numpy(np.float64, copy=False),
        "is_buyer_maker": frame.is_buyer_maker.to_numpy(bool, copy=False),
    }


def first_row(path: Path) -> dict[str, object]:
    batch = next(pq.ParquetFile(path).iter_batches(batch_size=1, columns=COLS))
    frame = batch.to_pandas()
    row = frame.iloc[0]
    return {key: row[key] for key in COLS}


def write_day(*, symbol: str, day: date, source: Path, next_source: Path | None, output: Path) -> dict[str, object]:
    data = read_day(source)
    ts_us = data["ts_us"]
    start_us = int(datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp() * 1_000_000)
    boundary_us = start_us + np.arange(1440, dtype=np.int64) * 60_000_000
    indices = np.searchsorted(ts_us, boundary_us, side="left")
    unresolved = indices >= len(ts_us)

    selected: dict[str, list[object]] = {name: [] for name in OUT_SCHEMA.names}
    future = first_row(next_source) if unresolved.any() and next_source else None
    for minute, (boundary, index) in enumerate(zip(boundary_us, indices, strict=True)):
        if index < len(ts_us):
            timestamp_us = int(ts_us[index])
            values = {key: data[key][index] for key in data if key != "ts_us"}
            source_row = int(index)
            source_date = day.isoformat()
            source_name = source.name
        elif future is not None:
            timestamp_us = int(pd.Timestamp(future["ts"]).value // 1000)
            values = {key: future[key] for key in COLS if key != "ts"}
            source_row = 0
            source_date = (day + timedelta(days=1)).isoformat()
            source_name = next_source.name if next_source else ""
        else:
            raise ValueError(f"unresolved boundary {day} minute={minute}")
        if timestamp_us < boundary:
            raise AssertionError("selected trade precedes boundary")
        if index > 0 and index <= len(ts_us) and int(ts_us[index - 1]) >= boundary:
            raise AssertionError("selected trade is not first at/after boundary")
        selected["minute_boundary_timestamp"].append(pd.Timestamp(boundary, unit="us", tz="UTC"))
        selected["first_trade_timestamp"].append(pd.Timestamp(timestamp_us, unit="us", tz="UTC"))
        selected["first_trade_id"].append(int(values["trade_id"]))
        selected["price"].append(float(values["price"]))
        selected["quantity"].append(float(values["quantity"]))
        selected["quote_quantity"].append(float(values["quote_quantity"]))
        selected["is_buyer_maker"].append(bool(values["is_buyer_maker"]))
        selected["wait_ms"].append((timestamp_us - boundary) // 1000)
        selected["source_date"].append(source_date)
        selected["source_archive_name"].append(source_name)
        selected["source_checksum"].append("CANONICAL_PARQUET_INGEST_VALIDATED")
        selected["source_row_index"].append(source_row)

    output.parent.mkdir(parents=True, exist_ok=True)
    tmp = output.with_suffix(output.suffix + ".tmp")
    pq.write_table(pa.Table.from_pydict(selected, schema=OUT_SCHEMA), tmp, compression="zstd")
    if pq.ParquetFile(tmp).metadata.num_rows != 1440:
        raise AssertionError("output row count mismatch")
    os.replace(tmp, output)
    waits = np.asarray(selected["wait_ms"], dtype=np.int64)
    return {
        "symbol": symbol,
        "date": day.isoformat(),
        "source_parquet": str(source),
        "source_trade_rows": len(ts_us),
        "minute_index_rows": 1440,
        "unresolved_boundaries": 0,
        "wait_median_ms": float(np.median(waits)),
        "wait_p95_ms": float(np.quantile(waits, 0.95)),
        "output_parquet": str(output),
        "output_sha256": sha256(output),
        "validation_status": "PASSED",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--start", default="2021-07-01")
    parser.add_argument("--end-exclusive", default="2026-07-01")
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()

    planned = days(date.fromisoformat(args.start), date.fromisoformat(args.end_exclusive))
    manifest_path = args.output_root / "canonical_trade_tick_index_manifest.csv"
    existing = pd.read_csv(manifest_path).to_dict("records") if manifest_path.is_file() else []
    completed = {row["date"]: row for row in existing if row.get("validation_status") == "PASSED"}
    records = list(completed.values())
    for position, day in enumerate(planned):
        if day.isoformat() in completed:
            continue
        source = source_path(args.source_root, day)
        next_day = day + timedelta(days=1)
        # The final day's late boundary may legitimately execute on the first
        # trade after midnight, so the look-ahead partition is required too.
        next_source = optional_source_path(args.source_root, next_day)
        output = args.output_root / "tick_execution_index" / f"symbol={args.symbol}" / f"date={day.isoformat()}" / "part.parquet"
        record = write_day(symbol=args.symbol, day=day, source=source, next_source=next_source, output=output)
        records.append(record)
        records.sort(key=lambda row: row["date"])
        pd.DataFrame(records).to_csv(manifest_path.with_suffix(".csv.tmp"), index=False)
        os.replace(manifest_path.with_suffix(".csv.tmp"), manifest_path)
        atomic_json(args.output_root / "tick_index_progress.json", {
            "status": "RUNNING", "planned_days": len(planned), "completed_days": len(records),
            "current_date": day.isoformat(), "source_downloads": 0,
        })

    if len(records) != len(planned) or any(row["validation_status"] != "PASSED" for row in records):
        raise AssertionError("incomplete index")
    atomic_json(args.output_root / "tick_index_progress.json", {
        "status": "PASSED", "planned_days": len(planned), "completed_days": len(records),
        "source_downloads": 0,
    })


if __name__ == "__main__":
    main()
