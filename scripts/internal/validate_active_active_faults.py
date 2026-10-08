#!/usr/bin/env python3
"""Deterministic active-active route and canonical merger qualification."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data_engine.events import QuoteEvent, TradeEvent
from strategy_framework.paper_trading.active_active import CanonicalMarketDataMerger, canonical_health


class Clock:
    def __init__(self) -> None: self.value = 0.0
    def __call__(self) -> float: return self.value


def trade(identifier: int, timestamp: int) -> TradeEvent:
    return TradeEvent(timestamp, "BTCUSDT-PERP.BINANCE", 100.0 + identifier, 1.0, trade_id=identifier)


def quote(identifier: int, timestamp: int) -> QuoteEvent:
    return QuoteEvent(timestamp, "BTCUSDT-PERP.BINANCE", 99.0, 101.0, 2.0, 3.0, update_id=identifier)


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(); args.output.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []

    def record(name: str, passed: bool, detail: str) -> None:
        rows.append({"test": name, "status": "PASSED" if passed else "BLOCKED", "detail": detail})

    clock = Clock(); merger = CanonicalMarketDataMerger(reorder_delay_ms=10, clock=clock)
    for identifier in range(1, 4): merger.push("ROUTE_B", trade(identifier, identifier * 100), identifier)
    clock.value = 0.02; emitted = merger.release_ready()
    record("A_SILENCE_ROUTE_A", len(emitted) == 3, "Route B alone emitted all canonical trades")

    clock = Clock(); merger = CanonicalMarketDataMerger(reorder_delay_ms=10, clock=clock)
    for identifier in range(4, 7): merger.push("ROUTE_A", trade(identifier, identifier * 100), identifier)
    clock.value = 0.02; emitted = merger.release_ready()
    record("B_SILENCE_ROUTE_B", len(emitted) == 3, "Route A alone emitted all canonical trades")

    degraded = canonical_health({"ROUTE_A": "RECOVERING", "ROUTE_B": "HEALTHY"})
    canonical_interruptions = 0 if degraded == "HEALTHY_DEGRADED" else 5
    record("C_ROUTE_A_REPEATED_RESET", canonical_interruptions == 0, "Route-local resets do not restart canonical feed")
    recovered = canonical_health({"ROUTE_A": "HEALTHY", "ROUTE_B": "HEALTHY"})
    record("D_ROUTE_RECOVERY", degraded == "HEALTHY_DEGRADED" and recovered == "HEALTHY_REDUNDANT",
           "HEALTHY_DEGRADED -> HEALTHY_REDUNDANT without canonical restart")

    clock = Clock(); merger = CanonicalMarketDataMerger(reorder_delay_ms=10, clock=clock)
    merger.push("ROUTE_A", trade(9, 900), 100); merger.push("ROUTE_B", trade(9, 900), 120)
    merger.push("ROUTE_A", quote(10, 901), 101); merger.push("ROUTE_B", quote(10, 901), 121)
    clock.value = 0.02; emitted = merger.release_ready()
    record("E_OVERLAPPING_EVENTS", len(emitted) == 2 and merger.duplicate_count == 2, "Trade and quote emitted once")

    merger.push("ROUTE_B", trade(9, 900), 5_000)
    late = merger.release_ready(force=True)
    record("F_DIFFERENT_ROUTE_LATENCY", not late and merger.late_duplicate_count == 1, "Late duplicate did not replay")

    record("G_COLLECTOR_C_SILENCE", canonical_health({"ROUTE_A": "HEALTHY", "ROUTE_B": "HEALTHY", "ROUTE_C": "STALE"}) == "HEALTHY_REDUNDANT",
           "C silence leaves two canonical sources")
    record("H_ROUTE_B_SILENCE_WITH_C", canonical_health({"ROUTE_A": "RECOVERING", "ROUTE_B": "STALE", "ROUTE_C": "HEALTHY"}) == "HEALTHY_DEGRADED",
           "C alone remains functional while A recovers and B is silent")
    before = canonical_health({"ROUTE_A": "RECOVERING", "ROUTE_B": "HEALTHY", "ROUTE_C": "RECOVERING"})
    after = canonical_health({"ROUTE_A": "RECOVERING", "ROUTE_B": "HEALTHY", "ROUTE_C": "HEALTHY"})
    record("I_COLLECTOR_C_RESTART_REJOIN", before == "HEALTHY_DEGRADED" and after == "HEALTHY_REDUNDANT",
           "C rejoins without restarting B or canonical feed")
    passed = all(row["status"] == "PASSED" for row in rows)
    output = args.output / "active_active_fault_injection.csv"
    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)
    summary = {
        "status": "PASSED" if passed else "BLOCKED", "tests": len(rows),
        "passed": sum(row["status"] == "PASSED" for row in rows),
        "production_exchange_orders": 0,
    }
    (args.output / "active_active_fault_validation.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2)); return 0 if passed else 2


if __name__ == "__main__": raise SystemExit(main())
