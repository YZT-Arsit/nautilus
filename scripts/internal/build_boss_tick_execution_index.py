#!/usr/bin/env python3
"""Build an exact, compact minute-boundary execution index from Binance trades.

The durable result contains the first official raw trade at or after each UTC
minute boundary.  Official daily ZIPs are checksum-verified and processed one
partition at a time; no extracted CSV or full-history tick store is created.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import shutil
import sys
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data_engine.adapters.binance_vision_raw_trades import (  # noqa: E402
    download_verified_archive,
    iter_raw_trade_archive,
    raw_trade_archive_url,
)
from data_engine.events import TradeEvent  # noqa: E402


SYMBOLS = (
    "XRPUSDT",
    "DOGEUSDT",
    "SUIUSDT",
    "BNBUSDT",
    "ETHUSDT",
    "BTCUSDT",
    "1000PEPEUSDT",
    "SOLUSDT",
    "ADAUSDT",
)
DEFAULT_START = date(2024, 7, 1)
DEFAULT_END_EXCLUSIVE = date(2026, 6, 30)
MINUTE_MS = 60_000
DAY_MS = 86_400_000


@dataclass(frozen=True)
class IndexedTrade:
    event_time_ms: int
    trade_id: int
    price: float
    quantity: float
    quote_quantity: float
    is_buyer_maker: bool
    source_row_index: int
    source_date: str
    source_archive_name: str
    source_checksum: str


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def atomic_csv(path: Path, rows: Iterable[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    os.replace(temporary, path)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def dates(start: date, end_exclusive: date) -> list[date]:
    return [start + timedelta(days=i) for i in range((end_exclusive - start).days)]


def utc_day_start_ms(day: date) -> int:
    return int(datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp() * 1000)


def _head_exists(url: str, timeout: int = 30) -> tuple[bool, int | None, str]:
    try:
        with urlopen(Request(url, method="HEAD"), timeout=timeout) as response:
            size = response.headers.get("Content-Length")
            return response.status == 200, int(size) if size else None, ""
    except HTTPError as exc:
        return False, None, f"HTTP_{exc.code}"
    except Exception as exc:  # network diagnostics are preserved in the audit
        return False, None, f"{type(exc).__name__}:{exc}"


def audit_official_availability(
    *,
    output_root: Path,
    start: date,
    end_exclusive: date,
    workers: int,
) -> dict[str, Any]:
    """Probe every official daily checksum before any production bulk download."""
    work = [(symbol, day) for symbol in SYMBOLS for day in dates(start, end_exclusive)]
    rows: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_head_exists, raw_trade_archive_url(symbol, day) + ".CHECKSUM"):
            (symbol, day)
            for symbol, day in work
        }
        for future in as_completed(futures):
            symbol, day = futures[future]
            exists, size, error = future.result()
            rows.append(
                {
                    "symbol": symbol,
                    "date": day.isoformat(),
                    "official_raw_trades_available": exists,
                    "checksum_content_length": size if size is not None else "",
                    "error": error,
                }
            )
    rows.sort(key=lambda row: (row["symbol"], row["date"]))
    atomic_csv(
        output_root / "official_raw_trade_daily_availability.csv",
        rows,
        list(rows[0]),
    )
    by_date = {
        day: all(
            next(
                row["official_raw_trades_available"]
                for row in rows
                if row["symbol"] == symbol and row["date"] == day
            )
            for symbol in SYMBOLS
        )
        for day in sorted({row["date"] for row in rows})
    }
    complete_days = [day for day, complete in by_date.items() if complete]
    common_start: str | None = None
    common_end: str | None = None
    requested = [day.isoformat() for day in dates(start, end_exclusive)]
    # Freeze the longest complete suffix ending at the requested end.  This
    # avoids accepting an interior gap as a valid common interval.
    if requested and by_date.get(requested[-1], False):
        first = len(requested) - 1
        while first > 0 and by_date.get(requested[first - 1], False):
            first -= 1
        common_start = requested[first]
        common_end = end_exclusive.isoformat()
    summary = {
        "status": "PASSED" if common_start else "BLOCKED",
        "symbols": list(SYMBOLS),
        "requested_start": start.isoformat(),
        "requested_end_exclusive": end_exclusive.isoformat(),
        "common_start": common_start,
        "common_end_exclusive": common_end,
        "requested_day_count": len(requested),
        "complete_common_day_count": len(complete_days),
        "missing_symbol_days": sum(not bool(row["official_raw_trades_available"]) for row in rows),
        "frozen_before_strategy_execution": True,
    }
    atomic_json(output_root / "boss_tick_index_data_window.json", summary)
    return summary


def to_indexed(
    trade: TradeEvent,
    *,
    row_index: int,
    source_day: date,
    archive_name: str,
    checksum: str,
) -> IndexedTrade:
    if trade.quote_quantity is None or trade.quote_quantity_source != "source_quote_qty":
        raise ValueError("official source quoteQty was not preserved")
    return IndexedTrade(
        event_time_ms=int(trade.event_time_ns) // 1_000_000,
        trade_id=int(trade.trade_id),
        price=float(trade.price),
        quantity=float(trade.quantity),
        quote_quantity=float(trade.quote_quantity),
        is_buyer_maker=bool(trade.is_buyer_maker),
        source_row_index=row_index,
        source_date=source_day.isoformat(),
        source_archive_name=archive_name,
        source_checksum=checksum,
    )


def build_minute_index_rows(
    trades: Iterable[IndexedTrade],
    *,
    day: date,
    future_trade: IndexedTrade | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Return the exact first trade at/after each UTC minute boundary."""
    day_start = utc_day_start_ms(day)
    boundaries = [day_start + i * MINUTE_MS for i in range(1440)]
    rows: list[dict[str, Any]] = []
    next_boundary = 0
    previous: IndexedTrade | None = None
    first_trade: IndexedTrade | None = None
    last_trade: IndexedTrade | None = None
    raw_count = 0
    minute_counts = [0] * 1440
    validation_candidates: list[dict[str, Any]] = []

    def assign(trade: IndexedTrade, prior: IndexedTrade | None) -> None:
        nonlocal next_boundary
        while next_boundary < 1440 and boundaries[next_boundary] <= trade.event_time_ms:
            boundary = boundaries[next_boundary]
            if trade.event_time_ms < boundary:
                raise AssertionError("selected trade precedes decision boundary")
            if prior is not None and prior.event_time_ms >= boundary:
                raise AssertionError("selected trade is not the first chronological trade")
            row = {
                "minute_boundary_timestamp": boundary,
                "first_trade_timestamp": trade.event_time_ms,
                "first_trade_id": trade.trade_id,
                "price": trade.price,
                "quantity": trade.quantity,
                "quote_quantity": trade.quote_quantity,
                "is_buyer_maker": trade.is_buyer_maker,
                "wait_ms": trade.event_time_ms - boundary,
                "source_date": trade.source_date,
                "source_archive_name": trade.source_archive_name,
                "source_checksum": trade.source_checksum,
                "source_row_index": trade.source_row_index,
            }
            rows.append(row)
            if (
                next_boundary in {0, 1, 719, 1438, 1439}
                or trade.event_time_ms == boundary
                or int(hashlib.sha256(f"{day}:{next_boundary}".encode()).hexdigest()[:8], 16)
                % 31
                == 0
            ):
                validation_candidates.append(
                    {
                        **row,
                        "previous_trade_timestamp": prior.event_time_ms if prior else "",
                        "previous_trade_id": prior.trade_id if prior else "",
                        "proof_selected_not_before_boundary": trade.event_time_ms >= boundary,
                        "proof_predecessor_before_boundary":
                        prior is None or prior.event_time_ms < boundary,
                    }
                )
            next_boundary += 1

    for trade in trades:
        raw_count += 1
        if first_trade is None:
            first_trade = trade
        last_trade = trade
        offset = trade.event_time_ms - day_start
        if 0 <= offset < DAY_MS:
            minute_counts[offset // MINUTE_MS] += 1
        assign(trade, previous)
        previous = trade
    if next_boundary < 1440 and future_trade is not None:
        assign(future_trade, previous)
    if rows:
        for minute_index in (max(range(1440), key=minute_counts.__getitem__),):
            if minute_index < len(rows):
                validation_candidates.append({**rows[minute_index], "sample_category": "HIGH_VOLUME"})
        nonzero = [i for i, count in enumerate(minute_counts) if count]
        if nonzero:
            minute_index = min(nonzero, key=minute_counts.__getitem__)
            if minute_index < len(rows):
                validation_candidates.append({**rows[minute_index], "sample_category": "LOW_VOLUME"})
    return rows, {
        "raw_trade_count": raw_count,
        "first_trade_timestamp": first_trade.event_time_ms if first_trade else None,
        "last_trade_timestamp": last_trade.event_time_ms if last_trade else None,
        "resolved_boundaries": len(rows),
        "unresolved_boundaries": 1440 - len(rows),
        "validation_candidates": validation_candidates,
    }


def build_minute_index_rows_from_source(
    trades: Iterable[IndexedTrade],
    *,
    day: date,
    future_trade: IndexedTrade | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Build an exact minute index without assuming source-row ordering.

    Only the chronological first and last trade for each of the 1,440 UTC
    minutes are retained.  A reverse suffix pass then selects the exact first
    trade at or after every minute boundary.  This is equivalent to sorting the
    full daily archive by ``(timestamp, trade_id)`` while keeping memory bounded
    independently of the raw trade count.
    """
    day_start = utc_day_start_ms(day)
    first_by_minute: list[IndexedTrade | None] = [None] * 1440
    last_by_minute: list[IndexedTrade | None] = [None] * 1440
    minute_counts = [0] * 1440
    raw_count = 0
    first_trade: IndexedTrade | None = None
    last_trade: IndexedTrade | None = None

    def key(trade: IndexedTrade) -> tuple[int, int]:
        return trade.event_time_ms, trade.trade_id

    for trade in trades:
        raw_count += 1
        offset = trade.event_time_ms - day_start
        if not 0 <= offset < DAY_MS:
            raise ValueError(
                f"raw trade timestamp outside UTC source partition {day}: "
                f"{trade.event_time_ms}"
            )
        minute = offset // MINUTE_MS
        minute_counts[minute] += 1
        if first_trade is None or key(trade) < key(first_trade):
            first_trade = trade
        if last_trade is None or key(trade) > key(last_trade):
            last_trade = trade
        current_first = first_by_minute[minute]
        if current_first is None or key(trade) < key(current_first):
            first_by_minute[minute] = trade
        current_last = last_by_minute[minute]
        if current_last is None or key(trade) > key(current_last):
            last_by_minute[minute] = trade

    selected: list[IndexedTrade | None] = [None] * 1440
    next_trade = future_trade
    for minute in range(1439, -1, -1):
        if first_by_minute[minute] is not None:
            next_trade = first_by_minute[minute]
        selected[minute] = next_trade

    predecessor: list[IndexedTrade | None] = [None] * 1440
    prior: IndexedTrade | None = None
    for minute in range(1440):
        predecessor[minute] = prior
        candidate = last_by_minute[minute]
        if candidate is not None and (prior is None or key(candidate) > key(prior)):
            prior = candidate

    rows: list[dict[str, Any]] = []
    validation_candidates: list[dict[str, Any]] = []
    for minute, trade in enumerate(selected):
        if trade is None:
            continue
        boundary = day_start + minute * MINUTE_MS
        prior = predecessor[minute]
        row = {
            "minute_boundary_timestamp": boundary,
            "first_trade_timestamp": trade.event_time_ms,
            "first_trade_id": trade.trade_id,
            "price": trade.price,
            "quantity": trade.quantity,
            "quote_quantity": trade.quote_quantity,
            "is_buyer_maker": trade.is_buyer_maker,
            "wait_ms": trade.event_time_ms - boundary,
            "source_date": trade.source_date,
            "source_archive_name": trade.source_archive_name,
            "source_checksum": trade.source_checksum,
            "source_row_index": trade.source_row_index,
        }
        rows.append(row)
        if (
            minute in {0, 1, 719, 1438, 1439}
            or trade.event_time_ms == boundary
            or int(hashlib.sha256(f"{day}:{minute}".encode()).hexdigest()[:8], 16) % 31 == 0
        ):
            validation_candidates.append(
                {
                    **row,
                    "previous_trade_timestamp": prior.event_time_ms if prior else "",
                    "previous_trade_id": prior.trade_id if prior else "",
                    "proof_selected_not_before_boundary": trade.event_time_ms >= boundary,
                    "proof_predecessor_before_boundary":
                    prior is None or prior.event_time_ms < boundary,
                }
            )

    if rows:
        high_minute = max(range(1440), key=minute_counts.__getitem__)
        if selected[high_minute] is not None:
            validation_candidates.append(
                {**rows[high_minute], "sample_category": "HIGH_VOLUME"}
            )
        nonzero = [minute for minute, count in enumerate(minute_counts) if count]
        if nonzero:
            low_minute = min(nonzero, key=minute_counts.__getitem__)
            if selected[low_minute] is not None:
                validation_candidates.append(
                    {**rows[low_minute], "sample_category": "LOW_VOLUME"}
                )
    return rows, {
        "raw_trade_count": raw_count,
        "first_trade_timestamp": first_trade.event_time_ms if first_trade else None,
        "last_trade_timestamp": last_trade.event_time_ms if last_trade else None,
        "resolved_boundaries": len(rows),
        "unresolved_boundaries": 1440 - len(rows),
        "validation_candidates": validation_candidates,
    }


def write_index_partition(path: Path, rows: list[dict[str, Any]]) -> str:
    import pyarrow as pa
    import pyarrow.parquet as pq

    schema = pa.schema(
        [
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
        ]
    )
    data = {field.name: [row[field.name] for row in rows] for field in schema}
    table = pa.Table.from_pydict(data, schema=schema)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.unlink(missing_ok=True)
    pq.write_table(table, temporary, compression="zstd")
    # A partition becomes visible only after a complete Parquet footer exists.
    if pq.ParquetFile(temporary).metadata.num_rows != len(rows):
        raise ValueError("temporary index row count validation failed")
    os.replace(temporary, path)
    return sha256_file(path)


def _free_gb(path: Path) -> float:
    return shutil.disk_usage(path).free / (1024**3)


def _archive_uncompressed_bytes(archive: Path) -> int:
    with zipfile.ZipFile(archive) as bundle:
        return sum(member.file_size for member in bundle.infolist())


def _first_trade(
    *, symbol: str, day: date, archive: Path, checksum: str
) -> IndexedTrade:
    first: tuple[int, TradeEvent] | None = None
    for row_index, trade in enumerate(
        iter_raw_trade_archive(archive, symbol=symbol, validate_order=False)
    ):
        if first is None or (trade.event_time_ns, int(trade.trade_id)) < (
            first[1].event_time_ns,
            int(first[1].trade_id),
        ):
            first = row_index, trade
    if first is None:
        raise ValueError(f"empty official raw trade archive: {archive}")
    return to_indexed(
        first[1],
        row_index=first[0],
        source_day=day,
        archive_name=archive.name,
        checksum=checksum,
    )


def process_day(
    *,
    symbol: str,
    day: date,
    cache_root: Path,
    index_root: Path,
    state_root: Path,
    safety_floor_gb: float,
) -> dict[str, Any]:
    cache_root.mkdir(parents=True, exist_ok=True)
    output = index_root / f"symbol={symbol}" / f"date={day.isoformat()}" / "part-0.parquet"
    state = state_root / f"symbol={symbol}" / f"date={day.isoformat()}.json"
    if output.is_file() and state.is_file():
        prior = json.loads(state.read_text(encoding="utf-8"))
        if prior.get("validation_status") == "PASSED" and prior.get("output_sha256") == sha256_file(output):
            return {**prior, "status": "SKIPPED_VALIDATED"}
    free_before = _free_gb(cache_root)
    if free_before < safety_floor_gb:
        raise RuntimeError(f"disk safety floor breached before {symbol} {day}: {free_before:.2f} GB")
    archive, checksum = download_verified_archive(symbol=symbol, day=day, cache_root=cache_root)
    compressed_bytes = archive.stat().st_size
    extracted_bytes = _archive_uncompressed_bytes(archive)
    indexed_trades = (
        to_indexed(
            trade,
            row_index=row_index,
            source_day=day,
            archive_name=archive.name,
            checksum=checksum,
        )
        for row_index, trade in enumerate(
            iter_raw_trade_archive(archive, symbol=symbol, validate_order=False)
        )
    )
    rows, summary = build_minute_index_rows_from_source(indexed_trades, day=day)
    lookahead_archive: Path | None = None
    if summary["unresolved_boundaries"]:
        lookahead_day = day + timedelta(days=1)
        lookahead_archive, lookahead_checksum = download_verified_archive(
            symbol=symbol, day=lookahead_day, cache_root=cache_root
        )
        future = _first_trade(
            symbol=symbol,
            day=lookahead_day,
            archive=lookahead_archive,
            checksum=lookahead_checksum,
        )
        indexed_trades = (
            to_indexed(
                trade,
                row_index=row_index,
                source_day=day,
                archive_name=archive.name,
                checksum=checksum,
            )
            for row_index, trade in enumerate(
                iter_raw_trade_archive(archive, symbol=symbol, validate_order=False)
            )
        )
        rows, summary = build_minute_index_rows_from_source(
            indexed_trades, day=day, future_trade=future
        )
    if len(rows) != 1440 or summary["unresolved_boundaries"] != 0:
        raise ValueError(f"unresolved minute boundaries for {symbol} {day}: {summary}")
    if any(row["wait_ms"] < 0 for row in rows):
        raise ValueError("negative execution wait")
    output_sha = write_index_partition(output, rows)
    record = {
        "symbol": symbol,
        "date": day.isoformat(),
        "source_archive": archive.name,
        "source_checksum": checksum,
        "raw_trade_count": summary["raw_trade_count"],
        "minute_index_row_count": len(rows),
        "first_trade_timestamp": summary["first_trade_timestamp"],
        "last_trade_timestamp": summary["last_trade_timestamp"],
        "output_parquet": str(output),
        "output_sha256": output_sha,
        "compressed_temp_bytes": compressed_bytes,
        "virtual_extracted_bytes": extracted_bytes,
        "validation_status": "PASSED",
        "resolved_boundaries": len(rows),
        "unresolved_boundaries": 0,
        "free_gb_before": free_before,
        "free_gb_after": _free_gb(cache_root),
        "validation_candidates": summary["validation_candidates"],
    }
    atomic_json(state, record)
    archive.unlink(missing_ok=True)
    # A look-ahead archive is intentionally retained only as the next day's
    # cache and will be deleted after that partition commits.
    return {**record, "status": "COMPLETE"}


def consolidate_manifest(output_root: Path, state_root: Path) -> dict[str, Any]:
    records = []
    samples = []
    for state in sorted(state_root.glob("symbol=*/date=*.json")):
        record = json.loads(state.read_text(encoding="utf-8"))
        samples.extend(
            {"symbol": record["symbol"], "date": record["date"], **sample}
            for sample in record.pop("validation_candidates", [])
        )
        records.append(record)
    if records:
        fields = [key for key in records[0] if not isinstance(records[0][key], (list, dict))]
        atomic_csv(output_root / "tick_execution_index_manifest.csv", records, fields)
    # Deterministic capped sample: >=100 per completed symbol, with category
    # candidates retained before hash-ordered generic observations.
    selected = []
    for symbol in SYMBOLS:
        candidates = [sample for sample in samples if sample["symbol"] == symbol]
        candidates.sort(
            key=lambda row: (
                0 if row.get("sample_category") in {"HIGH_VOLUME", "LOW_VOLUME"} else 1,
                hashlib.sha256(
                    f"{row['symbol']}:{row['minute_boundary_timestamp']}".encode()
                ).hexdigest(),
            )
        )
        selected.extend(candidates[:100])
    if selected:
        fields = sorted({key for row in selected for key in row})
        atomic_csv(output_root / "tick_execution_index_spot_validation.csv", selected, fields)
    return {
        "manifest_rows": len(records),
        "validation_sample_rows": len(selected),
        "completed_symbols": sum(
            all(any(r["symbol"] == symbol and r["date"] == day.isoformat() for r in records)
                for day in dates(DEFAULT_START, DEFAULT_END_EXCLUSIVE))
            for symbol in SYMBOLS
        ),
    }


def run_build(args: argparse.Namespace) -> int:
    window = json.loads((args.output_root / "boss_tick_index_data_window.json").read_text(encoding="utf-8"))
    if window.get("status") != "PASSED":
        raise RuntimeError("official archive availability gate has not passed")
    start = date.fromisoformat(window["common_start"])
    end = date.fromisoformat(window["common_end_exclusive"])
    symbols = tuple(args.symbol) if args.symbol else SYMBOLS
    selected_days = (
        sorted(set(args.date))
        if args.date
        else dates(args.start or start, args.end_exclusive or end)
    )
    if not selected_days or any(day < start or day >= end for day in selected_days):
        raise ValueError("selected build dates must lie inside the frozen common interval")
    progress_identity = (
        ";".join(symbols) + ":" + selected_days[0].isoformat()
        + ":" + selected_days[-1].isoformat()
    )
    progress_suffix = (
        "_" + hashlib.sha256(progress_identity.encode()).hexdigest()[:10]
        if args.symbol
        else ""
    )
    progress_path = args.output_root / f"tick_index_build_progress{progress_suffix}.json"
    planned = len(symbols) * len(selected_days)
    completed = 0
    peak_compressed = 0
    peak_virtual_extracted = 0
    try:
        for symbol in symbols:
            for day in selected_days:
                result = process_day(
                    symbol=symbol,
                    day=day,
                    cache_root=args.cache_root,
                    index_root=args.output_root / "tick_execution_index",
                    state_root=args.output_root / "tick_execution_index_state",
                    safety_floor_gb=args.safety_floor_gb,
                )
                completed += 1
                peak_compressed = max(peak_compressed, int(result["compressed_temp_bytes"]))
                peak_virtual_extracted = max(
                    peak_virtual_extracted, int(result["virtual_extracted_bytes"])
                )
                atomic_json(
                    progress_path,
                    {
                        "status": "RUNNING",
                        "planned_partitions": planned,
                        "completed_partitions": completed,
                        "current_symbol": symbol,
                        "current_date": day.isoformat(),
                        "peak_compressed_temp_bytes": peak_compressed,
                        "peak_virtual_extracted_bytes": peak_virtual_extracted,
                        "free_gb": _free_gb(args.cache_root),
                    },
                )
        consolidated = consolidate_manifest(
            args.output_root, args.output_root / "tick_execution_index_state"
        )
        # All planned partitions are now durable; cached look-ahead archives are
        # no longer needed.  Delete individual files, never a broad directory.
        for path in sorted(args.cache_root.glob("*")):
            if path.is_file() and path.suffix in {".zip", ".part"}:
                path.unlink()
        temporary_bytes = sum(path.stat().st_size for path in args.cache_root.glob("*") if path.is_file())
        final_index_bytes = sum(
            path.stat().st_size
            for path in (args.output_root / "tick_execution_index").glob(
                "symbol=*/date=*/*.parquet"
            )
        )
        summary = {
            "status": "PASSED",
            "planned_partitions": planned,
            "completed_partitions": completed,
            "peak_compressed_temp_bytes": peak_compressed,
            "peak_virtual_extracted_bytes": peak_virtual_extracted,
            "temporary_raw_bytes_remaining": temporary_bytes,
            "final_tick_index_bytes": final_index_bytes,
            "free_gb": _free_gb(args.cache_root),
            **consolidated,
        }
        atomic_json(progress_path, summary)
        if temporary_bytes:
            raise ValueError(f"successful ingest left {temporary_bytes} raw temporary bytes")
        return 0
    except Exception as exc:
        atomic_json(
            progress_path,
            {
                "status": "BLOCKED",
                "planned_partitions": planned,
                "completed_partitions": completed,
                "error": f"{type(exc).__name__}: {exc}",
            },
        )
        raise


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    audit = sub.add_parser("audit")
    audit.add_argument("--start", type=date.fromisoformat, default=DEFAULT_START)
    audit.add_argument("--end-exclusive", type=date.fromisoformat, default=DEFAULT_END_EXCLUSIVE)
    audit.add_argument("--workers", type=int, default=16)
    build = sub.add_parser("build")
    build.add_argument("--symbol", action="append", choices=SYMBOLS)
    build.add_argument("--date", action="append", type=date.fromisoformat)
    build.add_argument("--start", type=date.fromisoformat)
    build.add_argument("--end-exclusive", type=date.fromisoformat)
    build.add_argument("--safety-floor-gb", type=float, default=15.0)
    for command in (audit, build):
        command.add_argument(
            "--output-root",
            type=Path,
            default=ROOT / "outputs/baseline_evaluation/boss_multitimeframe_tick_screen",
        )
    build.add_argument(
        "--cache-root", type=Path, default=ROOT / "outputs/tmp_tick_ingest"
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    args.output_root.mkdir(parents=True, exist_ok=True)
    if args.command == "audit":
        summary = audit_official_availability(
            output_root=args.output_root,
            start=args.start,
            end_exclusive=args.end_exclusive,
            workers=args.workers,
        )
        print(json.dumps(summary, indent=2))
        return 0 if summary["status"] == "PASSED" else 2
    return run_build(args)


if __name__ == "__main__":
    raise SystemExit(main())
