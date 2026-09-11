#!/usr/bin/env python3
"""Audit official March-2024 Binance L1/trade archives before maker execution."""

from __future__ import annotations

import concurrent.futures
import json
import os
import urllib.error
import urllib.request
from datetime import date, timedelta
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "outputs/baseline_evaluation/execution_method_and_reverse_review/data_provenance"
SYMBOLS = (
    "XRPUSDT", "DOGEUSDT", "SUIUSDT", "BNBUSDT", "ETHUSDT",
    "BTCUSDT", "1000PEPEUSDT", "SOLUSDT", "ADAUSDT",
)
START = date(2024, 3, 1)
END = date(2024, 3, 31)


def head(url: str) -> tuple[bool, int]:
    request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "nautilus-execution-review/1"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status == 200, int(response.headers.get("Content-Length") or 0)
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError):
        return False, 0


def audit(item: tuple[str, str, str]) -> dict:
    symbol, day, data_type = item
    archive = f"{symbol}-{data_type}-{day}.zip"
    url = f"https://data.binance.vision/data/futures/um/daily/{data_type}/{symbol}/{archive}"
    exists, size = head(url)
    checksum_exists, _ = head(url + ".CHECKSUM")
    return {
        "symbol": symbol,
        "date": day,
        "source_provider": "Binance official historical data",
        "source_dataset": f"USD-M Futures {data_type}",
        "source_archive": archive,
        "source_url": url,
        "source_type": "L1_BBO" if data_type == "bookTicker" else "RAW_TRADES",
        "archive_exists": exists,
        "checksum_exists": checksum_exists,
        "compressed_bytes": size,
        "status": "AVAILABLE" if exists and checksum_exists else "MISSING",
    }


def main() -> None:
    days = []
    current = START
    while current < END:
        days.append(current.isoformat())
        current += timedelta(days=1)
    tasks = [(symbol, day, data_type) for symbol in SYMBOLS for day in days for data_type in ("bookTicker", "trades")]
    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
        rows = list(pool.map(audit, tasks))
    frame = pd.DataFrame(rows).sort_values(["symbol", "date", "source_type"])
    OUTPUT.mkdir(parents=True, exist_ok=True)
    temporary = OUTPUT / "maker_archive_availability.csv.tmp"
    frame.to_csv(temporary, index=False)
    os.replace(temporary, OUTPUT / "maker_archive_availability.csv")
    status = frame.groupby(["symbol", "source_type"]).status.apply(lambda value: bool(value.eq("AVAILABLE").all())).unstack()
    summary = {
        "status": "PASSED" if bool(frame.status.eq("AVAILABLE").all()) else "PARTIAL",
        "requests": len(frame),
        "complete_symbols": [symbol for symbol in SYMBOLS if bool(status.loc[symbol].all())],
        "incomplete_symbols": [symbol for symbol in SYMBOLS if not bool(status.loc[symbol].all())],
        "estimated_compressed_bytes": int(frame.compressed_bytes.sum()),
    }
    (OUTPUT / "maker_archive_availability_summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
