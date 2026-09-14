#!/usr/bin/env python3
"""Prepare the frozen five-year maker-data acquisition manifests.

This is a data-only workflow. It never reads strategy performance beyond the
already-frozen selected-case identity columns and never launches a backtest.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from datetime import date, timedelta
from pathlib import Path

import pandas as pd


START = date(2021, 7, 1)
END = date(2026, 7, 1)
EXPECTED_DAYS = (END - START).days


def atomic_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    frame.to_csv(temporary, index=False)
    os.replace(temporary, path)


def atomic_json(value: object, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(temporary, path)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def split_intervals(days: list[date]) -> str:
    if not days:
        return ""
    days = sorted(days)
    intervals: list[str] = []
    begin = previous = days[0]
    for current in days[1:]:
        if current != previous + timedelta(days=1):
            intervals.append(f"{begin.isoformat()}..{(previous + timedelta(days=1)).isoformat()}")
            begin = current
        previous = current
    intervals.append(f"{begin.isoformat()}..{(previous + timedelta(days=1)).isoformat()}")
    return ";".join(intervals)


def inventory_from_pilot(pilot_root: Path) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for manifest in sorted(pilot_root.glob("ingest_manifest_*.csv")):
        frame = pd.read_csv(manifest)
        for (symbol, data_type), group in frame.groupby(["symbol", "data_type"]):
            passed = group[group.status.eq("PASSED")].copy()
            paths = [Path(str(value)) for value in passed.converted_path]
            existing = [path for path in paths if path.exists()]
            rows.append({
                "symbol": symbol,
                "data_type": "L1_BBO" if data_type == "bookTicker" else "RAW_TRADES",
                "start": passed.date.min() if len(passed) else "",
                "end": (date.fromisoformat(str(passed.date.max())) + timedelta(days=1)).isoformat() if len(passed) else "",
                "days": int(passed.date.nunique()),
                "path": str(pilot_root / ("l1_quotes" if data_type == "bookTicker" else "raw_trades")),
                "size_bytes": sum(path.stat().st_size for path in existing),
                "source": "Binance Vision USD-M Futures daily archive",
                "validated": bool(len(passed) and passed.checksum_valid.astype(bool).all() and len(existing) == len(passed)),
            })
    return pd.DataFrame(rows)


def public_coverage_rows(availability: pd.DataFrame, symbols: list[str]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for record in availability[availability.symbol.isin(symbols)].to_dict("records"):
        rows.append({
            "source": "Binance Vision public daily archive",
            "source_url": f"https://data.binance.vision/?prefix={record['listing_prefix']}",
            "symbol": record["symbol"],
            "data_type": "L1_BBO" if record["data_type"] == "bookTicker" else "RAW_TRADES",
            "available_start": record["first_public_archive_date"],
            "available_end": record["last_public_archive_date"],
            "complete_days": int(record["available_required_days"]),
            "missing_days": int(record["missing_days"]),
            "covers_5y_window": bool(record["full_window_complete"]),
            "public": True,
            "authenticated": False,
            "paid": False,
            "access_requirement": "NONE",
            "schema": "bookTicker BBO event CSV" if record["data_type"] == "bookTicker" else "official raw trades CSV",
            "checksum_support": int(record["checksum_missing_days"]) == 0,
            "estimated_size": int(record["compressed_bytes_for_available_required_days"]),
            "recommended": record["data_type"] == "trades",
            "coverage_basis": "actual S3 object listing",
        })
    return rows


def restricted_source_rows(symbols: list[str]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    quote_templates = [
        {
            "source": "Binance official Historical Futures Order Book API",
            "source_url": "https://github.com/binance/binance-public-data/blob/master/Futures_Order_Book_Download.md",
            "data_type": "L2_MBP",
            "available_start": "2020-07-01",
            "available_end": "CURRENT (provider statement; entitlement probe required)",
            "complete_days": "UNKNOWN_UNTIL_AUTHENTICATED_PROBE",
            "missing_days": "UNKNOWN_UNTIL_AUTHENTICATED_PROBE",
            "covers_5y_window": "POTENTIALLY",
            "public": False,
            "authenticated": True,
            "paid": False,
            "access_requirement": "Binance Futures account + API key whitelisted for historical order-book downloads",
            "schema": "T_DEPTH / S_DEPTH / T_DEPTH_BACKFILL L2",
            "checksum_support": "NOT_DOCUMENTED",
            "estimated_size": "UNKNOWN_UNTIL_DOWNLOAD_ID_METADATA",
            "recommended": True,
            "coverage_basis": "official dataset documentation; actual account entitlement not available",
        },
        {
            "source": "Tardis.dev Binance USDT Futures",
            "source_url": "https://docs.tardis.dev/historical-data-details/binance-futures",
            "data_type": "L2_MBP_OR_L1_BBO",
            "available_start": "2019-11-17",
            "available_end": "CURRENT",
            "complete_days": "PROVIDER_COVERAGE_QUERY_REQUIRED",
            "missing_days": "PROVIDER_INCIDENT_FEED_REQUIRED",
            "covers_5y_window": True,
            "public": False,
            "authenticated": True,
            "paid": True,
            "access_requirement": "Tardis API subscription/key with sufficient back-history entitlement",
            "schema": "exchange-native depth/bookTicker/trades or normalized CSV",
            "checksum_support": "VENDOR_MANAGED",
            "estimated_size": "QUOTE_REQUIRED; L2 materially larger than L1",
            "recommended": True,
            "coverage_basis": "vendor documentation; full files require subscription",
        },
        {
            "source": "Kaiko Cloud Delivery",
            "source_url": "https://docs.kaiko.com/cloud-delivery/data-feeds/level-1-tick-level/best-bids-and-asks-top-of-book",
            "data_type": "L1_BBO",
            "available_start": "ENTITLEMENT_CATALOG_REQUIRED",
            "available_end": "CURRENT",
            "complete_days": "ENTITLEMENT_CATALOG_REQUIRED",
            "missing_days": "ENTITLEMENT_CATALOG_REQUIRED",
            "covers_5y_window": "UNCONFIRMED",
            "public": False,
            "authenticated": True,
            "paid": True,
            "access_requirement": "Kaiko commercial cloud-delivery entitlement",
            "schema": "daily tick-level BBA CSV.GZ plus trades",
            "checksum_support": "VENDOR_MANAGED",
            "estimated_size": "QUOTE_REQUIRED",
            "recommended": False,
            "coverage_basis": "vendor schema documentation; exact Binance Futures instrument coverage gated",
        },
        {
            "source": "Amberdata Historical Market Data",
            "source_url": "https://docs.amberdata.io/reference/market-data",
            "data_type": "ORDER_BOOK",
            "available_start": "ENTITLEMENT_CATALOG_REQUIRED",
            "available_end": "CURRENT",
            "complete_days": "ENTITLEMENT_CATALOG_REQUIRED",
            "missing_days": "ENTITLEMENT_CATALOG_REQUIRED",
            "covers_5y_window": "UNCONFIRMED",
            "public": False,
            "authenticated": True,
            "paid": True,
            "access_requirement": "Amberdata API/commercial data license",
            "schema": "vendor historical order-book and trade API",
            "checksum_support": "VENDOR_MANAGED",
            "estimated_size": "QUOTE_REQUIRED",
            "recommended": False,
            "coverage_basis": "vendor product documentation; exact entitlement probe required",
        },
    ]
    for symbol in symbols:
        for template in quote_templates:
            rows.append({"symbol": symbol, **template})
            if template["source"].startswith("Binance official"):
                continue
            trade = dict(template)
            trade["data_type"] = "RAW_TRADES"
            if trade["source"].startswith("Tardis"):
                trade["schema"] = "exchange-native trade stream or normalized trades CSV"
            elif trade["source"].startswith("Kaiko"):
                trade["schema"] = "daily tick-level trades CSV.GZ"
            else:
                trade["schema"] = "vendor historical trades API/download"
            rows.append({"symbol": symbol, **trade})
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--previous-root", type=Path, required=True)
    parser.add_argument("--pilot-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, required=True)
    args = parser.parse_args()

    previous = args.previous_root.resolve()
    output = args.output_root.resolve()
    data_root = args.data_root.resolve()
    output.mkdir(parents=True, exist_ok=True)
    (output / "logs").mkdir(exist_ok=True)

    selected_path = previous / "selection/long_horizon_first_tick_selected_cases.csv"
    selected = pd.read_csv(selected_path)
    required_symbols = sorted(selected.symbol.astype(str).unique().tolist())
    availability = pd.read_csv(previous / "long_horizon_maker_data_availability.csv")
    storage = pd.read_csv(previous / "long_horizon_storage_plan.csv")
    required_availability = availability[availability.symbol.isin(required_symbols)].copy()
    required_storage = storage[storage.symbol.isin(required_symbols)].copy()

    inventory = inventory_from_pilot(args.pilot_root.resolve())
    atomic_csv(inventory, output / "existing_maker_data_inventory.csv")

    coverage = pd.DataFrame(public_coverage_rows(required_availability, required_symbols) + restricted_source_rows(required_symbols))
    atomic_csv(coverage, output / "maker_data_source_coverage.csv")

    free_before = shutil.disk_usage(data_root.anchor or str(data_root)).free
    total_disk = shutil.disk_usage(data_root.anchor or str(data_root)).total
    reserve = int(total_disk * 0.20)
    plan = required_storage.copy()
    plan["acquisition_batching"] = "4 concurrent monthly partitions; edge/pilot partitions daily"
    plan["estimated_peak_temporary_bytes"] = 40 * 1024**3
    plan["peak_estimate_basis"] = "conservative 4×(monthly source ZIP + streaming Parquet temporary output)"
    plan["disk_total_bytes_before"] = total_disk
    plan["disk_free_bytes_before"] = free_before
    plan["required_free_reserve_bytes"] = reserve
    plan["storage_root"] = str(data_root)
    atomic_csv(plan, output / "five_year_storage_plan.csv")

    expected_dates = {START + timedelta(days=index) for index in range(EXPECTED_DAYS)}
    status_rows: list[dict[str, object]] = []
    blockers: list[dict[str, object]] = []
    for symbol in required_symbols:
        quote = required_availability[(required_availability.symbol == symbol) & required_availability.data_type.eq("bookTicker")].iloc[0]
        trades = required_availability[(required_availability.symbol == symbol) & required_availability.data_type.eq("trades")].iloc[0]
        quote_days = int(quote.available_required_days)
        trade_days = int(trades.available_required_days)
        missing_quote = EXPECTED_DAYS - quote_days
        status_rows.append({
            "symbol": symbol,
            "required_start": START.isoformat(),
            "required_end": END.isoformat(),
            "quote_source": "Binance Vision public bookTicker + acquisition-required L2/L1 source",
            "quote_tier": "L1_PUBLIC_PARTIAL; L2_AUTHENTICATED_POTENTIAL",
            "quote_days_required": EXPECTED_DAYS,
            "quote_days_available": quote_days,
            "quote_coverage_pct": quote_days / EXPECTED_DAYS,
            "trade_days_required": EXPECTED_DAYS,
            "trade_days_available": trade_days,
            "five_year_maker_ready": False,
            "missing_interval": "2021-07-01..2023-05-16;2024-03-31..2026-07-01",
            "blocker": "complete historical BBO/depth requires a whitelisted Binance historical-order-book API key or paid vendor entitlement",
        })
        if missing_quote:
            blockers.append({
                "symbol": symbol,
                "missing_start": "2021-07-01;2024-03-31",
                "missing_end": "2023-05-16;2026-07-01",
                "required_source": "Tier C L1 BBO or better; preferred Binance official T_DEPTH L2",
                "provider": "Binance official Historical Futures Order Book API (preferred); Tardis alternative",
                "access_type": "AUTHENTICATED_WHITELIST_OR_PAID_VENDOR",
                "estimated_cost": "Binance whitelist: no published fee; Tardis/vendor: public plan or custom quote",
                "required_user_action": "Provide/authorize a Binance Futures API key already whitelisted for historical order-book downloads, or approve and provide a vendor entitlement/API key",
            })
    atomic_csv(pd.DataFrame(status_rows), output / "five_year_maker_data_status.csv")
    atomic_csv(pd.DataFrame(blockers), output / "acquisition_blockers.csv")

    run_manifest = selected[[column for column in ["strategy_id", "source_origin", "semantic_group_id", "symbol", "timeframe"] if column in selected]].copy()
    run_manifest["start"] = START.isoformat()
    run_manifest["end"] = END.isoformat()
    run_manifest["quote_data_ready"] = False
    run_manifest["trade_data_ready"] = run_manifest.symbol.map(
        required_availability[required_availability.data_type.eq("trades")].set_index("symbol").full_window_complete.astype(bool)
    ).fillna(False)
    run_manifest["funding_ready"] = True
    run_manifest["FIRST_TICK_NORMAL_required"] = True
    run_manifest["MAKER_NORMAL_required"] = True
    run_manifest["FIRST_TICK_REVERSE_required"] = False
    run_manifest["MAKER_REVERSE_required"] = False
    atomic_csv(run_manifest, output / "five_year_maker_run_manifest.csv")

    empty_manifest_columns = [
        "symbol", "data_type", "date", "source", "source_path_archive", "source_checksum",
        "rows", "first_timestamp", "last_timestamp", "output_path", "output_sha256", "validation_status",
        "compressed_bytes", "uncompressed_bytes", "converted_bytes", "bytes_reclaimed", "provenance_mode",
    ]
    for filename in ("acquisition_manifest.csv", "five_year_maker_data_manifest.csv"):
        path = output / filename
        if not path.exists():
            atomic_csv(pd.DataFrame(columns=empty_manifest_columns), path)

    # Register the already validated March pilot as reused source partitions.
    acquisition_path = output / "acquisition_manifest.csv"
    acquisition = pd.read_csv(acquisition_path)
    acquisition_rows = acquisition.to_dict("records")
    existing_keys = {(str(row.get("symbol")), str(row.get("data_type")), str(row.get("date"))) for row in acquisition_rows}
    for pilot_manifest in sorted(args.pilot_root.resolve().glob("ingest_manifest_*.csv")):
        pilot = pd.read_csv(pilot_manifest)
        pilot = pilot[
            pilot.symbol.astype(str).isin(required_symbols)
            & pilot.status.astype(str).eq("PASSED")
            & pilot.checksum_valid.astype(bool)
        ]
        for row in pilot.to_dict("records"):
            key = (str(row["symbol"]), str(row["data_type"]), str(row["date"]))
            path = Path(str(row["converted_path"]))
            if key in existing_keys or not path.is_file():
                continue
            acquisition_rows.append({
                "symbol": row["symbol"], "data_type": row["data_type"], "date": row["date"],
                "source": "Binance Vision USD-M Futures daily archive",
                "source_path_archive": row.get("filename", ""),
                "source_checksum": row.get("converted_sha256", ""),
                "rows": int(row.get("rows", 0)), "first_timestamp": row.get("first_timestamp", ""),
                "last_timestamp": row.get("last_timestamp", ""), "output_path": str(path),
                "output_sha256": row.get("converted_sha256", ""), "validation_status": "PASSED",
                "compressed_bytes": int(row.get("compressed_bytes", 0)),
                "uncompressed_bytes": int(row.get("uncompressed_bytes", 0)),
                "converted_bytes": path.stat().st_size, "bytes_reclaimed": 0,
                "provenance_mode": "REFERENCE_REUSE_VALIDATED_MARCH_PILOT",
            })
            existing_keys.add(key)
    atomic_csv(pd.DataFrame(acquisition_rows), acquisition_path)

    summary = {
        "status": "PARTIAL_DATA_ACQUIRED" if len(inventory) else "USER_ACTION_REQUIRED",
        "frozen_window": {"start": START.isoformat(), "end_exclusive": END.isoformat(), "days": EXPECTED_DAYS},
        "selected_case_manifest": str(selected_path),
        "selected_case_manifest_sha256": sha256(selected_path),
        "required_symbol_count": len(required_symbols),
        "required_symbols": required_symbols,
        "selected_case_count": len(selected),
        "public_quote_days": int(required_availability[required_availability.data_type.eq("bookTicker")].available_required_days.sum()),
        "public_trade_days": int(required_availability[required_availability.data_type.eq("trades")].available_required_days.sum()),
        "disk_total_bytes_before": total_disk,
        "disk_free_bytes_before": free_before,
        "disk_reserve_bytes": reserve,
        "full_maker_backtest_started": False,
        "live_trading_used": False,
        "validation": "PARTIAL",
    }
    atomic_json(summary, output / "validation_summary.json")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
