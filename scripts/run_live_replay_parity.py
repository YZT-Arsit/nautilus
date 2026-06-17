#!/usr/bin/env python3
"""Read-only replay-parity smoke: historical vs live-normalized TradeEvents.

Path A  : local Binance Vision aggTrades parquet -> load_events(hive_parquet_trades)
          -> TradeEvent  (the historical/backtest path)
Path B  : each Path-A TradeEvent -> synthetic Binance aggTrade message
          -> LiveNormalizer -> TradeEvent  (the live-normalization path)

Compares the two field-by-field (source excluded; event_time at millisecond
resolution).  Read-only: no network, no download, no WebSocket, no orders, no
Nautilus.

    python scripts/run_live_replay_parity.py --root historical_data/market_data \\
        --symbol BTCUSDT --date 2026-06-16 [--max-rows 10000]
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from data_engine.loader import load_events  # noqa: E402
from data_engine.live import (  # noqa: E402
    LiveNormalizer,
    compare_trade_events,
    standard_trade_to_agg_message,
)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Replay-parity smoke (read-only)")
    ap.add_argument("--root", default="historical_data/market_data")
    ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument("--venue-type", default="spot")
    ap.add_argument("--exchange", default="BINANCE")
    ap.add_argument("--date", required=True, help="YYYY-MM-DD partition to replay")
    ap.add_argument("--max-rows", type=int, default=None, help="limit to first N trades")
    args = ap.parse_args(argv)

    instrument_id = f"{args.symbol}.BINANCE"
    t0 = time.perf_counter()
    warmup, live = load_events({
        "mode": "hive_parquet_trades",
        "root": args.root,
        "instrument_id": instrument_id,
        "timestamp_column": "ts",
        "timestamp_unit": "ns",
        "filters": {"exchange": args.exchange, "venue_type": args.venue_type,
                    "symbol": args.symbol, "data_type": "aggTrades", "date": args.date},
    })
    events_a = list(live)
    if args.max_rows is not None:
        events_a = events_a[: args.max_rows]
    t_load = time.perf_counter() - t0

    normalizer = LiveNormalizer()
    matched = 0
    mismatched = 0
    sub_ms = 0
    first_mismatch = None
    t1 = time.perf_counter()
    for ea in events_a:
        msg = standard_trade_to_agg_message(
            symbol=args.symbol, event_time_ns=ea.event_time_ns, price=ea.price,
            quantity=ea.quantity, agg_trade_id=ea.trade_id, is_buyer_maker=ea.is_buyer_maker,
        )
        eb = normalizer.normalize(msg)
        if ea.event_time_ns % 1_000_000 != 0:
            sub_ms += 1
        ok, diffs = compare_trade_events(ea, eb, ignore_source=True)
        if ok:
            matched += 1
        else:
            mismatched += 1
            if first_mismatch is None:
                first_mismatch = (ea, eb, diffs)
    elapsed = time.perf_counter() - t1
    n = len(events_a)

    print(f"ROOT: {args.root}")
    print(f"SYMBOL: {args.symbol}  DATE: {args.date}")
    print(f"ROWS_COMPARED: {n}")
    print(f"MATCHED: {matched}")
    print(f"MISMATCH: {mismatched}")
    if first_mismatch is not None:
        ea, eb, diffs = first_mismatch
        print(f"FIRST_MISMATCH: trade_id={ea.trade_id} diffs={diffs}")
    # source difference is expected and was excluded from the comparison
    src_a = events_a[0].source if events_a else None
    print(f"SOURCE_HISTORICAL: {src_a}")
    print("SOURCE_LIVE: binance_ws_aggTrade")
    print("SOURCE_DIFFERENCE_IGNORED: True (expected)")
    print(f"SUBMS_ARCHIVE_PRECISION_ROWS: {sub_ms} "
          f"(event_time compared at ms resolution; live WS T is ms)")
    print(f"LOAD_SECONDS: {t_load:.2f}")
    print(f"COMPARE_SECONDS: {elapsed:.2f}")
    print(f"ROWS_PER_SEC: {n / elapsed:.0f}" if elapsed else "ROWS_PER_SEC: n/a")
    print(f"PARITY_OK: {mismatched == 0 and n > 0}")
    return 0 if (mismatched == 0 and n > 0) else 1


if __name__ == "__main__":
    raise SystemExit(main())
