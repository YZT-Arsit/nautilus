#!/usr/bin/env python3
"""Persist Binance raw trades and derive canonical sub-minute StandardBars.

The source adapter, TradeEvent, aggregation, resampling, and locked Hive layout
are existing ``data_engine`` contracts. This module is only the daily atomic
ingestion orchestrator.
"""

from __future__ import annotations

import argparse
import gc
import json
import math
import os
import time
from datetime import date
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from pathlib import Path
from typing import Any

from data_engine.adapters.binance_vision_raw_trades import download_and_read_raw_trades
from data_engine.transforms import aggregate_ticks_to_bars
from data_engine.transforms import resample_standard_bars


BAR_FREQUENCIES = ("1s", "5s", "15s", "30s")
LOCKED_PREFIX = Path("asset_class=crypto/exchange=BINANCE/venue_type=futures_um/symbol=BTCUSDT")
UTC_EPOCH_NAIVE = datetime(1970, 1, 1)


def datetime_from_ns(value: int) -> datetime:
    """Convert ns to the locked microsecond Parquet timestamp without float time."""
    return UTC_EPOCH_NAIVE + timedelta(microseconds=int(value) // 1_000)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market-root", type=Path, required=True)
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--shard-index", type=int, default=0)
    parser.add_argument("--shard-count", type=int, default=1)
    parser.add_argument("--progress-root", type=Path, required=True)
    parser.add_argument("--cache-root", type=Path, required=True)
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
    # On Windows, a concurrent read can briefly prevent replacement of the
    # destination. Retry only that transient sharing/permission failure; the
    # temporary file remains outside the valid-artifact contract meanwhile.
    for attempt in range(10):
        try:
            os.replace(temporary, path)
            return
        except PermissionError:
            if attempt == 9:
                raise
            time.sleep(0.1 * (attempt + 1))


def trade_partition_path(market_root: Path, day: date) -> Path:
    return (
        market_root / LOCKED_PREFIX / "data_type=trade/freq=tick" / f"date={day}" / "part-0.parquet"
    )


def bar_partition_path(market_root: Path, day: date, frequency: str) -> Path:
    return (
        market_root
        / LOCKED_PREFIX
        / "data_type=bar"
        / f"freq={frequency}"
        / f"date={day}"
        / "part-0.parquet"
    )


def write_standard_bar_partition(
    *,
    market_root: Path,
    day: date,
    frequency: str,
    rows: list[dict[str, Any]],
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
        values["ts"].append(datetime_from_ns(ts_ns))
        values["instrument_id"].append("BTCUSDT")
        for column in ("open", "high", "low", "close", "volume"):
            values[column].append(float(row[column]))
        values["quote_volume"].append(float(row["quote_volume"]))
        values["trade_count"].append(int(row["trade_count"]))
        values["taker_buy_volume"].append(float(row["taker_buy_volume"]))
        values["taker_buy_quote_volume"].append(float(row["taker_buy_quote_volume"]))
        values["source"].append("derived_from_binance_vision_raw_trades")
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
    destination = bar_partition_path(market_root, day, frequency)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".parquet.tmp")
    temporary.unlink(missing_ok=True)
    try:
        pq.write_table(table, temporary, compression="zstd")
        os.replace(temporary, destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return destination


def write_trade_partition(
    *,
    market_root: Path,
    day: date,
    trades: list[Any],
    batch_size: int = 250_000,
) -> Path:
    """Atomically persist one raw source trade per canonical TradeEvent row."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    schema = pa.schema(
        [
            ("ts", pa.timestamp("us")),
            ("instrument_id", pa.string()),
            ("trade_id", pa.int64()),
            ("price", pa.float64()),
            ("quantity", pa.float64()),
            ("quote_quantity", pa.float64()),
            ("quote_quantity_source", pa.string()),
            ("is_buyer_maker", pa.bool_()),
            ("side", pa.string()),
            ("source", pa.string()),
            ("ingested_at", pa.timestamp("us")),
        ]
    )
    destination = trade_partition_path(market_root, day)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".parquet.tmp")
    temporary.unlink(missing_ok=True)
    ingested_at = datetime.now(timezone.utc).replace(tzinfo=None)
    try:
        with pq.ParquetWriter(temporary, schema=schema, compression="zstd") as writer:
            for start in range(0, len(trades), batch_size):
                chunk = trades[start : start + batch_size]
                writer.write_table(
                    pa.Table.from_pydict(
                        {
                            "ts": [datetime_from_ns(t.event_time_ns) for t in chunk],
                            "instrument_id": [str(t.instrument_id) for t in chunk],
                            "trade_id": [int(t.trade_id) for t in chunk],
                            "price": [float(t.price) for t in chunk],
                            "quantity": [float(t.quantity) for t in chunk],
                            "quote_quantity": [float(t.quote_quantity) for t in chunk],
                            "quote_quantity_source": [str(t.quote_quantity_source) for t in chunk],
                            "is_buyer_maker": [bool(t.is_buyer_maker) for t in chunk],
                            "side": [str(t.side) for t in chunk],
                            "source": [str(t.source) for t in chunk],
                            "ingested_at": [ingested_at] * len(chunk),
                        },
                        schema=schema,
                    )
                )
        os.replace(temporary, destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return destination


def summarize_trades(trades: list[Any], day: date) -> dict[str, Any]:
    """Validate normalized ordering/identity/date and return precise totals."""
    day_start_ns = int(
        datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp() * 1e9
    )
    day_end_ns = day_start_ns + 86_400_000_000_000
    prior_key: tuple[int, int] | None = None
    prior_trade_id: int | None = None
    for trade in trades:
        trade_id = int(trade.trade_id)
        key = (int(trade.event_time_ns), trade_id)
        if prior_key is not None and key < prior_key:
            raise ValueError(f"raw trades are not sorted at trade_id={trade_id}")
        if prior_trade_id is not None and trade_id <= prior_trade_id:
            raise ValueError(f"duplicate or non-increasing raw trade_id={trade_id}")
        if not day_start_ns <= key[0] < day_end_ns:
            raise ValueError(f"trade_id={trade_id} timestamp is outside UTC partition {day}")
        if trade.quote_quantity_source != "source_quote_qty":
            raise ValueError(f"trade_id={trade_id} did not preserve source quoteQty")
        prior_key = key
        prior_trade_id = trade_id
    return {
        "row_count": len(trades),
        "first_event_time_ns": int(trades[0].event_time_ns),
        "last_event_time_ns": int(trades[-1].event_time_ns),
        "quantity": math.fsum(float(trade.quantity) for trade in trades),
        "quote_quantity": math.fsum(float(trade.quote_quantity) for trade in trades),
        "taker_buy_quantity": math.fsum(
            float(trade.quantity) for trade in trades if trade.is_buyer_maker is False
        ),
        "taker_buy_quote_quantity": math.fsum(
            float(trade.quote_quantity) for trade in trades if trade.is_buyer_maker is False
        ),
    }


def summarize_trade_partition(path: Path) -> dict[str, Any]:
    """Read the committed Parquet in bounded batches and validate its contents."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    parquet = pq.ParquetFile(path)
    required = (
        "ts",
        "trade_id",
        "quantity",
        "quote_quantity",
        "quote_quantity_source",
        "is_buyer_maker",
    )
    missing = set(required) - set(parquet.schema_arrow.names)
    if missing:
        raise ValueError(f"committed tick partition is missing columns: {sorted(missing)}")
    row_count = 0
    quantity_parts: list[float] = []
    quote_parts: list[float] = []
    buy_quantity_parts: list[float] = []
    buy_quote_parts: list[float] = []
    first_event_time_ns: int | None = None
    last_event_time_ns: int | None = None
    prior_key: tuple[int, int] | None = None
    for batch in parquet.iter_batches(columns=list(required), batch_size=250_000):
        ts_us = batch.column(0).cast(pa.int64()).to_pylist()
        ids = batch.column(1).to_pylist()
        quantities = batch.column(2).to_pylist()
        quotes = batch.column(3).to_pylist()
        quote_sources = batch.column(4).to_pylist()
        makers = batch.column(5).to_pylist()
        for timestamp_us, trade_id in zip(ts_us, ids, strict=True):
            key = (int(timestamp_us) * 1_000, int(trade_id))
            if prior_key is not None and key <= prior_key:
                raise ValueError(f"committed tick order/ID violation at {key}")
            if first_event_time_ns is None:
                first_event_time_ns = key[0]
            last_event_time_ns = key[0]
            prior_key = key
        if any(value != "source_quote_qty" for value in quote_sources):
            raise ValueError("committed ticks contain reconstructed quote quantities")
        quantity_parts.append(math.fsum(float(value) for value in quantities))
        quote_parts.append(math.fsum(float(value) for value in quotes))
        buy_quantity_parts.append(
            math.fsum(
                float(value)
                for value, maker in zip(quantities, makers, strict=True)
                if maker is False
            )
        )
        buy_quote_parts.append(
            math.fsum(
                float(value) for value, maker in zip(quotes, makers, strict=True) if maker is False
            )
        )
        row_count += batch.num_rows
    return {
        "row_count": row_count,
        "first_event_time_ns": first_event_time_ns,
        "last_event_time_ns": last_event_time_ns,
        "quantity": math.fsum(quantity_parts),
        "quote_quantity": math.fsum(quote_parts),
        "taker_buy_quantity": math.fsum(buy_quantity_parts),
        "taker_buy_quote_quantity": math.fsum(buy_quote_parts),
    }


def validate_tick_summaries(source: dict[str, Any], persisted: dict[str, Any]) -> None:
    for field in ("row_count", "first_event_time_ns", "last_event_time_ns"):
        if source[field] != persisted[field]:
            raise ValueError(
                f"tick persistence mismatch for {field}: {source[field]} != {persisted[field]}"
            )
    for field in (
        "quantity",
        "quote_quantity",
        "taker_buy_quantity",
        "taker_buy_quote_quantity",
    ):
        if not math.isclose(
            float(source[field]), float(persisted[field]), rel_tol=1e-15, abs_tol=1e-9
        ):
            raise ValueError(
                f"tick persistence mismatch for {field}: {source[field]} != {persisted[field]}"
            )


def summarize_bar_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "bar_count": len(rows),
        "trade_count": sum(int(row["trade_count"]) for row in rows),
        "volume": math.fsum(float(row["volume"]) for row in rows),
        "quote_volume": math.fsum(float(row["quote_volume"]) for row in rows),
        "taker_buy_volume": math.fsum(float(row["taker_buy_volume"]) for row in rows),
        "taker_buy_quote_volume": math.fsum(float(row["taker_buy_quote_volume"]) for row in rows),
    }


def validate_bar_totals(
    *,
    tick_summary: dict[str, Any],
    bar_summary: dict[str, Any],
    frequency: str,
) -> None:
    if bar_summary["trade_count"] != tick_summary["row_count"]:
        raise ValueError(f"{frequency} trade_count does not match source rows")
    pairs = (
        ("volume", "quantity"),
        ("quote_volume", "quote_quantity"),
        ("taker_buy_volume", "taker_buy_quantity"),
        ("taker_buy_quote_volume", "taker_buy_quote_quantity"),
    )
    for bar_field, tick_field in pairs:
        if not math.isclose(
            float(bar_summary[bar_field]),
            float(tick_summary[tick_field]),
            rel_tol=1e-12,
            abs_tol=1e-9,
        ):
            raise ValueError(
                f"{frequency} {bar_field}={bar_summary[bar_field]} does not match "
                f"tick {tick_field}={tick_summary[tick_field]}"
            )


def process_day(
    market_root: Path,
    cache_root: Path,
    progress_root: Path,
    day: date,
    *,
    overwrite: bool,
) -> dict[str, Any]:
    import pyarrow.parquet as pq

    tick_path = trade_partition_path(market_root, day)
    bar_paths = {
        frequency: bar_partition_path(market_root, day, frequency) for frequency in BAR_FREQUENCIES
    }
    if (
        not overwrite
        and tick_path.is_file()
        and "trade_id" in pq.ParquetFile(tick_path).schema_arrow.names
        and all(path.is_file() for path in bar_paths.values())
    ):
        one_second_rows = pq.ParquetFile(bar_paths["1s"]).metadata.num_rows
        return {
            "date": day.isoformat(),
            "status": "skipped",
            "trades": pq.ParquetFile(tick_path).metadata.num_rows,
            "bars": one_second_rows,
            "path": str(bar_paths["1s"]),
            "bytes": tick_path.stat().st_size
            + sum(path.stat().st_size for path in bar_paths.values()),
        }
    trades, archive, checksum = download_and_read_raw_trades(
        symbol="BTCUSDT",
        day=day,
        cache_root=cache_root,
    )
    if not trades:
        raise ValueError(f"no raw trades for {day}")

    source_summary = summarize_trades(trades, day)
    tick_path = write_trade_partition(market_root=market_root, day=day, trades=trades)
    persisted_summary = summarize_trade_partition(tick_path)
    validate_tick_summaries(source_summary, persisted_summary)

    one_second = aggregate_ticks_to_bars(
        trades,
        frequency="1s",
        default_instrument="BTCUSDT",
        trading_date=day.isoformat(),
    )
    if one_second.issues:
        raise ValueError(f"1s bar validation failed for {day}: {one_second.issues[:5]}")
    if one_second.quote_quantity_fallback_count:
        raise ValueError(
            f"{one_second.quote_quantity_fallback_count} raw trades used quote fallback"
        )

    results = {"1s": one_second}
    for frequency in BAR_FREQUENCIES[1:]:
        results[frequency] = resample_standard_bars(
            one_second.rows,
            frequency=frequency,
            default_instrument="BTCUSDT",
            trading_date=day.isoformat(),
        )
        if results[frequency].issues:
            raise ValueError(
                f"{frequency} bar validation failed for {day}: {results[frequency].issues[:5]}"
            )

    validation: dict[str, Any] = {
        "date": day.isoformat(),
        "source_archive": archive.name,
        "source_checksum_sha256": checksum,
        "source_rows": len(trades),
        "normalized_ticks": source_summary,
        "persisted_ticks": persisted_summary,
        "bars": {},
    }
    written_paths: dict[str, Path] = {}
    for frequency, result in results.items():
        bar_summary = summarize_bar_rows(result.rows)
        validate_bar_totals(
            tick_summary=source_summary,
            bar_summary=bar_summary,
            frequency=frequency,
        )
        written_paths[frequency] = write_standard_bar_partition(
            market_root=market_root,
            day=day,
            frequency=frequency,
            rows=result.rows,
        )
        validation["bars"][frequency] = bar_summary

    # The checksum and validation summary cross the durability boundary before
    # the official ZIP is eligible for deletion.
    atomic_json(progress_root / "validation" / f"date={day}.json", validation)
    total_bytes = tick_path.stat().st_size + sum(
        path.stat().st_size for path in written_paths.values()
    )
    output = {
        "date": day.isoformat(),
        "status": "complete",
        "trades": len(trades),
        "bars": len(one_second.rows),
        "bars_by_frequency": {frequency: len(result.rows) for frequency, result in results.items()},
        "tick_path": str(tick_path),
        "path": str(written_paths["1s"]),
        "bytes": total_bytes,
        "source_checksum": checksum,
    }
    del trades, one_second, results
    gc.collect()
    archive.unlink(missing_ok=True)
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
            value = process_day(
                args.market_root,
                args.cache_root / f"shard_{args.shard_index}",
                args.progress_root,
                day,
                overwrite=args.overwrite,
            )
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
