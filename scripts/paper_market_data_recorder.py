#!/usr/bin/env python3
"""
Bounded, public USD-M market-data recorder for paper-trading preparation.

This command has no credential, account, or order API surface.  It records
normalized public ``aggTrade`` and ``bookTicker`` events into replayable UTC
partitions.  Both runtime bounds are mandatory so invoking it can never start
an unbounded forward experiment accidentally.

Creating this tool does not authorize starting P2.  The release preflight and
human review remain separate gates.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data_engine.live.binance_ws_client import BinancePublicWebSocketSource  # noqa: E402
from strategy_framework.paper_trading import AppendOnlyMarketDataRecorder  # noqa: E402


USD_M_PUBLIC_WS = "wss://fstream.binance.com"


def main() -> int:
    parser = argparse.ArgumentParser(description="Bounded Binance USD-M public market-data recorder")
    parser.add_argument("--symbol", required=True, help="USD-M symbol, e.g. BTCUSDT")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-messages", type=int, required=True)
    parser.add_argument("--timeout-seconds", type=float, required=True)
    args = parser.parse_args()
    if args.max_messages <= 0 or args.timeout_seconds <= 0:
        parser.error("--max-messages and --timeout-seconds must both be positive")

    symbol = args.symbol.upper()
    source = BinancePublicWebSocketSource(
        symbol,
        ("aggTrade", "bookTicker"),
        base_url=USD_M_PUBLIC_WS,
        instrument_id=f"{symbol}.BINANCE",
    )
    recorder = AppendOnlyMarketDataRecorder(args.output, "BINANCE_USDM_PUBLIC_WEBSOCKET")
    observed = 0
    for event in source.iter_events(
        max_messages=args.max_messages,
        timeout_seconds=args.timeout_seconds,
    ):
        observed += int(recorder.append(event))
    summary = {
        "market": "USD_M_FUTURES",
        "symbol": symbol,
        "live_order_submission": "DISABLED",
        "events_appended": observed,
        "partitions": recorder.manifest(),
    }
    print(json.dumps(summary, indent=2))
    return 0 if observed else 2


if __name__ == "__main__":
    raise SystemExit(main())
