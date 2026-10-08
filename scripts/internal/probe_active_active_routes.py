#!/usr/bin/env python3
"""Bounded simultaneous public-market route probe; never initializes trading."""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data_engine.live.binance_ws_client import BinancePublicWebSocketSource, proxy_transport_factory


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seconds", type=float, default=30.0); args = parser.parse_args()
    routes = {"ROUTE_A": ("100.64.0.6", 7890), "ROUTE_B": ("100.64.0.5", 7890),
              "ROUTE_C": ("100.64.0.22", 7891)}
    results: dict[str, dict] = {}; lock = threading.Lock()

    def probe(route: str, endpoint: tuple[str, int]) -> None:
        counts = {"trade": 0, "quote": 0, "funding_rate": 0}; error = None
        started = time.monotonic()
        try:
            source = BinancePublicWebSocketSource(
                "BTCUSDT", ("trade", "bookTicker", "markPrice@1s"),
                base_url="wss://fstream.binance.com", instrument_id="BTCUSDT-PERP.BINANCE",
                transport_factory=proxy_transport_factory(*endpoint),
            )
            for event in source.iter_events(max_messages=100_000, timeout_seconds=args.seconds,
                                            receive_timeout_seconds=5.0):
                counts[event.event_type] += 1
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
        with lock:
            results[route] = {
                "route": route, "endpoint": f"{endpoint[0]}:{endpoint[1]}", **counts,
                "elapsed_seconds": time.monotonic() - started, "error": error,
                "status": "PASSED" if counts["trade"] > 0 and counts["quote"] > 0 else "BLOCKED",
            }

    threads = [threading.Thread(target=probe, args=item, daemon=True) for item in routes.items()]
    for thread in threads: thread.start()
    for thread in threads: thread.join(args.seconds + 15)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    summary = {"routes": results, "production_market_data": "READ_ONLY",
               "production_exchange_orders": 0,
               "status": "PASSED" if sum(x["status"] == "PASSED" for x in results.values()) >= 2 else "BLOCKED"}
    args.output.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0 if summary["status"] == "PASSED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
