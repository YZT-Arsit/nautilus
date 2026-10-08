#!/usr/bin/env python3
"""Fetch Binance USD-M aggregate trades for one post-hoc audit minute.

This writes only to a caller-provided scratch directory.  The result is
explicitly diagnostic and is never inserted into the failed forward run.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import ProxyHandler, Request, build_opener

import pandas as pd


LABEL = "POST_HOC_BACKFILL_FOR_SENSITIVITY_ONLY"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--proxy", default="http://100.64.0.5:7890")
    parser.add_argument("--minute", default="2026-10-07T10:53:00Z")
    parser.add_argument("--symbol", default="BTCUSDT")
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    start = pd.Timestamp(args.minute)
    if start.tzinfo is None:
        start = start.tz_localize("UTC")
    start = start.tz_convert("UTC").floor("min")
    end = start + pd.Timedelta(minutes=1)
    start_ms, end_ms = start.value // 1_000_000, end.value // 1_000_000 - 1
    opener = build_opener(ProxyHandler({"http": args.proxy, "https": args.proxy}))
    trades: list[dict] = []
    from_id = None
    while True:
        params = {"symbol": args.symbol, "startTime": start_ms, "endTime": end_ms, "limit": 1000}
        if from_id is not None:
            params["fromId"] = from_id
        request = Request(
            "https://fapi.binance.com/fapi/v1/aggTrades?" + urlencode(params),
            headers={"User-Agent": "nautilus-post-hoc-gap-audit/1.0"},
        )
        with opener.open(request, timeout=30) as response:
            page = json.loads(response.read())
        if not page:
            break
        for row in page:
            timestamp = int(row["T"])
            if start_ms <= timestamp <= end_ms:
                trades.append(row)
        next_id = int(page[-1]["a"]) + 1
        if len(page) < 1000 or next_id == from_id or int(page[-1]["T"]) > end_ms:
            break
        from_id = next_id
    unique = {int(row["a"]): row for row in trades}
    ordered = [unique[key] for key in sorted(unique)]
    raw_path = output / "BTCUSDT_aggTrades_20261007_1053Z.json"
    raw_path.write_text(json.dumps(ordered, separators=(",", ":")) + "\n", encoding="utf-8")
    summary = {
        "evidence_label": LABEL,
        "provider": "Binance USD-M Futures REST",
        "endpoint": "/fapi/v1/aggTrades",
        "symbol": args.symbol,
        "minute_utc": start.isoformat(),
        "minute_china": start.tz_convert("Asia/Shanghai").isoformat(),
        "aggregate_trade_count": len(ordered),
        "first_trade_id": int(ordered[0]["a"]) if ordered else None,
        "last_trade_id": int(ordered[-1]["a"]) if ordered else None,
        "first_trade_timestamp_ms": int(ordered[0]["T"]) if ordered else None,
        "last_trade_timestamp_ms": int(ordered[-1]["T"]) if ordered else None,
        "sha256": hashlib.sha256(raw_path.read_bytes()).hexdigest(),
        "source_experiment_modified": False,
        "production_exchange_orders": 0,
    }
    (output / "missing_minute_source_summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0 if ordered else 2


if __name__ == "__main__":
    raise SystemExit(main())
