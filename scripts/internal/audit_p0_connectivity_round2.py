#!/usr/bin/env python3
"""Reconstruct the failed P0 smoke connectivity evidence without rerunning it."""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


def iso(ns: int | None) -> str:
    if not ns:
        return ""
    return datetime.fromtimestamp(ns / 1e9, tz=timezone.utc).isoformat()


def load_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    values = sorted(values)
    index = (len(values) - 1) * q
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return values[lower]
    return values[lower] * (upper - index) + values[upper] * (index - lower)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke-root", type=Path, required=True)
    parser.add_argument("--proxy-log", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    worker = args.smoke_root / "workers/BTCUSDT"
    lifecycle_path = worker / "connectivity_state_timeline.csv"
    events_path = worker / "market_data/symbol=BTCUSDT/date=2026-10-04/events.jsonl"
    lifecycle = list(csv.DictReader(lifecycle_path.open(encoding="utf-8-sig")))
    proxy_rows = load_jsonl(args.proxy_log)

    worker_ready_ns = [
        int(datetime.fromisoformat(row["timestamp"]).timestamp() * 1e9)
        for row in lifecycle if row["stage"] == "RESUBSCRIBE_READY" and row["result"] == "SUCCESS"
    ]
    market_events: list[tuple[int, str]] = []
    with events_path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            payload = row.get("payload", {})
            kind = payload.get("event_type")
            if kind in {"quote", "trade"}:
                market_events.append((int(row.get("ts_receive") or payload["receive_time_ns"]), kind))

    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in proxy_rows:
        if row.get("connection_id"):
            grouped[str(row["connection_id"])].append(row)
    connection_rows: list[dict] = []
    for connection_id, group in grouped.items():
        group.sort(key=lambda value: int(value.get("time_ns", 0)))
        attempts = [row for row in group if row.get("event") == "CONNECT_ATTEMPT"]
        if not attempts:
            continue
        connected = next((row for row in group if row.get("event") == "UPSTREAM_CONNECTED"), None)
        ended = next((row for row in reversed(group) if row.get("event") == "RELAY_ENDED"), None)
        failed = next((row for row in reversed(group) if row.get("event") in {"RELAY_FAILED", "UPSTREAM_FAILED"}), None)
        start_ns = int(attempts[0]["time_ns"])
        end_ns = int((ended or failed or group[-1])["time_ns"])
        data = [(stamp, kind) for stamp, kind in market_events if start_ns <= stamp <= end_ns]
        quotes = [stamp for stamp, kind in data if kind == "quote"]
        trades = [stamp for stamp, kind in data if kind == "trade"]
        ready = any(start_ns <= stamp <= end_ns for stamp in worker_ready_ns)
        route = str((connected or attempts[-1]).get("upstream", ""))
        reason = str((ended or {}).get("end_reason") or (failed or {}).get("event") or "")
        error = str((failed or {}).get("error", ""))
        connection_rows.append({
            "connection_id": connection_id,
            "start_timestamp": iso(start_ns), "end_timestamp": iso(end_ns),
            "route_id": route, "proxy_endpoint": route,
            "TCP_connect_result": "SUCCESS" if connected else "FAILED",
            "CONNECT_result": "SUCCESS" if connected else "FAILED",
            "TLS_handshake_result": "SUCCESS" if ready else "UNVERIFIED_OR_FAILED",
            "websocket_upgrade_result": "SUCCESS" if ready else "UNVERIFIED_OR_FAILED",
            "subscription_ack": "SUCCESS" if ready else "NO",
            "first_quote_timestamp": iso(min(quotes) if quotes else None),
            "first_trade_timestamp": iso(min(trades) if trades else None),
            "last_quote_timestamp": iso(max(quotes) if quotes else None),
            "last_trade_timestamp": iso(max(trades) if trades else None),
            "connection_lifetime_seconds": (end_ns - start_ns) / 1e9,
            "termination_reason": reason,
            "exception_class": error.split("(", 1)[0] if error else "",
            "exception_message": error,
            "market_data_received": bool(quotes and trades),
        })
    fields = [
        "connection_id", "start_timestamp", "end_timestamp", "route_id", "proxy_endpoint",
        "TCP_connect_result", "CONNECT_result", "TLS_handshake_result",
        "websocket_upgrade_result", "subscription_ack", "first_quote_timestamp",
        "first_trade_timestamp", "last_quote_timestamp", "last_trade_timestamp",
        "connection_lifetime_seconds", "termination_reason", "exception_class",
        "exception_message", "market_data_received",
    ]
    write_csv(args.output / "connection_attempts.csv", connection_rows, fields)

    route_rows: list[dict] = []
    for route in sorted({str(row["route_id"]) for row in connection_rows if row["route_id"]}):
        subset = [row for row in connection_rows if row["route_id"] == route]
        lifetimes = [float(row["connection_lifetime_seconds"]) for row in subset]
        route_rows.append({
            "route_id": route, "attempts": len(subset),
            "successful_TLS": sum(row["TLS_handshake_result"] == "SUCCESS" for row in subset),
            "successful_websocket": sum(row["websocket_upgrade_result"] == "SUCCESS" for row in subset),
            "successful_subscription": sum(row["subscription_ack"] == "SUCCESS" for row in subset),
            "successful_market_data": sum(bool(row["market_data_received"]) for row in subset),
            "median_connection_lifetime": percentile(lifetimes, 0.5),
            "failed_before_first_data": sum(not row["market_data_received"] for row in subset),
            "failed_after_data": sum(bool(row["market_data_received"]) and row["termination_reason"] != "" for row in subset),
        })
    write_csv(args.output / "route_selection_summary.csv", route_rows, [
        "route_id", "attempts", "successful_TLS", "successful_websocket",
        "successful_subscription", "successful_market_data", "median_connection_lifetime",
        "failed_before_first_data", "failed_after_data",
    ])

    cb_events = {
        "ROUTE_PENALIZED", "ROUTE_RECOVERED", "TRANSPORT_ROUTE_RECOVERED",
        "ROUTES_COOLING_DOWN", "DEGRADED_WAIT", "CONNECT_ATTEMPT",
    }
    cb_rows = [{
        "timestamp": iso(int(row["time_ns"])), "connection_id": row.get("connection_id", ""),
        "event": row.get("event", ""), "route_id": row.get("upstream", ""),
        "reason": row.get("reason", ""), "cooldown_seconds": row.get("cooldown_seconds", ""),
        "wait_seconds": row.get("wait_seconds", ""), "half_open": row.get("half_open", ""),
        "route_states": json.dumps(row.get("route_states", {}), sort_keys=True),
    } for row in proxy_rows if row.get("event") in cb_events]
    write_csv(args.output / "circuit_breaker_timeline.csv", cb_rows, [
        "timestamp", "connection_id", "event", "route_id", "reason",
        "cooldown_seconds", "wait_seconds", "half_open", "route_states",
    ])

    summary = {
        "status": "AUDITED",
        "smoke_root": str(args.smoke_root),
        "proxy_log": str(args.proxy_log),
        "connection_attempts": len(connection_rows),
        "routes": len(route_rows),
        "market_data_healthy_connections": sum(bool(row["market_data_received"]) for row in connection_rows),
        "analysis_note": "TLS/WebSocket success requires correlated RESUBSCRIBE_READY plus fresh quote and trade",
    }
    (args.output / "round2_offline_audit.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
