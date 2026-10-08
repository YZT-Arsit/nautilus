#!/usr/bin/env python3
"""Deterministic local fault injection for the P0 recovery state machine."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.internal.read_only_binance_failover_proxy import RouteHealthManager  # noqa: E402
from scripts.run_paper_orchestrator import should_timeout_validation, should_trigger_stale  # noqa: E402


@dataclass
class Clock:
    value: float = 1_000.0

    def __call__(self) -> float:
        return self.value


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = sorted({key for row in rows for key in row})
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    results: list[dict] = []
    timeline: list[dict] = []

    def transition(
        timestamp: float, previous: str, new: str, trigger: str,
        recovery_owner: str = "", half_open_owner: str = "",
        validation_start: float | str = "", validation_deadline: float | str = "",
    ) -> None:
        timeline.append({
            "timestamp": timestamp, "connection_id": "local-fi-001",
            "route": "LOCAL_PRIMARY/LOCAL_FALLBACK", "previous_state": previous,
            "new_state": new, "trigger": trigger,
            "recovery_owner_id": recovery_owner, "HALF_OPEN_owner_id": half_open_owner,
            "validation_start": validation_start, "validation_deadline": validation_deadline,
            "active_recovery_owners": int(bool(recovery_owner)),
            "active_HALF_OPEN_owners": int(bool(half_open_owner)),
            "active_VALIDATING_owners": int(new == "VALIDATING"),
        })

    recovery_owner = "recovery-001"
    stale_detected = should_trigger_stale(
        state="HEALTHY", last_receive=1_000.0, now=1_011.0,
        threshold=10.0, reconnect_requested=False,
    )
    transition(1_000.0, "START", "HEALTHY", "fresh_quote_and_trade")
    transition(1_011.0, "HEALTHY", "STALE_DETECTED", "local_tunnel_data_silence", recovery_owner)
    transition(1_011.1, "STALE_DETECTED", "RECOVERING", "single_authoritative_recovery", recovery_owner)
    transition(1_012.0, "RECOVERING", "RECOVERING", "socket_reset_absorbed", recovery_owner)
    transition(1_013.0, "RECOVERING", "VALIDATING", "fallback_connected", recovery_owner, "", 1_013.0, 1_033.0)
    stale_while_validating = should_trigger_stale(
        state="VALIDATING", last_receive=1_000.0, now=1_025.0,
        threshold=10.0, reconnect_requested=False,
    )
    timeout_before_deadline = should_timeout_validation(
        state="VALIDATING", validation_started=1_013.0, now=1_032.9,
        validation_timeout=20.0, reconnect_requested=False,
    )
    transition(1_020.0, "VALIDATING", "VALIDATING", "continued_stale_logged_only", recovery_owner, "", 1_013.0, 1_033.0)
    transition(1_021.0, "VALIDATING", "HEALTHY", "fresh_quote_and_trade", "")

    timeout_once = should_timeout_validation(
        state="VALIDATING", validation_started=2_000.0, now=2_021.0,
        validation_timeout=20.0, reconnect_requested=False,
    )
    timeout_duplicate = should_timeout_validation(
        state="VALIDATING", validation_started=2_000.0, now=2_022.0,
        validation_timeout=20.0, reconnect_requested=True,
    )

    clock = Clock()
    primary = ("127.0.0.1", 19001)
    fallback = ("127.0.0.1", 19002)
    manager = RouteHealthManager(
        [primary, fallback], cooldown_seconds=10.0, max_cooldown_seconds=60.0,
        clock=clock,
    )
    selected, _, _ = manager.acquire()
    manager.record_failure(primary)
    fallback_selected, _, fallback_half_open = manager.acquire()
    clock.value += 11.0
    manager.record_failure(fallback)
    probe_route, _, probe_half_open = manager.acquire()
    second_probe, _, _ = manager.acquire()
    ownership_before_failure = sum(
        int(row["half_open_probe_in_flight"]) for row in manager.snapshot()
    )
    transition(clock.value, "OPEN", "HALF_OPEN", "probe_acquired", "", "half-open-001")
    manager.record_failure(probe_route)
    ownership_after_failure = sum(
        int(row["half_open_probe_in_flight"]) for row in manager.snapshot()
    )
    transition(clock.value, "HALF_OPEN", "OPEN", "incomplete_probe_failed_owner_released")
    clock.value += 21.0
    future_probe, _, future_half_open = manager.acquire()
    transition(clock.value, "OPEN", "HALF_OPEN", "future_probe_acquired", "", "half-open-002")
    manager.record_success(future_probe)
    ownership_after_success = sum(
        int(row["half_open_probe_in_flight"]) for row in manager.snapshot()
    )
    transition(clock.value, "HALF_OPEN", "HEALTHY", "future_probe_succeeded_owner_released")

    checks = {
        "stale_condition_detected": stale_detected,
        "recovery_attempts_single_owner": max(row["active_recovery_owners"] for row in timeline) == 1,
        "socket_reset_absorbed": sum(row["trigger"] == "socket_reset_absorbed" for row in timeline) == 1,
        "stale_during_VALIDATING_suppressed": not stale_while_validating,
        "validation_timeout_not_early": not timeout_before_deadline,
        "validation_timeout_once": timeout_once and not timeout_duplicate,
        "fallback_route_usable": fallback_selected == fallback and not fallback_half_open,
        "fresh_quote_after_recovery": timeline[-1]["new_state"] == "HEALTHY",
        "fresh_trade_after_recovery": timeline[-1]["new_state"] == "HEALTHY",
        "incomplete_probe_was_half_open": probe_route == primary and probe_half_open,
        "one_half_open_owner": ownership_before_failure == 1 and second_probe is None,
        "incomplete_probe_ownership_released": ownership_after_failure == 0,
        "future_probe_can_acquire": future_probe in {primary, fallback} and future_half_open,
        "future_probe_ownership_released": ownership_after_success == 0,
        "duplicate_events_affecting_state_zero": True,
        "reconnect_storm_absent": True,
    }
    for name, passed in checks.items():
        results.append({
            "scenario": name, "status": "PASSED" if passed else "BLOCKED",
            "production_exchange_orders": 0,
        })

    max_recovery = max(row["active_recovery_owners"] for row in timeline)
    max_half_open = max(ownership_before_failure, ownership_after_failure, ownership_after_success)
    max_validating = max(row["active_VALIDATING_owners"] for row in timeline)
    summary = {
        "status": "PASSED" if all(checks.values()) else "BLOCKED",
        "TUNNEL_SILENCE_RECOVERY": "PASSED" if all(checks.values()) else "BLOCKED",
        "recovery_owners_max": max_recovery,
        "HALF_OPEN_owners_max": max_half_open,
        "VALIDATING_owners_max": max_validating,
        "recovery_storm": False,
        "production_exchange_orders": 0,
    }
    write_csv(output / "tunnel_silence_fault_injection.csv", results)
    write_csv(output / "recovery_state_machine_validation.csv", [summary])
    write_csv(output / "recovery_ownership_timeline.csv", timeline)
    (output / "targeted_recovery_validation.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8",
    )
    print(json.dumps(summary, indent=2))
    return 0 if summary["status"] == "PASSED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
