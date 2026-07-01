#!/usr/bin/env python3
"""Bounded Binance public market-data WebSocket smoke test.

Connects to Binance's public combined stream (aggTrade + bookTicker) for one
symbol, reads a bounded number of messages (by count AND wall-clock timeout),
normalizes each into our canonical TradeEvent/QuoteEvent, then disconnects.

Market-data ONLY: no credentials, no signing, no orders. The optional
``websocket-client`` package must already be installed (installation is gated,
never automatic).

Run on the server (where the network egress is)::

    uv run python scripts/binance_live_smoke.py --symbol btcusdt --messages 5 --timeout 15

Exit code 0 = at least one event received; 2 = connected but no data; 3 = could
not connect / dependency missing.
"""
from __future__ import annotations

import argparse
import sys

from data_engine.live.binance_ws_client import BinancePublicWebSocketSource


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Binance public WS market-data smoke")
    p.add_argument("--symbol", default="btcusdt")
    p.add_argument("--streams", default="aggTrade,bookTicker",
                   help="comma-separated stream names")
    p.add_argument("--messages", type=int, default=5, help="max messages to read")
    p.add_argument("--timeout", type=float, default=15.0, help="wall-clock seconds")
    p.add_argument("--base-url", default="wss://stream.binance.com:9443",
                   help="WS host; use wss://data-stream.binance.vision:9443 where "
                        "stream.binance.com is geo-blocked (market-data-only mirror)")
    args = p.parse_args(argv)

    src = BinancePublicWebSocketSource(args.symbol, args.streams, base_url=args.base_url)
    print(f"[binance-smoke] connecting: {src.url}")
    try:
        res = src.run_until(max_messages=args.messages, timeout_seconds=args.timeout)
    except Exception as exc:  # dependency missing / connection refused / DNS / firewall
        print(f"[binance-smoke] FAILED to connect: {type(exc).__name__}: {exc}")
        return 3

    print(f"[binance-smoke] disconnect_reason={res.disconnect_reason} "
          f"elapsed={res.elapsed_seconds:.2f}s raw={res.raw_received} "
          f"trades={res.trade_count} quotes={res.quote_count} dropped={res.dropped_count}")

    if res.first_trade is not None:
        t = res.first_trade
        # trade timestamp + volume are preserved end-to-end
        print(f"[binance-smoke] first trade: instrument={t.instrument_id} "
              f"price={t.price} qty={t.quantity} side={getattr(t, 'side', None)} "
              f"event_time_ns={t.event_time_ns} recv_ns={getattr(t, 'receive_time_ns', None)}")
    if res.first_quote is not None:
        q = res.first_quote
        print(f"[binance-smoke] first quote: instrument={q.instrument_id} "
              f"bid={q.bid_price}x{q.bid_size} ask={q.ask_price}x{q.ask_size} "
              f"event_time_ns={q.event_time_ns}")

    if res.trade_count == 0 and res.quote_count == 0:
        print("[binance-smoke] connected but received no usable events")
        return 2
    print("[binance-smoke] OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
