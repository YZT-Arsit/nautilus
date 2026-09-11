#!/usr/bin/env python3
"""Create complete source/archive provenance for the frozen March maker data."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import time
import urllib.request
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
WORK = Path("outputs/baseline_evaluation/execution_method_and_reverse_review")
PILOT = Path("outputs/baseline_evaluation/maker_execution_research/l1_pilot")


def checksum(url: str) -> str:
    last = None
    for attempt in range(5):
        try:
            request = urllib.request.Request(url + ".CHECKSUM", headers={"User-Agent":"nautilus-review/1"})
            with urllib.request.urlopen(request, timeout=60) as response:
                return response.read().decode("utf-8").split()[0].lower()
        except Exception as error:
            last = error
            time.sleep(2**attempt)
    raise last  # type: ignore[misc]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=ROOT)
    parser.add_argument("--work", type=Path)
    args = parser.parse_args()
    repo = args.repo.resolve()
    work = (args.work or repo / WORK).resolve()
    roots = (repo / PILOT, work / "maker_data")
    manifests = {}
    for root in roots:
        for path in root.glob("ingest_manifest_*.csv"):
            for row in pd.read_csv(path).to_dict("records"):
                manifests[(row["symbol"], row["date"], row["data_type"])] = row
    availability = pd.read_csv(work / "data_provenance/maker_archive_availability.csv")
    if len(availability) != 540:
        raise ValueError(f"availability audit rows={len(availability)}, expected 540")
    urls = availability.source_url.tolist()
    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
        source_checksums = list(pool.map(checksum, urls))
    rows = []
    for available, source_sha in zip(availability.itertuples(index=False), source_checksums, strict=True):
        data_type = "bookTicker" if available.source_type == "L1_BBO" else "trades"
        ingest = manifests.get((available.symbol, available.date, data_type))
        valid_checksum = str(ingest["checksum_valid"]).lower() == "true" if ingest is not None else False
        if ingest is None or ingest["status"] != "PASSED" or not valid_checksum:
            raise ValueError(f"missing validated ingest: {available.symbol} {available.date} {data_type}")
        rows.append(
            {
                "symbol": available.symbol,
                "date": available.date,
                "source_provider": "Binance official historical data",
                "source_dataset": "USD-M Futures bookTicker" if data_type == "bookTicker" else "USD-M Futures trades",
                "source_archive": available.source_archive,
                "source_url": available.source_url,
                "source_type": "L1_BBO" if data_type == "bookTicker" else "RAW_TRADES",
                "checksum": source_sha,
                "checksum_valid": True,
                "first_timestamp": ingest["first_timestamp"],
                "last_timestamp": ingest["last_timestamp"],
                "rows": ingest["rows"],
                "converted_path": ingest["converted_path"],
                "converted_sha256": ingest["converted_sha256"],
                "Nautilus_type": "QuoteTick" if data_type == "bookTicker" else "TradeTick",
                "validation_status": ingest["status"],
            }
        )
    result = pd.DataFrame(rows).sort_values(["symbol", "date", "source_type"])
    result.to_csv(work / "data_provenance/maker_market_data_source.csv", index=False)
    summary = {
        "status":"PASSED", "rows":len(result), "symbols":result.symbol.nunique(),
        "dates":result.date.nunique(), "checksum_valid":int(result.checksum_valid.sum()),
    }
    (work / "data_provenance/maker_market_data_source_summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
