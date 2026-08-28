"""Import an official Binance funding-rate REST response through canonical normalization."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pyarrow as pa
import pyarrow.dataset as ds

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from feature_engine.data_sources.binance_vision import normalize_binance_funding


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    payload = json.loads(args.input.read_text(encoding="utf-8"))
    rows = [
        {
            "calc_time": int(row["fundingTime"]),
            "funding_interval_hours": 8,
            "funding_rate": float(row["fundingRate"]),
        }
        for row in payload
    ]
    frame = normalize_binance_funding(rows, symbol=args.symbol).with_columns(
        __import__("polars").col("ts").dt.strftime("%Y-%m-%d").alias("date"),
        __import__("polars").lit("crypto").alias("asset_class"),
        __import__("polars").lit("funding_rate").alias("data_type"),
        __import__("polars").lit("settlement").alias("freq"),
    )
    table = pa.table({column: frame[column].to_list() for column in frame.columns})
    args.output.mkdir(parents=True, exist_ok=True)
    ds.write_dataset(
        table,
        base_dir=str(args.output),
        format="parquet",
        partitioning=[
            "asset_class", "exchange", "venue_type", "symbol",
            "data_type", "freq", "date",
        ],
        partitioning_flavor="hive",
        existing_data_behavior="overwrite_or_ignore",
        basename_template="part-{i}.parquet",
        max_partitions=4096,
    )
    print(f"imported {len(rows)} official funding rows for {args.symbol}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
