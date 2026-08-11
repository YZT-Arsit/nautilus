#!/usr/bin/env python3
"""Build canonical 1-second StandardBar partitions from stored Binance aggTrades.

The market reader and OHLCV aggregation are the existing ``data_engine``
contracts.  This module only orchestrates daily partitions and maps the result
back to the locked StandardBar physical schema.
"""

from __future__ import annotations

import argparse
import gc
import json
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from data_engine.loader import load_events
from data_engine.transforms import aggregate_ticks_to_bars


ONE_SECOND_NS = 1_000_000_000
PARTITION_PREFIX = Path(
    "asset_class=crypto/exchange=BINANCE/venue_type=futures_um/"
    "symbol=BTCUSDT/data_type=bar/freq=1s"
)
FILTERS = {
    "asset_class": "crypto",
    "exchange": "BINANCE",
    "venue_type": "futures_um",
    "symbol": "BTCUSDT",
    "data_type": "trade",
    "freq": "tick",
}


@dataclass
class ExtraBucket:
    trade_count: int = 0
    taker_buy_volume: float = 0.0
    taker_buy_quote_volume: float = 0.0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market-root", type=Path, required=True)
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--shard-index", type=int, default=0)
    parser.add_argument("--shard-count", type=int, default=1)
    parser.add_argument("--progress-root", type=Path, required=True)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def dates_inclusive(start: date, end: date) -> list[date]:
    if end < start:
        raise ValueError("end precedes start")
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def load_day(market_root: Path, day: date) -> list[Any]:
    _warmup, live = load_events(
        {
            "mode": "hive_parquet_trades",
            "root": str(market_root),
            "instrument_id": "BTCUSDT",
            "filters": FILTERS,
            "timestamp_column": "ts",
            "timestamp_unit": "ns",
            "start": day.isoformat(),
            "end": day.isoformat(),
            "warmup": 0,
        }
    )
    return list(live)


def build_extras(trades: list[Any]) -> dict[int, ExtraBucket]:
    extras: dict[int, ExtraBucket] = {}
    for trade in trades:
        bucket_ns = trade.event_time_ns // ONE_SECOND_NS * ONE_SECOND_NS
        value = extras.get(bucket_ns)
        if value is None:
            value = ExtraBucket()
            extras[bucket_ns] = value
        value.trade_count += 1
        if trade.side == "BUY":
            value.taker_buy_volume += float(trade.quantity)
            value.taker_buy_quote_volume += float(
                trade.quote_quantity
                if trade.quote_quantity is not None
                else trade.price * trade.quantity
            )
    return extras


def write_standard_bar_partition(
    *,
    market_root: Path,
    day: date,
    rows: list[dict[str, Any]],
    extras: dict[int, ExtraBucket],
) -> Path:
    import pyarrow as pa
    import pyarrow.parquet as pq

    ingested_at = datetime.now(timezone.utc).replace(tzinfo=None)
    values: dict[str, list[Any]] = {
        "ts": [],
        "instrument_id": [],
        "open": [],
        "high": [],
        "low": [],
        "close": [],
        "volume": [],
        "quote_volume": [],
        "trade_count": [],
        "taker_buy_volume": [],
        "taker_buy_quote_volume": [],
        "source": [],
        "ingested_at": [],
    }
    for row in rows:
        ts_ns = int(row["ts_event"])
        extra = extras[ts_ns]
        values["ts"].append(datetime.fromtimestamp(ts_ns / 1e9, tz=timezone.utc).replace(tzinfo=None))
        values["instrument_id"].append("BTCUSDT")
        for column in ("open", "high", "low", "close", "volume"):
            values[column].append(float(row[column]))
        values["quote_volume"].append(float(row["turnover"]))
        values["trade_count"].append(extra.trade_count)
        values["taker_buy_volume"].append(extra.taker_buy_volume)
        values["taker_buy_quote_volume"].append(extra.taker_buy_quote_volume)
        values["source"].append("derived_from_binance_vision_aggTrades")
        values["ingested_at"].append(ingested_at)

    schema = pa.schema(
        [
            ("ts", pa.timestamp("us")),
            ("instrument_id", pa.string()),
            ("open", pa.float64()),
            ("high", pa.float64()),
            ("low", pa.float64()),
            ("close", pa.float64()),
            ("volume", pa.float64()),
            ("quote_volume", pa.float64()),
            ("trade_count", pa.int64()),
            ("taker_buy_volume", pa.float64()),
            ("taker_buy_quote_volume", pa.float64()),
            ("source", pa.string()),
            ("ingested_at", pa.timestamp("us")),
        ]
    )
    table = pa.Table.from_pydict(values, schema=schema)
    destination = market_root / PARTITION_PREFIX / f"date={day.isoformat()}" / "part-0.parquet"
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".parquet.tmp")
    pq.write_table(table, temporary, compression="zstd")
    os.replace(temporary, destination)
    return destination


def process_day(market_root: Path, day: date, *, overwrite: bool) -> dict[str, Any]:
    import pyarrow.parquet as pq

    destination = market_root / PARTITION_PREFIX / f"date={day.isoformat()}" / "part-0.parquet"
    if destination.is_file() and not overwrite:
        return {
            "date": day.isoformat(),
            "status": "skipped",
            "bars": pq.ParquetFile(destination).metadata.num_rows,
            "path": str(destination),
        }
    trades = load_day(market_root, day)
    if not trades:
        raise ValueError(f"no aggTrades for {day}")
    result = aggregate_ticks_to_bars(
        trades,
        frequency="1s",
        default_instrument="BTCUSDT",
        trading_date=day.isoformat(),
    )
    if result.issues:
        raise ValueError(f"bar validation failed for {day}: {result.issues[:5]}")
    extras = build_extras(trades)
    if len(extras) != len(result.rows):
        raise ValueError(f"bucket mismatch for {day}: {len(extras)} != {len(result.rows)}")
    path = write_standard_bar_partition(
        market_root=market_root,
        day=day,
        rows=result.rows,
        extras=extras,
    )
    output = {
        "date": day.isoformat(),
        "status": "complete",
        "trades": len(trades),
        "bars": len(result.rows),
        "path": str(path),
        "bytes": path.stat().st_size,
    }
    del trades, result, extras
    gc.collect()
    return output


def main() -> int:
    args = parse_args()
    if args.shard_count < 1 or not 0 <= args.shard_index < args.shard_count:
        raise ValueError("invalid shard index/count")
    selected = dates_inclusive(args.start, args.end)[args.shard_index :: args.shard_count]
    progress = args.progress_root / f"progress_shard_{args.shard_index}.json"
    atomic_json(
        progress,
        {
            "status": "running",
            "shard_index": args.shard_index,
            "shard_count": args.shard_count,
            "date_count": len(selected),
            "completed": 0,
        },
    )
    total_bars = 0
    total_bytes = 0
    try:
        for index, day in enumerate(selected, 1):
            value = process_day(args.market_root, day, overwrite=args.overwrite)
            total_bars += int(value["bars"])
            total_bytes += int(value.get("bytes", 0))
            atomic_json(
                progress,
                {
                    "status": "running",
                    "shard_index": args.shard_index,
                    "shard_count": args.shard_count,
                    "date": day.isoformat(),
                    "date_count": len(selected),
                    "completed": index,
                    "total_bars": total_bars,
                    "written_bytes": total_bytes,
                },
            )
            print(
                f"COMPLETE date={day} status={value['status']} "
                f"trades={value.get('trades', 0)} bars={value['bars']} "
                f"bytes={value.get('bytes', 0)}",
                flush=True,
            )
    except Exception as exc:
        atomic_json(
            progress,
            {
                "status": "failed",
                "shard_index": args.shard_index,
                "shard_count": args.shard_count,
                "error": f"{type(exc).__name__}: {exc}",
            },
        )
        raise
    atomic_json(
        progress,
        {
            "status": "complete",
            "shard_index": args.shard_index,
            "shard_count": args.shard_count,
            "date_count": len(selected),
            "completed": len(selected),
            "total_bars": total_bars,
            "written_bytes": total_bytes,
        },
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
