#!/usr/bin/env python3
"""Independent active-active Binance public market-data collector service."""

from __future__ import annotations

import argparse
import csv
import json
import queue
import random
import socket
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data_engine.live.binance_ws_client import BinancePublicWebSocketSource, proxy_transport_factory
from data_engine.live.binance_ws import normalize_message
from strategy_framework.paper_trading.active_active import (
    CanonicalMarketDataMerger,
    CanonicalWriteAheadLog,
    canonical_health,
)


PUBLIC_WS = "wss://fstream.binance.com"


def main() -> int:  # noqa: C901
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--start-ns", type=int, required=True)
    parser.add_argument("--end-ns", type=int, required=True)
    parser.add_argument("--route", action="append", default=[], help="ROUTE_ID=proxy_host:port")
    parser.add_argument(
        "--remote-source", action="append", default=[],
        help="ROUTE_ID=host:port for a durable independent collector stream",
    )
    parser.add_argument(
        "--remote-live-max-lag-seconds", type=float, default=5.0,
        help="older remote WAL catch-up is audit-only and cannot drive live execution",
    )
    parser.add_argument("--stale-seconds", type=float, default=10.0)
    parser.add_argument("--reorder-delay-ms", type=float, default=250.0)
    parser.add_argument("--inject-reset-route")
    parser.add_argument("--inject-at-seconds", type=float)
    args = parser.parse_args()
    routes: dict[str, tuple[str, int]] = {}
    for value in args.route:
        route_id, endpoint = value.split("=", 1)
        host, port = endpoint.rsplit(":", 1)
        routes[route_id] = (host, int(port))
    remote_sources: dict[str, tuple[str, int]] = {}
    for value in args.remote_source:
        route_id, endpoint = value.split("=", 1)
        host, port = endpoint.rsplit(":", 1)
        if route_id in routes:
            raise ValueError(f"duplicate source id: {route_id}")
        remote_sources[route_id] = (host, int(port))
    all_sources = {**routes, **remote_sources}
    if len(all_sources) < 2:
        raise RuntimeError("active-active collector requires at least two independent routes")

    output = args.output.resolve()
    wal = CanonicalWriteAheadLog(output / "canonical_wal")
    merger = CanonicalMarketDataMerger(reorder_delay_ms=args.reorder_delay_ms)
    event_queue: queue.Queue = queue.Queue(maxsize=500_000)
    stop = threading.Event()
    route_stop = {route: threading.Event() for route in all_sources}
    route_state = {route: "RECOVERING" for route in all_sources}
    route_last = {route: {"quote": None, "trade": None} for route in all_sources}
    route_counts: dict[int, dict[str, int]] = {}
    canonical_counts: dict[int, dict[str, int]] = {}
    reconnects = {route: 0 for route in all_sources}
    outages = {route: 0 for route in all_sources}
    remote_last_sequence = {route: 0 for route in remote_sources}
    remote_delayed_audit_only = {route: 0 for route in remote_sources}
    errors: list[dict] = []
    state_lock = threading.Lock()
    connection_stop: dict[str, threading.Event | None] = {route: None for route in all_sources}
    injected = False

    def write_live_status() -> None:
        complete = sum(bool(row.get("quote")) and bool(row.get("trade")) for row in canonical_counts.values())
        value = {
            "updated_at_ns": time.time_ns(), "route_states": dict(route_state),
            "reconnects": dict(reconnects), "route_outages": dict(outages),
            "canonical_complete_minutes_seen": complete,
            "canonical_event_count": wal.sequence, "queue_depth": event_queue.qsize(),
            "remote_last_sequence": dict(remote_last_sequence),
            "remote_delayed_audit_only": dict(remote_delayed_audit_only),
            "fault_injection_performed": injected, "production_exchange_orders": 0,
        }
        destination = output / "collector_live_status.json"
        if sys.platform == "win32":
            destination.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
        else:
            temporary = output / "collector_live_status.json.tmp"
            temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
            temporary.replace(destination)

    def worker(route_id: str, endpoint: tuple[str, int], initial_delay: float) -> None:
        if stop.wait(initial_delay):
            return
        rng = random.Random(route_id)
        streak = 0
        while not stop.is_set():
            local_stop = threading.Event()
            connection_stop[route_id] = local_stop
            route_stop[route_id].clear()
            with state_lock:
                route_state[route_id] = "RECOVERING"
            source = BinancePublicWebSocketSource(
                args.symbol, ("trade", "bookTicker", "markPrice@1s"),
                base_url=PUBLIC_WS, instrument_id=f"{args.symbol}-PERP.BINANCE",
                transport_factory=proxy_transport_factory(*endpoint),
            )
            connected_at = time.monotonic()
            seen = {"quote": False, "trade": False}
            try:
                for event in source.iter_events(
                    max_messages=2_000_000_000, timeout_seconds=172_800.0,
                    receive_timeout_seconds=5.0, stop_event=local_stop,
                ):
                    if stop.is_set() or route_stop[route_id].is_set():
                        break
                    kind = str(event.event_type)
                    receive_ns = int(getattr(event, "receive_time_ns", 0) or time.time_ns())
                    if kind in {"quote", "trade"}:
                        route_last[route_id][kind] = time.monotonic()
                        seen[kind] = True
                        if all(seen.values()):
                            with state_lock:
                                route_state[route_id] = "HEALTHY"
                    event_queue.put((route_id, event, receive_ns), timeout=5)
                if not stop.is_set() and not route_stop[route_id].is_set():
                    raise ConnectionError("route stream ended")
            except Exception as exc:
                errors.append({
                    "timestamp_ns": time.time_ns(), "route": route_id,
                    "type": type(exc).__name__, "message": str(exc),
                })
            finally:
                connection_stop[route_id] = None
                if not stop.is_set():
                    with state_lock:
                        route_state[route_id] = "RECOVERING"
                    reconnects[route_id] += 1
                    outages[route_id] += 1
            if stop.is_set():
                break
            stable = time.monotonic() - connected_at
            streak = 0 if stable >= 300 else streak + 1
            delay = min(120.0, 2.0 ** max(0, streak - 1)) + rng.uniform(0, 1)
            stop.wait(delay)

    def remote_worker(route_id: str, endpoint: tuple[str, int], initial_delay: float) -> None:
        """Consume a durable independent collector without replaying delayed ticks live."""
        if stop.wait(initial_delay):
            return
        rng = random.Random(route_id)
        streak = 0
        while not stop.is_set():
            local_stop = threading.Event()
            connection_stop[route_id] = local_stop
            with state_lock:
                route_state[route_id] = "RECOVERING"
            started = time.monotonic()
            seen = {"quote": False, "trade": False}
            try:
                with socket.create_connection(endpoint, timeout=15) as stream:
                    stream.settimeout(5)
                    request = (
                        {"live_only": True}
                        if remote_last_sequence[route_id] == 0
                        else {"from_sequence": remote_last_sequence[route_id] + 1}
                    )
                    stream.sendall((json.dumps(request) + "\n").encode())
                    handle = stream.makefile("r", encoding="utf-8")
                    while not stop.is_set() and not local_stop.is_set():
                        try:
                            line = handle.readline()
                        except TimeoutError:
                            continue
                        if not line:
                            raise ConnectionError("remote collector stream ended")
                        row = json.loads(line)
                        if row.get("heartbeat"):
                            continue
                        sequence = int(row["collector_sequence"])
                        remote_last_sequence[route_id] = sequence
                        source_receive_ns = int(row["source_receive_time_ns"])
                        downstream_receive_ns = time.time_ns()
                        lag = (downstream_receive_ns - source_receive_ns) / 1e9
                        if lag > args.remote_live_max_lag_seconds:
                            remote_delayed_audit_only[route_id] += 1
                            continue
                        event = normalize_message(
                            json.loads(row["raw"]), instrument_id=f"{args.symbol}-PERP.BINANCE",
                            receive_time_ns=source_receive_ns,
                        )
                        if event is None:
                            continue
                        kind = str(event.event_type)
                        if kind in seen:
                            route_last[route_id][kind] = time.monotonic()
                            seen[kind] = True
                            if all(seen.values()):
                                with state_lock:
                                    route_state[route_id] = "HEALTHY"
                        event_queue.put((route_id, event, downstream_receive_ns), timeout=5)
                if not stop.is_set() and not local_stop.is_set():
                    raise ConnectionError("remote collector disconnected")
            except Exception as exc:
                errors.append({
                    "timestamp_ns": time.time_ns(), "route": route_id,
                    "type": type(exc).__name__, "message": str(exc),
                })
            finally:
                connection_stop[route_id] = None
                if not stop.is_set():
                    with state_lock:
                        route_state[route_id] = "RECOVERING"
                    reconnects[route_id] += 1
                    outages[route_id] += 1
            if stop.is_set():
                break
            lifetime = time.monotonic() - started
            streak = 0 if lifetime >= 300 else streak + 1
            stop.wait(min(120.0, 2.0 ** max(0, streak - 1)) + rng.random())

    threads = []
    for index, (route_id, endpoint) in enumerate(routes.items()):
        thread = threading.Thread(
            target=worker, args=(route_id, endpoint, index * 3.0),
            name=f"collector-{route_id}", daemon=True,
        )
        thread.start()
        threads.append(thread)
    for index, (route_id, endpoint) in enumerate(remote_sources.items(), start=len(routes)):
        thread = threading.Thread(
            target=remote_worker, args=(route_id, endpoint, index * 3.0),
            name=f"collector-{route_id}", daemon=True,
        )
        thread.start()
        threads.append(thread)

    health_rows: list[dict] = []
    last_health_minute = None
    start_mono = time.monotonic()
    last_live_status = 0.0
    wall_deadline = args.end_ns / 1e9 + 30.0
    while time.time() < wall_deadline:
        elapsed = time.monotonic() - start_mono
        if (
            not injected and args.inject_reset_route and args.inject_at_seconds is not None
            and elapsed >= args.inject_at_seconds
        ):
            target = connection_stop.get(args.inject_reset_route)
            if target is not None:
                target.set()
                injected = True
        try:
            route_id, event, receive_ns = event_queue.get(timeout=0.05)
            minute = int(event.event_time_ns // 60_000_000_000 * 60_000_000_000)
            if args.start_ns <= minute < args.end_ns:
                row = route_counts.setdefault(minute, {})
                key = f"{route_id}_{event.event_type}_count"
                row[key] = row.get(key, 0) + 1
            merger.push(route_id, event, receive_ns)
        except queue.Empty:
            pass
        ready = merger.release_ready()
        for item in ready:
            earliest = min(item.receive_times_ns.values())
            if hasattr(item.event, "receive_time_ns"):
                item.event = replace(item.event, receive_time_ns=earliest)
            wal.append(item)
            minute = int(item.event.event_time_ns // 60_000_000_000 * 60_000_000_000)
            if args.start_ns <= minute < args.end_ns:
                row = canonical_counts.setdefault(minute, {"quote": 0, "trade": 0})
                if item.event.event_type in row:
                    row[item.event.event_type] += 1
        if merger.provenance_updates:
            wal.append_provenance_updates(merger.provenance_updates)
            merger.provenance_updates.clear()
        now_mono = time.monotonic()
        healthy = []
        for route_id in all_sources:
            fresh = all(
                value is not None and now_mono - value <= args.stale_seconds
                for value in route_last[route_id].values()
            )
            if fresh and route_state[route_id] == "HEALTHY":
                healthy.append(route_id)
            elif route_state[route_id] == "HEALTHY":
                route_state[route_id] = "STALE"
        current_minute = time.time_ns() // 60_000_000_000 * 60_000_000_000
        if current_minute != last_health_minute:
            last_health_minute = current_minute
            health = canonical_health({
                route: "HEALTHY" if route in healthy else route_state[route]
                for route in all_sources
            })
            health_rows.append({
                "minute": current_minute, "canonical_health": health,
                "healthy_routes": ";".join(healthy), "queue_depth": event_queue.qsize(),
                **{f"{route}_state": route_state[route] for route in all_sources},
                **{f"{route}_reconnects": reconnects[route] for route in all_sources},
            })
        if now_mono - last_live_status >= 5.0:
            write_live_status()
            last_live_status = now_mono
        if time.time_ns() >= args.end_ns and event_queue.empty():
            break

    for item in merger.release_ready(force=True):
        wal.append(item)
    wal.close()
    write_live_status()
    stop.set()
    for event in connection_stop.values():
        if event is not None:
            event.set()
    for thread in threads:
        thread.join(timeout=10)

    coverage = []
    unavailable = 0
    for minute in range(args.start_ns, args.end_ns, 60_000_000_000):
        route = route_counts.get(minute, {})
        canonical = canonical_counts.get(minute, {"quote": 0, "trade": 0})
        row = {"minute": minute}
        for route_id in all_sources:
            row[f"{route_id}_quote_count"] = route.get(f"{route_id}_quote_count", 0)
            row[f"{route_id}_trade_count"] = route.get(f"{route_id}_trade_count", 0)
        row["canonical_quote_count"] = canonical["quote"]
        row["canonical_trade_count"] = canonical["trade"]
        row["canonical_health"] = "HEALTHY" if canonical["quote"] and canonical["trade"] else "UNAVAILABLE"
        unavailable += int(row["canonical_health"] == "UNAVAILABLE")
        coverage.append(row)
    with (output / "canonical_minute_coverage.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(coverage[0]))
        writer.writeheader(); writer.writerows(coverage)
    if health_rows:
        with (output / "route_health_timeline.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(health_rows[0]))
            writer.writeheader(); writer.writerows(health_rows)
    summary = {
        "status": "PASSED" if unavailable == 0 else "BLOCKED",
        "canonical_missing_minutes": unavailable, "reconnects": reconnects,
        "route_outages": outages, "canonical_events": wal.sequence,
        "duplicate_events": merger.duplicate_count,
        "late_duplicate_events": merger.late_duplicate_count,
        "late_unique_events": merger.late_unique_count,
        "remote_last_sequence": remote_last_sequence,
        "remote_delayed_audit_only": remote_delayed_audit_only,
        "fault_injection_performed": injected,
        "production_market_data": "READ_ONLY", "production_exchange_orders": 0,
    }
    if errors:
        with (output / "route_errors.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(errors[0]))
            writer.writeheader(); writer.writerows(errors)
    (output / "collector_validation.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))
    return 0 if summary["status"] == "PASSED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
