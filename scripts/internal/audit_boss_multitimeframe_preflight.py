#!/usr/bin/env python3
"""Audit the immutable inputs for the boss multi-timeframe/tick experiment."""

from __future__ import annotations

import argparse
import json
import os
import shutil
from urllib.request import Request, urlopen
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow.parquet as pq


SYMBOLS = (
    "XRPUSDT", "DOGEUSDT", "SUIUSDT", "BNBUSDT", "ETHUSDT",
    "BTCUSDT", "1000PEPEUSDT", "SOLUSDT", "ADAUSDT",
)
START = date(2024, 7, 1)
END_EXCLUSIVE = date(2026, 6, 30)


def dates(start: date = START, end: date = END_EXCLUSIVE) -> list[str]:
    return [(start + timedelta(days=i)).isoformat() for i in range((end - start).days)]


def atomic_csv(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    frame.to_csv(temporary, index=False, encoding="utf-8-sig")
    os.replace(temporary, path)


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def partition_audit(root: Path, symbol: str, data_type: str, frequency: str) -> dict[str, Any]:
    base = (
        root / "asset_class=crypto" / "exchange=BINANCE" / "venue_type=futures_um"
        / f"symbol={symbol}" / f"data_type={data_type}" / f"freq={frequency}"
    )
    required = dates()
    existing = sorted(
        path.name.removeprefix("date=")
        for path in base.glob("date=*")
        if path.is_dir()
    ) if base.is_dir() else []
    existing_set = set(existing)
    missing = [day for day in required if day not in existing_set]
    all_files = sorted(base.glob("date=*/*.parquet")) if base.is_dir() else []
    files = [path for path in all_files if path.parent.name.removeprefix("date=") in set(required)]
    rows = 0
    invalid_files = 0
    schema_names: set[str] = set()
    bytes_total = 0
    for path in files:
        try:
            parquet = pq.ParquetFile(path)
            rows += parquet.metadata.num_rows
            schema_names.update(parquet.schema_arrow.names)
            bytes_total += path.stat().st_size
        except Exception:
            invalid_files += 1
    required_schema = {
        "bar": {"ts", "open", "high", "low", "close", "volume", "quote_volume"},
        "funding_rate": {"ts", "funding_rate"},
        "trade": {"ts", "trade_id", "price", "quantity", "quote_quantity"},
    }[data_type]
    schema_ok = required_schema.issubset(schema_names)
    return {
        "symbol": symbol,
        "data_type": data_type,
        "frequency": frequency,
        "required_start": START.isoformat(),
        "required_end_exclusive": END_EXCLUSIVE.isoformat(),
        "required_partition_count": len(required),
        "existing_partition_count_all_dates": len(existing),
        "complete_required_partition_count": len(required) - len(missing),
        "missing_required_partition_count": len(missing),
        "first_partition": existing[0] if existing else "",
        "last_partition": existing[-1] if existing else "",
        "parquet_file_count_required_window": len(files),
        "row_count": rows,
        "size_bytes": bytes_total,
        "schema_valid": schema_ok,
        "invalid_parquet_files": invalid_files,
        "missing_dates": ";".join(missing),
        "checksum_status": (
            "PERSISTED_FROM_CHECKSUM_PIPELINE;DAILY_VALIDATION_MANIFEST_NOT_COLOCATED"
            if data_type == "trade" and files else "NOT_AVAILABLE"
        ),
        "raw_trade_integrity_status": (
            "SCHEMA_AND_PARQUET_READABLE" if data_type == "trade" and schema_ok and not invalid_files
            else "MISSING" if data_type == "trade" and not files else "NOT_APPLICABLE"
        ),
        "complete_for_requested_window": not missing and schema_ok and not invalid_files,
    }


def official_archive_estimate(output_root: Path, availability: pd.DataFrame) -> dict[str, Any]:
    sample_days = (
        "2024-07-01", "2024-10-01", "2025-01-01", "2025-04-01",
        "2025-07-01", "2025-10-01", "2026-01-01", "2026-04-01", "2026-06-29",
    )
    rows: list[dict[str, Any]] = []
    for symbol in SYMBOLS:
        sizes: list[int] = []
        for day in sample_days:
            url = (
                "https://data.binance.vision/data/futures/um/daily/trades/"
                f"{symbol}/{symbol}-trades-{day}.zip"
            )
            try:
                with urlopen(Request(url, method="HEAD"), timeout=30) as response:
                    size = int(response.headers["Content-Length"])
                status = "AVAILABLE"
                sizes.append(size)
            except Exception as exc:
                size = 0
                status = f"ERROR:{type(exc).__name__}"
            rows.append({"symbol": symbol, "sample_date": day, "archive_bytes": size, "status": status, "url": url})
    samples = pd.DataFrame(rows)
    atomic_csv(output_root / "official_raw_trade_archive_samples.csv", samples)
    means = samples.loc[samples.status.eq("AVAILABLE")].groupby("symbol").archive_bytes.mean()
    btc = availability.loc[
        availability.symbol.eq("BTCUSDT") & availability.data_type.eq("trade")
    ].iloc[0]
    btc_persisted_daily = float(btc.size_bytes) / max(float(btc.complete_required_partition_count), 1.0)
    btc_archive_daily = float(means.get("BTCUSDT", 0.0))
    persistence_ratio = btc_persisted_daily / btc_archive_daily if btc_archive_daily else float("nan")
    missing = availability.loc[
        availability.data_type.eq("trade") & ~availability.complete_for_requested_window,
        "symbol",
    ].tolist()
    estimated = float(sum(means.get(symbol, 0.0) for symbol in missing) * len(dates()) * persistence_ratio)
    summary_rows = [{
        "symbol": symbol,
        "sample_mean_archive_bytes_per_day": float(means.get(symbol, 0.0)),
        "btc_observed_parquet_to_sample_archive_ratio": persistence_ratio,
        "estimated_normalized_tick_bytes_requested_window": float(means.get(symbol, 0.0) * len(dates()) * persistence_ratio),
        "currently_missing_tick_window": symbol in missing,
    } for symbol in SYMBOLS]
    atomic_csv(output_root / "boss_multitimeframe_storage_estimate.csv", pd.DataFrame(summary_rows))
    return {
        "sample_days": len(sample_days),
        "btc_observed_parquet_to_sample_archive_ratio": persistence_ratio,
        "estimated_missing_normalized_tick_bytes": estimated,
        "missing_tick_symbols": missing,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--market-root", type=Path, required=True)
    parser.add_argument("--final-master", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()

    master = pd.read_csv(args.final_master)
    scope = master.loc[master.record_type.eq("STRATEGY_INDEX"), ["strategy_id", "canonical_timeframe"]].copy()
    scope["eligible_for_intraday_resample"] = scope.canonical_timeframe.eq("1m")
    scope["exclusion_reason"] = scope.eligible_for_intraday_resample.map(
        {True: "", False: "SOURCE_NATIVE_NON_1M_NOT_FORCED_TO_INTRADAY"}
    )
    if len(scope) != 280 or scope.strategy_id.nunique() != 280:
        raise AssertionError("final workbook strategy universe is not exactly 280 identities")
    atomic_csv(args.output_root / "boss_multitimeframe_strategy_scope.csv", scope)

    rows = []
    for symbol in SYMBOLS:
        rows.append(partition_audit(args.market_root, symbol, "bar", "1m"))
        rows.append(partition_audit(args.market_root, symbol, "funding_rate", "settlement"))
        rows.append(partition_audit(args.market_root, symbol, "trade", "tick"))
    availability = pd.DataFrame(rows)
    atomic_csv(args.output_root / "boss_multitimeframe_data_availability.csv", availability)
    storage = official_archive_estimate(args.output_root, availability)
    complete = availability.groupby("symbol").complete_for_requested_window.all()
    common_ready = bool(complete.reindex(SYMBOLS, fill_value=False).all())
    disk = shutil.disk_usage(args.market_root)
    summary = {
        "status": "PREFLIGHT_PASSED" if common_ready else "PREFLIGHT_BLOCKED",
        "workbook_strategies": 280,
        "eligible_1m_strategies": int(scope.eligible_for_intraday_resample.sum()),
        "excluded_non_1m_strategies": int((~scope.eligible_for_intraday_resample).sum()),
        "symbols": list(SYMBOLS),
        "timeframes": ["1m", "5m", "10m", "15m"],
        "logical_cases": int(scope.eligible_for_intraday_resample.sum()) * len(SYMBOLS) * 4,
        "requested_common_start": START.isoformat(),
        "requested_common_end_exclusive": END_EXCLUSIVE.isoformat(),
        "common_window_ready": common_ready,
        "complete_symbols": [symbol for symbol, value in complete.items() if value],
        "incomplete_symbols": [symbol for symbol in SYMBOLS if not bool(complete.get(symbol, False))],
        "market_root_free_bytes": disk.free,
        **storage,
        "storage_capacity_ready": disk.free >= storage["estimated_missing_normalized_tick_bytes"],
        "production_runs_started": 0,
        "canonical_configs_changed": 0,
        "historical_results_changed": 0,
    }
    atomic_json(args.output_root / "preflight_validation_summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
