#!/usr/bin/env python3
"""Stream, validate, and convert Binance USD-M L1/trades for the frozen pilot."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import sys
import time
import urllib.error
import urllib.request
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
DEFAULT_OUTPUT = Path("outputs/baseline_evaluation/maker_execution_research/l1_pilot")
SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
PRICE_PRECISION = {"BTCUSDT": 1, "ETHUSDT": 2, "SOLUSDT": 3}
SIZE_PRECISION = {"BTCUSDT": 3, "ETHUSDT": 3, "SOLUSDT": 1}
BOOK_COLUMNS = [
    "update_id",
    "best_bid_price",
    "best_bid_qty",
    "best_ask_price",
    "best_ask_qty",
    "transaction_time",
    "event_time",
]
TRADE_COLUMNS = ["trade_id", "price", "qty", "quote_qty", "time", "is_buyer_maker"]


def atomic_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    frame.to_csv(temporary, index=False)
    os.replace(temporary, path)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download(url: str, destination: Path, attempts: int = 5) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".part")
    if temporary.exists():
        temporary.unlink()
    for attempt in range(attempts):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "nautilus-l1-pilot/1"})
            with urllib.request.urlopen(request, timeout=120) as response, temporary.open("wb") as out:
                while block := response.read(4 * 1024 * 1024):
                    out.write(block)
            os.replace(temporary, destination)
            return
        except (OSError, urllib.error.URLError):
            if temporary.exists():
                temporary.unlink()
            if attempt + 1 == attempts:
                raise
            time.sleep(2**attempt)


def archive_urls(symbol: str, day: str, data_type: str) -> tuple[str, str, str]:
    archive = f"{symbol}-{data_type}-{day}.zip"
    base = f"https://data.binance.vision/data/futures/um/daily/{data_type}/{symbol}/{archive}"
    return archive, base, base + ".CHECKSUM"


def expected_checksum(path: Path) -> str:
    return path.read_text(encoding="utf-8").strip().split()[0].lower()


def parquet_writer(path: Path, schema: pa.Schema) -> pq.ParquetWriter:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    if temporary.exists():
        temporary.unlink()
    return pq.ParquetWriter(temporary, schema, compression="zstd", compression_level=6)


def parse_bool(series: pd.Series) -> np.ndarray:
    return series.astype(str).str.lower().map({"true": True, "false": False}).to_numpy(bool)


def convert_book(archive: Path, output: Path) -> dict:
    schema = pa.schema(
        [
            ("update_id", pa.int64()),
            ("bid_price", pa.float64()),
            ("bid_size", pa.float64()),
            ("ask_price", pa.float64()),
            ("ask_size", pa.float64()),
            ("ts_event_ns", pa.int64()),
            ("ts_init_ns", pa.int64()),
        ]
    )
    writer = parquet_writer(output, schema)
    temporary = output.with_suffix(output.suffix + ".tmp")
    rows = 0
    first = None
    last = None
    previous_ts = -1
    invalid_order = crossed = bad_qty = bad_ts = 0
    with zipfile.ZipFile(archive) as zipped:
        bad_member = zipped.testzip()
        if bad_member is not None:
            raise ValueError(f"corrupt ZIP member: {bad_member}")
        members = zipped.infolist()
        if len(members) != 1:
            raise ValueError("daily bookTicker ZIP must contain exactly one CSV")
        uncompressed = members[0].file_size
        with zipped.open(members[0]) as handle:
            for chunk in pd.read_csv(handle, chunksize=500_000):
                if list(chunk.columns) != BOOK_COLUMNS:
                    raise ValueError(f"unexpected bookTicker columns: {list(chunk.columns)}")
                transaction = chunk.transaction_time.to_numpy(np.int64, copy=False)
                event = chunk.event_time.to_numpy(np.int64, copy=False)
                bid = chunk.best_bid_price.to_numpy(float, copy=False)
                ask = chunk.best_ask_price.to_numpy(float, copy=False)
                bid_size = chunk.best_bid_qty.to_numpy(float, copy=False)
                ask_size = chunk.best_ask_qty.to_numpy(float, copy=False)
                order_id = chunk.update_id.to_numpy(np.int64, copy=False)
                invalid_order += int(np.count_nonzero(np.diff(transaction) < 0))
                if previous_ts >= 0 and transaction[0] < previous_ts:
                    invalid_order += 1
                previous_ts = int(transaction[-1])
                crossed += int(np.count_nonzero(bid > ask))
                bad_qty += int(np.count_nonzero((bid_size <= 0) | (ask_size <= 0)))
                bad_ts += int(np.count_nonzero((transaction <= 0) | (event <= 0)))
                first = int(transaction[0]) if first is None else first
                last = int(transaction[-1])
                writer.write_table(
                    pa.table(
                        {
                            "update_id": order_id,
                            "bid_price": bid,
                            "bid_size": bid_size,
                            "ask_price": ask,
                            "ask_size": ask_size,
                            "ts_event_ns": transaction * 1_000_000,
                            "ts_init_ns": event * 1_000_000,
                        },
                        schema=schema,
                    )
                )
                rows += len(chunk)
    writer.close()
    if invalid_order or crossed or bad_qty or bad_ts:
        temporary.unlink(missing_ok=True)
        raise ValueError(
            f"book validation failed order={invalid_order} crossed={crossed} "
            f"bad_qty={bad_qty} bad_ts={bad_ts}"
        )
    os.replace(temporary, output)
    return {
        "rows": rows,
        "first_timestamp": first,
        "last_timestamp": last,
        "uncompressed_bytes": uncompressed,
        "chronology_failures": invalid_order,
        "crossed_bbo_count": crossed,
        "nonpositive_quantity_count": bad_qty,
        "malformed_timestamp_count": bad_ts,
    }


def convert_trades(archive: Path, output: Path) -> dict:
    schema = pa.schema(
        [
            ("trade_id", pa.int64()),
            ("price", pa.float64()),
            ("quantity", pa.float64()),
            ("quote_quantity", pa.float64()),
            ("ts_event_ns", pa.int64()),
            ("is_buyer_maker", pa.bool_()),
        ]
    )
    writer = parquet_writer(output, schema)
    temporary = output.with_suffix(output.suffix + ".tmp")
    rows = 0
    first = None
    last = None
    previous_ts = -1
    invalid_order = bad_qty = bad_ts = 0
    with zipfile.ZipFile(archive) as zipped:
        bad_member = zipped.testzip()
        if bad_member is not None:
            raise ValueError(f"corrupt ZIP member: {bad_member}")
        members = zipped.infolist()
        if len(members) != 1:
            raise ValueError("daily trades ZIP must contain exactly one CSV")
        uncompressed = members[0].file_size
        with zipped.open(members[0]) as handle:
            first_line = handle.readline().decode("utf-8").strip().split(",")
            handle.seek(0)
            header = 0 if first_line[0].lower() in {"id", "trade_id"} else None
            names = None if header == 0 else TRADE_COLUMNS
            for chunk in pd.read_csv(handle, header=header, names=names, chunksize=500_000):
                if len(chunk.columns) != 6:
                    raise ValueError(f"unexpected trade column count: {len(chunk.columns)}")
                chunk.columns = TRADE_COLUMNS
                ts = chunk.time.to_numpy(np.int64, copy=False)
                qty = chunk.qty.to_numpy(float, copy=False)
                price = chunk.price.to_numpy(float, copy=False)
                invalid_order += int(np.count_nonzero(np.diff(ts) < 0))
                if previous_ts >= 0 and ts[0] < previous_ts:
                    invalid_order += 1
                previous_ts = int(ts[-1])
                bad_qty += int(np.count_nonzero((qty <= 0) | (price <= 0)))
                bad_ts += int(np.count_nonzero(ts <= 0))
                first = int(ts[0]) if first is None else first
                last = int(ts[-1])
                writer.write_table(
                    pa.table(
                        {
                            "trade_id": chunk.trade_id.to_numpy(np.int64, copy=False),
                            "price": price,
                            "quantity": qty,
                            "quote_quantity": chunk.quote_qty.to_numpy(float, copy=False),
                            "ts_event_ns": ts * 1_000_000,
                            "is_buyer_maker": parse_bool(chunk.is_buyer_maker),
                        },
                        schema=schema,
                    )
                )
                rows += len(chunk)
    writer.close()
    if invalid_order or bad_qty or bad_ts:
        temporary.unlink(missing_ok=True)
        raise ValueError(
            f"trade validation failed order={invalid_order} bad_qty={bad_qty} bad_ts={bad_ts}"
        )
    os.replace(temporary, output)
    return {
        "rows": rows,
        "first_timestamp": first,
        "last_timestamp": last,
        "uncompressed_bytes": uncompressed,
        "chronology_failures": invalid_order,
        "nonpositive_quantity_count": bad_qty,
        "malformed_timestamp_count": bad_ts,
    }


def smoke_quote_tick(output: Path, symbol: str) -> dict:
    from nautilus_trader.model.data import QuoteTick
    from nautilus_trader.model.identifiers import InstrumentId
    from nautilus_trader.model.objects import Price, Quantity

    frame = pd.read_parquet(output)
    price_precision = PRICE_PRECISION[symbol]
    size_precision = SIZE_PRECISION[symbol]
    quote = QuoteTick(
        instrument_id=InstrumentId.from_str(f"{symbol}-PERP.BINANCE"),
        bid_price=Price.from_str(f"{frame.bid_price.iat[0]:.{price_precision}f}"),
        ask_price=Price.from_str(f"{frame.ask_price.iat[0]:.{price_precision}f}"),
        bid_size=Quantity.from_str(f"{frame.bid_size.iat[0]:.{size_precision}f}"),
        ask_size=Quantity.from_str(f"{frame.ask_size.iat[0]:.{size_precision}f}"),
        ts_event=int(frame.ts_event_ns.iat[0]),
        ts_init=int(frame.ts_init_ns.iat[0]),
    )
    return {
        "quote_tick_class": f"{type(quote).__module__}.{type(quote).__name__}",
        "instrument_id": str(quote.instrument_id),
        "bid_price": str(quote.bid_price),
        "ask_price": str(quote.ask_price),
        "bid_size": str(quote.bid_size),
        "ask_size": str(quote.ask_size),
        "ts_event": int(quote.ts_event),
        "ts_init": int(quote.ts_init),
    }


def dates(start: str, end_exclusive: str) -> list[str]:
    current = date.fromisoformat(start)
    end = date.fromisoformat(end_exclusive)
    result = []
    while current < end:
        result.append(current.isoformat())
        current += timedelta(days=1)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", choices=SYMBOLS, required=True)
    parser.add_argument("--start", default="2024-03-01")
    parser.add_argument("--end-exclusive", default="2024-03-31")
    parser.add_argument("--output", type=Path, default=ROOT / DEFAULT_OUTPUT)
    parser.add_argument("--temp", type=Path, default=Path(r"D:\nautilus\outputs\tmp_l1_pilot"))
    parser.add_argument("--book-only", action="store_true")
    args = parser.parse_args()
    output = args.output.resolve()
    manifest_path = output / f"ingest_manifest_{args.symbol}.csv"
    existing = pd.read_csv(manifest_path).to_dict("records") if manifest_path.exists() else []
    by_key = {(row["date"], row["data_type"]): row for row in existing}

    for day in dates(args.start, args.end_exclusive):
        for data_type in (["bookTicker"] if args.book_only else ["bookTicker", "trades"]):
            key = (day, data_type)
            destination = (
                output
                / ("l1_quotes" if data_type == "bookTicker" else "raw_trades")
                / f"symbol={args.symbol}"
                / f"date={day}"
                / "part.parquet"
            )
            prior = by_key.get(key)
            if prior and prior.get("status") == "PASSED" and destination.exists():
                continue
            archive_name, archive_url, checksum_url = archive_urls(args.symbol, day, data_type)
            archive = args.temp / archive_name
            checksum = args.temp / f"{archive_name}.CHECKSUM"
            row = {
                "symbol": args.symbol,
                "date": day,
                "data_type": data_type,
                "filename": archive_name,
                "archive_exists": False,
                "checksum_exists": False,
                "checksum_valid": False,
                "rows": 0,
                "first_timestamp": "",
                "last_timestamp": "",
                "compressed_bytes": 0,
                "uncompressed_bytes": 0,
                "converted_bytes": 0,
                "converted_path": str(destination),
                "converted_sha256": "",
                "status": "RUNNING",
            }
            try:
                download(archive_url, archive)
                row["archive_exists"] = True
                download(checksum_url, checksum)
                row["checksum_exists"] = True
                actual = sha256(archive)
                row["checksum_valid"] = actual == expected_checksum(checksum)
                if not row["checksum_valid"]:
                    raise ValueError("source checksum mismatch")
                row["compressed_bytes"] = archive.stat().st_size
                metrics = (
                    convert_book(archive, destination)
                    if data_type == "bookTicker"
                    else convert_trades(archive, destination)
                )
                row.update(metrics)
                row["converted_bytes"] = destination.stat().st_size
                row["converted_sha256"] = sha256(destination)
                row["status"] = "PASSED"
                by_key[key] = row
                atomic_csv(pd.DataFrame(by_key.values()).sort_values(["date", "data_type"]), manifest_path)
                archive.unlink(missing_ok=True)
                checksum.unlink(missing_ok=True)
            except Exception as exc:
                row["status"] = f"FAILED:{type(exc).__name__}:{exc}"
                by_key[key] = row
                atomic_csv(pd.DataFrame(by_key.values()).sort_values(["date", "data_type"]), manifest_path)
                raise

    if args.symbol == "BTCUSDT" and args.start <= "2024-03-01" < args.end_exclusive:
        smoke_path = output / "l1_quotes/symbol=BTCUSDT/date=2024-03-01/part.parquet"
        smoke = smoke_quote_tick(smoke_path, "BTCUSDT")
        (output / "quote_tick_smoke_test.json").write_text(
            json.dumps(smoke, indent=2) + "\n", encoding="utf-8"
        )


if __name__ == "__main__":
    main()
