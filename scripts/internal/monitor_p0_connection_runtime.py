#!/usr/bin/env python3
"""Read-only runtime sampler for the gated P0 paper-market-data pipeline."""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


FIELDS = [
    "sample_timestamp",
    "gate_status",
    "experiment",
    "phase",
    "bars_1m",
    "quote_events",
    "trade_events",
    "feed_state",
    "reconnects",
    "stale_incidents",
    "worker_errors",
    "dropped_events",
    "duplicate_events_dropped",
    "proxy_pid",
    "proxy_thread_count",
    "connection_manager_count",
    "websocket_reader_count",
    "reconnect_task_count",
    "cooldown_task_created",
    "cooldown_task_active",
    "cooldown_task_completed",
    "routes_closed",
    "routes_open",
    "routes_half_open",
    "half_open_probes_active",
    "global_recovery_probes_active",
]


def read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def latest_proxy_state(path: Path) -> dict[str, Any]:
    latest: dict[str, Any] = {}
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if event.get("route_states"):
                    latest = event
    except OSError:
        pass
    return latest


def listening_pid(port: int) -> int | None:
    try:
        result = subprocess.run(
            ["netstat", "-ano", "-p", "tcp"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    suffix = f":{port}"
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[1].endswith(suffix) and parts[3] == "LISTENING":
            try:
                return int(parts[4])
            except ValueError:
                return None
    return None


def process_threads(pid: int | None) -> int | None:
    if pid is None:
        return None
    command = [
        "powershell",
        "-NoProfile",
        "-Command",
        f"(Get-Process -Id {pid} -ErrorAction Stop).Threads.Count",
    ]
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return int(result.stdout.strip()) if result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None


def experiment_from_gate(gate: dict[str, Any]) -> Path | None:
    for key in ("new_experiment_path", "continuity_experiment", "smoke_experiment"):
        value = gate.get(key)
        if value:
            return Path(str(value))
    return None


def append_row(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    exists = path.exists() and path.stat().st_size > 0
    with path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        if not exists:
            writer.writeheader()
        writer.writerow(row)
        handle.flush()


def sample(audit_root: Path, port: int) -> dict[str, Any]:
    gate = read_json(audit_root / "gate_status.json")
    experiment = experiment_from_gate(gate)
    heartbeat_path = (
        experiment / "workers" / "BTCUSDT" / "health" / "heartbeat.json"
        if experiment
        else Path("__missing__")
    )
    heartbeat = read_json(heartbeat_path)
    proxy = latest_proxy_state(audit_root / "proxy_lifecycle_round2.jsonl")
    states = proxy.get("route_states", [])
    counts = heartbeat.get("counts", {})
    reconnects = heartbeat.get("reconnects", 0)
    if isinstance(reconnects, dict):
        reconnects = sum(int(value) for value in reconnects.values())
    feed_state = heartbeat.get("feed_state", {})
    workers = heartbeat.get("workers_running", {})
    pid = listening_pid(port)
    return {
        "sample_timestamp": datetime.now(UTC).isoformat(),
        "gate_status": gate.get("status", "UNKNOWN"),
        "experiment": str(experiment or ""),
        "phase": heartbeat.get("phase", ""),
        "bars_1m": counts.get("bars_1m", 0),
        "quote_events": counts.get("quote_events", 0),
        "trade_events": counts.get("trade_events", 0),
        "feed_state": feed_state.get("BTCUSDT", "UNKNOWN"),
        "reconnects": reconnects,
        "stale_incidents": heartbeat.get("stale_incidents", 0),
        "worker_errors": heartbeat.get("worker_errors", 0),
        "dropped_events": heartbeat.get("dropped_events", 0),
        "duplicate_events_dropped": heartbeat.get("duplicate_events_dropped", 0),
        "proxy_pid": pid or "",
        "proxy_thread_count": process_threads(pid) or "",
        "connection_manager_count": 1 if pid else 0,
        "websocket_reader_count": sum(bool(value) for value in workers.values()),
        "reconnect_task_count": 1 if str(feed_state.get("BTCUSDT")) == "RECOVERING" else 0,
        "cooldown_task_created": 0,
        "cooldown_task_active": 0,
        "cooldown_task_completed": 0,
        "routes_closed": sum(item.get("state") == "CLOSED" for item in states),
        "routes_open": sum(item.get("state") == "OPEN" for item in states),
        "routes_half_open": sum(item.get("state") == "HALF_OPEN" for item in states),
        "half_open_probes_active": sum(
            bool(item.get("half_open_probe_in_flight")) for item in states
        ),
        "global_recovery_probes_active": sum(
            bool(item.get("half_open_probe_in_flight")) for item in states
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit-root", type=Path, required=True)
    parser.add_argument("--interval-seconds", type=float, default=60.0)
    parser.add_argument("--port", type=int, default=18898)
    parser.add_argument("--max-hours", type=float, default=28.0)
    args = parser.parse_args()
    output = args.audit_root / "connection_runtime_health.csv"
    deadline = time.monotonic() + args.max_hours * 3600
    terminal = {"PASSED", "BLOCKED_30M_NETWORK_SMOKE", "BLOCKED_2H_CONTINUITY_TEST", "BLOCKED_CLEAN_24H", "BLOCKED_CLEAN_24H_COVERAGE", "BLOCKED_REPLAY", "BLOCKED_PACKAGING"}
    while time.monotonic() < deadline:
        row = sample(args.audit_root, args.port)
        append_row(output, row)
        if row["gate_status"] in terminal:
            break
        time.sleep(args.interval_seconds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
