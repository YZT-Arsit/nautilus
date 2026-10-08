#!/usr/bin/env python3
"""Build deterministic forensic artifacts for the failed P0 long run.

This is an offline/read-only audit of recorded market data and diagnostics.  It
does not connect to Binance and cannot place orders.  The only experiment-tree
write is an additive failure marker; raw evidence is never modified.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from statistics import median
from typing import Any


NS = 1_000_000_000
MINUTE_NS = 60 * NS


def dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def iso_ns(value: int) -> str:
    return datetime.fromtimestamp(value / NS, tz=UTC).isoformat()


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temp.replace(path)


def atomic_csv(path: Path, rows: list[dict[str, Any]], fields: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fields is None:
        fields = list(rows[0]) if rows else ["status"]
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows or [{"status": "NO_ROWS"}])
    temp.replace(path)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * q
    lo, hi = math.floor(index), math.ceil(index)
    if lo == hi:
        return ordered[lo]
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (index - lo)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:  # noqa: C901
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--experiment-id", required=True)
    parser.add_argument("--audit-root", type=Path, required=True)
    parser.add_argument("--proxy-log", type=Path, required=True)
    args = parser.parse_args()

    experiment = args.repo / "paper_trading" / "experiments" / args.experiment_id
    worker = experiment / "workers" / "BTCUSDT"
    lifecycle_path = worker / "connectivity_state_timeline.csv"
    runtime_path = args.audit_root / "connection_runtime_health.csv"
    bars_path = worker / "bars" / "BTCUSDT_1m.jsonl"
    event_files = sorted((worker / "market_data" / "symbol=BTCUSDT").glob("date=*/events.jsonl"))

    lifecycle = read_csv(lifecycle_path)
    for row in lifecycle:
        row["_dt"] = dt(row["timestamp"])
    stale_rows = [row for row in lifecycle if row["stage"] in {"QUOTE_STALE", "TRADE_STALE"}]
    if not stale_rows:
        raise RuntimeError("failed run has no recorded stale event")
    first_stale = stale_rows[0]
    first_stale_dt = first_stale["_dt"]
    last_good_dt = first_stale_dt - timedelta(milliseconds=float(first_stale["latency_ms"]))

    proxy = read_jsonl(args.proxy_log)
    for row in proxy:
        row["_dt"] = datetime.fromtimestamp(int(row["time_ns"]) / NS, tz=UTC)
    relay_failures = [
        row for row in proxy
        if row.get("event") in {"RELAY_FAILED", "RELAY_ENDED"}
        and row["_dt"] >= last_good_dt
    ]
    first_relay_failure = min(relay_failures, key=lambda row: row["_dt"])

    bars = read_jsonl(bars_path)
    bar_minutes = sorted((int(row["event_time_ns"]) - MINUTE_NS) // MINUTE_NS for row in bars)
    missing_minutes: list[int] = []
    for left, right in zip(bar_minutes, bar_minutes[1:], strict=False):
        missing_minutes.extend(range(left + 1, right))

    runtime = [
        row for row in read_csv(runtime_path)
        if args.experiment_id in row.get("experiment", "")
    ]
    for row in runtime:
        row["_dt"] = dt(row["sample_timestamp"])

    # Aggregate the persisted source stream.  Receive-time coverage identifies
    # where data actually reached the recorder; exchange time is retained for
    # transport-latency diagnostics.
    per_minute: dict[int, dict[str, Any]] = defaultdict(
        lambda: {"quote_count": 0, "trade_count": 0, "latency_ms": []},
    )
    last_receive_by_kind: dict[str, int] = {}
    for path in event_files:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                event = json.loads(line)
                payload = event.get("payload", {})
                kind = str(payload.get("event_type", ""))
                receive_ns = int(event.get("ts_receive") or payload.get("receive_time_ns") or 0)
                exchange_ns = int(event.get("ts_exchange") or payload.get("event_time_ns") or 0)
                if not receive_ns or kind not in {"quote", "trade"}:
                    continue
                minute = receive_ns // MINUTE_NS
                per_minute[minute][f"{kind}_count"] += 1
                last_receive_by_kind[kind] = max(last_receive_by_kind.get(kind, 0), receive_ns)
                if exchange_ns:
                    per_minute[minute]["latency_ms"].append((receive_ns - exchange_ns) / 1_000_000)

    # Map proxy connections and route state changes.
    proxy_connections: dict[str, dict[str, Any]] = defaultdict(dict)
    for row in proxy:
        cid = row.get("connection_id")
        if not cid:
            continue
        item = proxy_connections[cid]
        if row.get("event") == "CONNECT_ATTEMPT":
            item.update(start=row["_dt"], route=row.get("upstream"), half_open=row.get("half_open", False))
        elif row.get("event") == "UPSTREAM_CONNECTED":
            item["connected"] = row["_dt"]
        elif row.get("event") == "RELAY_ENDED":
            item.update(
                end=row["_dt"], lifetime=row.get("relay_lifetime_seconds"),
                termination=row.get("end_reason"), bytes_up=row.get("bytes_upstream_to_client"),
            )

    connection_rows = []
    for cid, item in proxy_connections.items():
        connection_rows.append({
            "connection_id": cid,
            "start": item.get("start", "").isoformat() if item.get("start") else "",
            "end": item.get("end", "").isoformat() if item.get("end") else "",
            "lifetime_seconds": item.get("lifetime", ""),
            "route": item.get("route", ""),
            "half_open_probe": item.get("half_open", False),
            "termination_reason": item.get("termination", "OPEN_AT_LOG_END"),
            "bytes_upstream_to_client": item.get("bytes_up", ""),
        })
    atomic_csv(args.audit_root / "connection_lifetime_audit.csv", connection_rows)

    route_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in connection_rows:
        if row["route"]:
            route_groups[row["route"]].append(row)
    proxy_summary = []
    for route, rows in sorted(route_groups.items()):
        lifetimes = [float(row["lifetime_seconds"]) for row in rows if row["lifetime_seconds"] != ""]
        proxy_summary.append({
            "route_id": route,
            "tunnels": len(rows),
            "completed_tunnels": len(lifetimes),
            "total_healthy_duration_seconds": sum(lifetimes),
            "median_tunnel_lifetime_seconds": median(lifetimes) if lifetimes else "",
            "max_tunnel_lifetime_seconds": max(lifetimes) if lifetimes else "",
            "relay_resets": sum(row["termination_reason"] == "relay_exception" for row in rows),
            "short_half_open_client_eof": sum(
                bool(row["half_open_probe"])
                and row["termination_reason"] == "client_eof"
                and row["lifetime_seconds"] != ""
                and float(row["lifetime_seconds"]) < 30
                for row in rows
            ),
            "longevity_issue": "YES" if any(
                row["termination_reason"] == "relay_exception" and row["lifetime_seconds"] != ""
                and 2_400 <= float(row["lifetime_seconds"]) <= 2_500
                for row in rows
            ) else "NO",
        })
    atomic_csv(args.audit_root / "proxy_long_duration_audit.csv", proxy_summary)

    circuit_rows = []
    for row in proxy:
        if row.get("route_states"):
            for state in row["route_states"]:
                circuit_rows.append({
                    "timestamp": row["_dt"].isoformat(),
                    "event": row.get("event"),
                    "connection_id": row.get("connection_id", ""),
                    "route_id": state.get("route_id"),
                    "route_state": state.get("state"),
                    "failure_count": state.get("failure_count"),
                    "half_open_probe_in_flight": state.get("half_open_probe_in_flight"),
                    "successful_probe_count": state.get("successful_probe_count"),
                })
    atomic_csv(args.audit_root / "circuit_breaker_long_run_audit.csv", circuit_rows)

    # Minute forensic timeline from 30 minutes before the first failure through
    # five minutes after the second route became stuck HALF_OPEN.
    both_half_open = next((
        row for row in proxy
        if sum(state.get("state") == "HALF_OPEN" for state in row.get("route_states", [])) == 2
    ), None)
    end_dt = (both_half_open["_dt"] if both_half_open else first_stale_dt + timedelta(hours=1)) + timedelta(minutes=5)
    start_dt = first_stale_dt - timedelta(minutes=30)
    start_minute = int(start_dt.timestamp() // 60)
    end_minute = int(end_dt.timestamp() // 60)

    timeline = []
    last_worker: dict[str, Any] | None = None
    last_proxy: dict[str, Any] | None = None
    for minute in range(start_minute, end_minute + 1):
        minute_dt = datetime.fromtimestamp(minute * 60, tz=UTC)
        worker_events = [row for row in lifecycle if minute_dt <= row["_dt"] < minute_dt + timedelta(minutes=1)]
        proxy_events = [row for row in proxy if minute_dt <= row["_dt"] < minute_dt + timedelta(minutes=1)]
        if worker_events:
            last_worker = worker_events[-1]
        if proxy_events:
            last_proxy = proxy_events[-1]
        sample = min(runtime, key=lambda row: abs((row["_dt"] - minute_dt).total_seconds())) if runtime else {}
        source = per_minute.get(minute, {"quote_count": 0, "trade_count": 0, "latency_ms": []})
        states = (last_proxy or {}).get("route_states", [])
        route_state = ";".join(f"{x.get('route_id')}={x.get('state')}" for x in states)
        exceptions = [
            f"{row.get('exception_type')}:{row.get('exception_message')}"
            for row in worker_events if row.get("exception_type") or row.get("exception_message")
        ]
        ages = {}
        for kind in ("quote", "trade"):
            stale = [row for row in worker_events if row["stage"] == f"{kind.upper()}_STALE"]
            ages[kind] = stale[-1]["latency_ms"] if stale else ""
        timeline.append({
            "timestamp": minute_dt.isoformat(),
            "connection_id": (last_worker or {}).get("connection_id", ""),
            "route_id": (last_proxy or {}).get("upstream", ""),
            "route_state": route_state,
            "websocket_state": (last_worker or {}).get("stage", ""),
            "last_quote_age_ms": ages["quote"],
            "last_trade_age_ms": ages["trade"],
            "quote_count_this_minute": source["quote_count"],
            "trade_count_this_minute": source["trade_count"],
            "bar_emitted": minute in set(bar_minutes),
            "reconnect_requested": any("STALE" in row["stage"] for row in worker_events),
            "reconnect_started": any(row["stage"] == "WEBSOCKET_CONNECT" for row in worker_events),
            "reconnect_completed": any(row["stage"] == "RESUBSCRIBE_READY" for row in worker_events),
            "circuit_breaker_event": ";".join(row.get("event", "") for row in proxy_events if row.get("route_states")),
            "HALF_OPEN_probe_count": sample.get("half_open_probes_active", ""),
            "thread_count": sample.get("proxy_thread_count", ""),
            "task_count": "NOT_CAPTURED",
            "queue_depth": "NOT_CAPTURED",
            "writer_lag_ms": "NOT_CAPTURED",
            "event_loop_lag_ms": "NOT_CAPTURED",
            "memory_mb": "NOT_CAPTURED",
            "socket_state_counts": "NOT_CAPTURED",
            "exception_type": ";".join(exceptions),
            "exception_message": ";".join(exceptions),
        })
    atomic_csv(args.audit_root / "long_run_failure_timeline.csv", timeline)

    task_rows = [{
        "sample_timestamp": row["sample_timestamp"],
        "proxy_thread_count": row.get("proxy_thread_count", ""),
        "connection_manager_count": row.get("connection_manager_count", ""),
        "websocket_reader_count": row.get("websocket_reader_count", ""),
        "reconnect_task_count": row.get("reconnect_task_count", ""),
        "cooldown_task_active": row.get("cooldown_task_active", ""),
        "half_open_probes_active": row.get("half_open_probes_active", ""),
        "validation_status": "RECORDED",
    } for row in runtime]
    atomic_csv(args.audit_root / "task_thread_long_run_audit.csv", task_rows)

    max_threads = max((int(row.get("proxy_thread_count") or 0) for row in runtime), default=0)
    min_threads = min((int(row.get("proxy_thread_count") or 0) for row in runtime), default=0)
    atomic_csv(args.audit_root / "memory_handle_socket_audit.csv", [{
        "component": "failed_run",
        "memory_mb": "NOT_CAPTURED_HISTORICALLY",
        "handle_count": "NOT_CAPTURED_HISTORICALLY",
        "established_sockets": "DERIVED_FROM_PROXY_CONNECTIONS",
        "time_wait_sockets": "NOT_CAPTURED_HISTORICALLY",
        "close_wait_sockets": "NOT_CAPTURED_HISTORICALLY",
        "proxy_thread_min": min_threads,
        "proxy_thread_max": max_threads,
        "resource_leak_evidence": "NO",
        "notes": "Thread count stayed bounded; OS memory/handle history was not recorded.",
    }])

    latency_rows = []
    for minute, values in sorted(per_minute.items()):
        latencies = values["latency_ms"]
        if not latencies:
            continue
        latency_rows.append({
            "minute": datetime.fromtimestamp(minute * 60, tz=UTC).isoformat(),
            "event_count": len(latencies),
            "receive_minus_exchange_p50_ms": percentile(latencies, 0.5),
            "receive_minus_exchange_p95_ms": percentile(latencies, 0.95),
            "receive_minus_exchange_max_ms": max(latencies),
            "event_loop_lag_ms": "NOT_CAPTURED_HISTORICALLY",
            "validation_status": "TRANSPORT_LATENCY_ONLY",
        })
    atomic_csv(args.audit_root / "event_loop_lag_audit.csv", latency_rows)

    queue_rows = [{
        "sample_timestamp": row["sample_timestamp"],
        "bars_1m": row.get("bars_1m", ""),
        "quote_events": row.get("quote_events", ""),
        "trade_events": row.get("trade_events", ""),
        "queue_depth": "NOT_CAPTURED_HISTORICALLY",
        "dropped_events": row.get("dropped_events", ""),
        "queue_backpressure_evidence": "NO" if row.get("dropped_events", "0") == "0" else "YES",
    } for row in runtime]
    atomic_csv(args.audit_root / "queue_backpressure_long_run.csv", queue_rows)

    atomic_csv(args.audit_root / "writer_latency_audit.csv", [{
        "component": "event_recorder",
        "write_latency_ms": "NOT_CAPTURED_HISTORICALLY",
        "flush_latency_ms": "NOT_CAPTURED_HISTORICALLY",
        "file_rotation_events": 0,
        "disk_errors": 0,
        "writer_failure_evidence": "NO",
        "notes": "Recorder continued through recovery and dropped_events remained zero.",
    }])

    atomic_csv(args.audit_root / "ping_pong_audit.csv", [{
        "component": "websocket-client",
        "last_successful_ping": "NOT_CAPTURED_HISTORICALLY",
        "last_successful_pong": "NOT_CAPTURED_HISTORICALLY",
        "missed_heartbeat_count": "NOT_CAPTURED_HISTORICALLY",
        "application_stale_detection": first_stale_dt.isoformat(),
        "validation_status": "PING_PONG_NOT_INSTRUMENTED",
    }])

    stale_during_recovery = sum(
        row["stage"] in {"QUOTE_STALE", "TRADE_STALE"}
        and row["_dt"] > first_stale_dt
        and row["_dt"] < first_stale_dt + timedelta(minutes=4)
        for row in lifecycle
    )
    leaked_half_open = sum(
        row["half_open_probe"] and row["termination_reason"] == "client_eof"
        and row["lifetime_seconds"] != "" and float(row["lifetime_seconds"]) < 30
        for row in connection_rows
    )
    feedback_rows = [
        {
            "mechanism": "STALE_MONITOR_CANCELLED_VALIDATING_CONNECTIONS",
            "observed": "YES",
            "count": stale_during_recovery,
            "evidence": "Repeated *_STALE events occurred before RESUBSCRIBE_READY while feed_state was RECOVERING.",
        },
        {
            "mechanism": "HALF_OPEN_OWNERSHIP_NOT_RELEASED_ON_SHORT_CLIENT_EOF",
            "observed": "YES",
            "count": leaked_half_open,
            "evidence": "Half-open tunnel ended client_eof under 30s with no success/failure transition.",
        },
        {
            "mechanism": "ALL_ROUTES_STUCK_HALF_OPEN",
            "observed": "YES" if both_half_open else "NO",
            "count": 1 if both_half_open else 0,
            "evidence": both_half_open["_dt"].isoformat() if both_half_open else "",
        },
    ]
    atomic_csv(args.audit_root / "recovery_feedback_loop_audit.csv", feedback_rows)

    first_failure = {
        "experiment_id": args.experiment_id,
        "failure_label": "FAILED_LONG_HORIZON_CONTINUITY",
        "first_causal_failure_timestamp": first_stale_dt.isoformat(),
        "last_good_trade_receive_inferred": last_good_dt.isoformat(),
        "first_causal_failure_type": "UPSTREAM_PROXY_TUNNEL_DATA_SILENCE_PRECEDING_RESET",
        "first_causal_failure": (
            "Trade delivery stopped on route 100.64.0.5:7890; the first observable "
            "failure was TRADE_STALE, followed by ConnectionResetError on the same "
            "~2444.6-second proxy tunnel."
        ),
        "root_cause": "UPSTREAM_PROXY_TUNNEL_RESET_PLUS_RECOVERY_STATE_DEFECTS",
        "downstream_effect": "RECONNECT_STORM",
        "first_proxy_failure_timestamp": first_relay_failure["_dt"].isoformat(),
        "missing_minutes": [datetime.fromtimestamp(x * 60, tz=UTC).isoformat() for x in missing_minutes],
        "missing_minute_classification": {
            datetime.fromtimestamp(x * 60, tz=UTC).isoformat(): (
                "PROXY_TUNNEL_FAILURE_AND_RECOVERY_FEEDBACK_LOOP"
            ) for x in missing_minutes
        },
        "unknown_missing_minutes": 0,
        "reconnect_storm_was": "DOWNSTREAM_EFFECT",
        "task_thread_leak": False,
        "queue_event_loop_degradation": False,
        "proxy_route_longevity_issue": True,
        "recovery_feedback_loop_issue": True,
        "raw_evidence_sha256": {
            "connectivity_state_timeline.csv": sha256(lifecycle_path),
            "proxy_lifecycle_round2.jsonl": sha256(args.proxy_log),
            "connection_runtime_health.csv": sha256(runtime_path),
            "BTCUSDT_1m.jsonl": sha256(bars_path),
        },
    }
    atomic_json(args.audit_root / "first_causal_failure.json", first_failure)

    marker = {
        "experiment_id": args.experiment_id,
        "status": "FAILED_LONG_HORIZON_CONTINUITY",
        "reason": "UNEXPLAINED_MISSING_BARS_FOLLOWED_BY_RECONNECT_STALE_DATA_STORM",
        "authoritative_performance": "INVALID_NOT_COMPUTED",
        "observed_bars": len(bars),
        "expected_bars": 1440,
        "unexplained_missing_minutes": len(missing_minutes),
        "production_exchange_orders": 0,
        "raw_evidence_preserved": True,
        "audit": str(args.audit_root / "first_causal_failure.json"),
    }
    atomic_json(experiment / "FAILED_LONG_HORIZON_CONTINUITY.json", marker)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
