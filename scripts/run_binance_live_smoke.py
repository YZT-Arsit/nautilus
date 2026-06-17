#!/usr/bin/env python3
"""Binance **public** market-data WebSocket smoke (read-only, bounded).

Connects to Binance's public combined stream, reads a bounded number of messages
(by ``--max-messages`` and ``--timeout-seconds``), normalizes each via the
existing LiveNormalizer, prints summaries, and disconnects.

Market-data ONLY — no API key, no account, no orders, no Nautilus, no parquet/
manifest writes, no downloads.  Needs the optional ``websocket-client`` package
for the real socket; if missing it exits with a clear message (install is gated).

    python scripts/run_binance_live_smoke.py --symbol BTCUSDT \\
        --streams aggTrade,bookTicker --max-messages 20 --timeout-seconds 20
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from data_engine.live.binance_ws_client import BinancePublicWebSocketSource  # noqa: E402


def _summ_trade(ev) -> str:
    return ("price=%s qty=%s side=%s id=%s ts=%s" %
            (ev.price, ev.quantity, ev.side, ev.trade_id, ev.event_time_ns))


def _summ_quote(ev) -> str:
    return ("bid=%s/%s ask=%s/%s mid=%s update_id=%s ts=%s" %
            (ev.bid_price, ev.bid_size, ev.ask_price, ev.ask_size,
             ev.mid_price, ev.update_id, ev.event_time_ns))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Binance public market-data WS smoke (read-only)")
    ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument("--streams", default="aggTrade,bookTicker",
                    help="comma list, e.g. aggTrade,bookTicker")
    ap.add_argument("--max-messages", type=int, default=20)
    ap.add_argument("--timeout-seconds", type=float, default=20.0)
    ap.add_argument("--instrument-id", default=None)
    args = ap.parse_args(argv)

    streams = [s.strip() for s in args.streams.split(",") if s.strip()]
    source = BinancePublicWebSocketSource(args.symbol, streams, instrument_id=args.instrument_id)
    print(f"CONNECTING: {source.url}")
    try:
        res = source.run_until(max_messages=args.max_messages, timeout_seconds=args.timeout_seconds)
    except RuntimeError as exc:           # e.g. websocket-client not installed (gated)
        print(f"SMOKE_ERROR: {exc}", file=sys.stderr)
        return 2

    print(f"CONNECTED_STREAM: {res.connected_url}")
    print(f"RAW_MESSAGES_RECEIVED: {res.raw_received}")
    print(f"NORMALIZED_EVENTS: {len(res.events)}")
    print(f"TRADE_COUNT: {res.trade_count}")
    print(f"QUOTE_COUNT: {res.quote_count}")
    print(f"DROPPED_UNKNOWN: {res.dropped_count}")
    print("FIRST_TRADE: " + (_summ_trade(res.first_trade) if res.first_trade else "none"))
    print("FIRST_QUOTE: " + (_summ_quote(res.first_quote) if res.first_quote else "none"))
    print(f"DISCONNECT_REASON: {res.disconnect_reason}")
    print(f"ELAPSED_SECONDS: {res.elapsed_seconds:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
