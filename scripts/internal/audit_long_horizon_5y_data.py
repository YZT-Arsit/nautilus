#!/usr/bin/env python3
"""Freeze the canonical 5-year window and audit public maker-data coverage.

This script is deliberately metadata-only. It lists Binance Vision S3 objects;
it does not download market-data archives or inspect strategy performance.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd


SYMBOLS = [
    "XRPUSDT", "DOGEUSDT", "SUIUSDT", "BNBUSDT", "ETHUSDT",
    "BTCUSDT", "1000PEPEUSDT", "SOLUSDT", "ADAUSDT",
]
DATA_TYPES = ("bookTicker", "trades")
S3_LIST_URL = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
EXPECTED_START = "2021-07-01T00:00:00+00:00"
EXPECTED_END_INCLUSIVE = "2026-06-30T23:59:00+00:00"


def atomic_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    frame.to_csv(tmp, index=False)
    tmp.replace(path)


def atomic_json(obj: object, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def dates(start: date, end_exclusive: date) -> list[date]:
    return [start + timedelta(days=i) for i in range((end_exclusive - start).days)]


def canonical_windows(paths: list[Path]) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for path in paths:
        frame = pd.read_csv(path, low_memory=False)
        if not {"start_time", "end_time"}.issubset(frame.columns):
            raise ValueError(f"missing window columns: {path}")
        grouped = frame.groupby(["start_time", "end_time"], dropna=False).size()
        for (start, end), count in grouped.items():
            rows.append({"source": str(path), "start_time": start, "end_time": end, "rows": int(count)})
    result = pd.DataFrame(rows)
    exact = result[
        result.start_time.astype(str).eq(EXPECTED_START)
        & result.end_time.astype(str).eq(EXPECTED_END_INCLUSIVE)
    ]
    if exact.source.nunique() != len(paths):
        raise ValueError("canonical 5-year window is not present in every authoritative source")
    return result


def list_prefix(prefix: str) -> list[tuple[str, int]]:
    items: list[tuple[str, int]] = []
    token: str | None = None
    while True:
        params = {"list-type": "2", "prefix": prefix, "max-keys": "1000"}
        if token:
            params["continuation-token"] = token
        request = Request(
            f"{S3_LIST_URL}?{urlencode(params)}",
            headers={"User-Agent": "nautilus-long-horizon-metadata-audit/1.0"},
        )
        with urlopen(request, timeout=60) as response:
            payload = response.read()
        root = ET.fromstring(payload)
        ns = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}
        for entry in root.findall("s3:Contents", ns):
            key = entry.findtext("s3:Key", default="", namespaces=ns)
            size = int(entry.findtext("s3:Size", default="0", namespaces=ns))
            items.append((key, size))
        truncated = root.findtext("s3:IsTruncated", default="false", namespaces=ns).lower() == "true"
        if not truncated:
            break
        token = root.findtext("s3:NextContinuationToken", default="", namespaces=ns)
        if not token:
            raise RuntimeError(f"truncated S3 listing without continuation token: {prefix}")
    return items


def parse_objects(symbol: str, data_type: str, objects: list[tuple[str, int]]) -> tuple[dict[date, int], set[date]]:
    escaped = re.escape(f"{symbol}-{data_type}-")
    zip_re = re.compile(escaped + r"(\d{4}-\d{2}-\d{2})\.zip$")
    checksum_re = re.compile(escaped + r"(\d{4}-\d{2}-\d{2})\.zip\.CHECKSUM$")
    archives: dict[date, int] = {}
    checksums: set[date] = set()
    for key, size in objects:
        if match := zip_re.search(key):
            archives[date.fromisoformat(match.group(1))] = size
        elif match := checksum_re.search(key):
            checksums.add(date.fromisoformat(match.group(1)))
    return archives, checksums


def pilot_ratios(pilot_root: Path) -> dict[tuple[str, str], float]:
    ratios: dict[tuple[str, str], float] = {}
    for path in pilot_root.glob("ingest_manifest_*.csv"):
        frame = pd.read_csv(path)
        frame = frame[(frame.compressed_bytes > 0) & (frame.converted_bytes >= 0)]
        for (symbol, data_type), group in frame.groupby(["symbol", "data_type"]):
            ratios[(str(symbol), str(data_type))] = float(group.converted_bytes.sum() / group.compressed_bytes.sum())
    return ratios


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--pilot-root", type=Path, required=True)
    parser.add_argument("--canonical-source", type=Path, action="append", required=True)
    args = parser.parse_args()

    start = date(2021, 7, 1)
    end_exclusive = date(2026, 7, 1)
    expected = dates(start, end_exclusive)
    expected_set = set(expected)

    window_sources = canonical_windows(args.canonical_source)
    atomic_csv(window_sources, args.output_root / "canonical_window_source_audit.csv")

    freeze = {
        "canonical_5y_start": start.isoformat(),
        "canonical_5y_end_inclusive": "2026-06-30",
        "canonical_5y_end_exclusive": end_exclusive.isoformat(),
        "canonical_result_start_timestamp": EXPECTED_START,
        "canonical_result_end_timestamp_inclusive": EXPECTED_END_INCLUSIVE,
        "number_of_calendar_days": len(expected),
        "effective_years_365_25": len(expected) / 365.25,
        "window_semantics": "[start,end_exclusive)",
        "selection_performance_read_before_freeze": False,
        "frozen_at_utc": datetime.now(timezone.utc).isoformat(),
        "authoritative_sources": [str(p) for p in args.canonical_source],
    }
    freeze_path = args.output_root / "long_horizon_window_freeze.json"
    atomic_json(freeze, freeze_path)
    freeze_hash = sha256(freeze_path)
    (args.output_root / "long_horizon_window_freeze.sha256").write_text(
        f"{freeze_hash}  {freeze_path.name}\n", encoding="utf-8"
    )

    ratios = pilot_ratios(args.pilot_root)
    rows: list[dict[str, object]] = []
    storage_rows: list[dict[str, object]] = []
    for symbol in SYMBOLS:
        for data_type in DATA_TYPES:
            prefix = f"data/futures/um/daily/{data_type}/{symbol}/"
            objects = list_prefix(prefix)
            archives, checksums = parse_objects(symbol, data_type, objects)
            available_dates = sorted(archives)
            covered = sorted(expected_set.intersection(archives))
            missing = sorted(expected_set.difference(archives))
            checksum_missing = sorted(set(covered).difference(checksums))
            compressed = sum(archives[d] for d in covered)
            ratio = ratios.get((symbol, data_type))
            if ratio is None:
                same_type = [v for (s, t), v in ratios.items() if t == data_type]
                ratio = float(pd.Series(same_type).median()) if same_type else float("nan")
            estimated_converted = compressed * ratio if pd.notna(ratio) else float("nan")
            rows.append({
                "symbol": symbol,
                "data_type": data_type,
                "required_start": start.isoformat(),
                "required_end_exclusive": end_exclusive.isoformat(),
                "first_public_archive_date": available_dates[0].isoformat() if available_dates else "",
                "last_public_archive_date": available_dates[-1].isoformat() if available_dates else "",
                "required_days": len(expected),
                "available_required_days": len(covered),
                "missing_days": len(missing),
                "first_missing_date": missing[0].isoformat() if missing else "",
                "last_missing_date": missing[-1].isoformat() if missing else "",
                "checksum_missing_days": len(checksum_missing),
                "compressed_bytes_for_available_required_days": compressed,
                "full_window_complete": len(missing) == 0,
                "source": "Binance Vision USD-M Futures daily archive",
                "listing_prefix": prefix,
                "audit_mode": "S3_METADATA_ONLY_NO_ARCHIVE_DOWNLOAD",
            })
            for year in range(start.year, end_exclusive.year + 1):
                year_dates = [d for d in expected if d.year == year]
                if not year_dates:
                    continue
                year_covered = [d for d in year_dates if d in archives]
                year_bytes = sum(archives[d] for d in year_covered)
                storage_rows.append({
                    "symbol": symbol,
                    "year": year,
                    "data_type": data_type,
                    "required_days": len(year_dates),
                    "archive_days_available": len(year_covered),
                    "missing_days": len(year_dates) - len(year_covered),
                    "compressed_source_bytes": year_bytes,
                    "converted_to_compressed_ratio_estimate": ratio,
                    "estimated_converted_bytes": year_bytes * ratio if pd.notna(ratio) else float("nan"),
                    "estimate_basis": "actual S3 object sizes; conversion ratio from validated March-2024 pilot",
                })

    availability = pd.DataFrame(rows)
    storage = pd.DataFrame(storage_rows)
    atomic_csv(availability, args.output_root / "long_horizon_maker_data_availability.csv")
    atomic_csv(storage, args.output_root / "long_horizon_storage_plan.csv")

    combined = availability.pivot(index="symbol", columns="data_type", values="full_window_complete").fillna(False)
    fully_covered = sorted(combined.index[combined.all(axis=1)].tolist())
    summary = {
        "window_freeze_sha256": freeze_hash,
        "metadata_only": True,
        "archive_downloads_started": 0,
        "symbols_audited": SYMBOLS,
        "symbols_with_full_bookTicker_and_trades_coverage": fully_covered,
        "maker_full_window_symbol_count": len(fully_covered),
        "validation": "PASSED",
    }
    atomic_json(summary, args.output_root / "data_availability_audit_summary.json")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
