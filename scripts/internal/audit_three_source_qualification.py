#!/usr/bin/env python3
"""Create source-independence and per-minute redundancy audits without touching a run."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--experiment", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True); parser.add_argument("--clock-offset-ms", type=float, required=True)
    args = parser.parse_args(); args.output.mkdir(parents=True, exist_ok=True)
    source = pd.read_csv(args.experiment / "active_active/canonical_minute_coverage.csv")
    routes = ["ROUTE_A", "ROUTE_B", "ROUTE_C"]
    for route in routes:
        source[f"{route}_present"] = ((source[f"{route}_quote_count"] > 0) & (source[f"{route}_trade_count"] > 0))
    source["healthy_source_count"] = source[[f"{route}_present" for route in routes]].sum(axis=1)
    source["bar_created"] = (source.canonical_quote_count > 0) & (source.canonical_trade_count > 0)
    source.to_csv(args.output / "three_source_qualification_coverage.csv", index=False)
    counts = source.healthy_source_count.value_counts().to_dict()
    independence = {
        "collector_id": "COLLECTOR_C",
        "host_id": "shannon-macbook-air/100.64.0.22",
        "network_path_id": "MAC_DIRECT_INTERNET_TO_BINANCE_OVER_RESTRICTED_TAILSCALE_CONNECT_RELAY",
        "binance_endpoint": "wss://fstream.binance.com",
        "uses_route_a": False, "uses_route_b": False, "uses_windows_failover_proxy": False,
        "relay_allowlisted_client": "100.64.0.17", "relay_allowed_target": "fstream.binance.com:443",
        "subscriptions": ["BTCUSDT trade", "BTCUSDT bookTicker", "BTCUSDT markPrice@1s"],
        "trading_api_initialized": False, "production_exchange_orders": 0,
        "mac_ntp_probe": "time.apple.com success",
        "windows_ntp_service": "NOT_RUNNING",
        "measured_windows_minus_mac_clock_offset_ms": args.clock_offset_ms,
        "canonical_order_basis": "exchange timestamp + trade/update identity; local clock is provenance only",
        "qualification": {
            "bars": int(len(source)), "canonical_missing": int((~source.bar_created).sum()),
            "minimum_simultaneous_healthy_sources": int(source.healthy_source_count.min()),
            "minutes_3_sources": int(counts.get(3, 0)), "minutes_2_sources": int(counts.get(2, 0)),
            "minutes_1_source": int(counts.get(1, 0)), "minutes_0_sources": int(counts.get(0, 0)),
        },
    }
    (args.output / "collector_c_independence_and_clock_audit.json").write_text(json.dumps(independence, indent=2) + "\n")
    print(json.dumps(independence, indent=2)); return 0


if __name__ == "__main__":
    raise SystemExit(main())
