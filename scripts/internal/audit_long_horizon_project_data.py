#!/usr/bin/env python3
"""Audit project-local canonical partitions for the frozen five-year window."""

from __future__ import annotations

import argparse
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq


SYMBOLS = ["XRPUSDT", "DOGEUSDT", "SUIUSDT", "BNBUSDT", "ETHUSDT", "BTCUSDT", "1000PEPEUSDT", "SOLUSDT", "ADAUSDT"]
SPECS = [("bar", "1m"), ("funding_rate", "settlement"), ("trade", "tick")]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--market-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    start, end = date(2021, 7, 1), date(2026, 7, 1)
    required = {(start + timedelta(days=i)).isoformat() for i in range((end - start).days)}
    rows = []
    for symbol in SYMBOLS:
        for data_type, frequency in SPECS:
            root = (
                args.market_root / "asset_class=crypto" / "exchange=BINANCE"
                / "venue_type=futures_um" / f"symbol={symbol}"
                / f"data_type={data_type}" / f"freq={frequency}"
            )
            parts = {p.name.removeprefix("date="): p for p in root.glob("date=*") if p.is_dir()}
            covered = sorted(required.intersection(parts))
            missing = sorted(required.difference(parts))
            files = [f for d in covered for f in parts[d].glob("*.parquet")]
            bytes_total = sum(f.stat().st_size for f in files)
            row_count = sum(pq.ParquetFile(f).metadata.num_rows for f in files)
            rows.append({
                "symbol": symbol, "data_type": data_type, "frequency": frequency,
                "required_start": start.isoformat(), "required_end_exclusive": end.isoformat(),
                "required_days": len(required), "available_required_days": len(covered),
                "missing_days": len(missing), "first_available_date": min(parts) if parts else "",
                "last_available_date": max(parts) if parts else "",
                "first_missing_date": missing[0] if missing else "",
                "last_missing_date": missing[-1] if missing else "",
                "parquet_files": len(files), "row_count": row_count, "bytes": bytes_total,
                "full_window_complete": not missing,
            })
    args.output.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(args.output, index=False)


if __name__ == "__main__":
    main()
