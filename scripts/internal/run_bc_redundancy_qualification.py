#!/usr/bin/env python3
"""Run the gated 120-minute Route-B + durable Collector-C qualification only."""

from __future__ import annotations

import json
import socket
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd


REPO = Path(r"D:\nautilus")
PARENT = REPO / "paper_trading/experiments/paper_20260928_4c9ee2b28d67"
AUDIT = REPO / "outputs/baseline_evaluation/paper_market_data_continuity_repair"
STATUS = AUDIT / "bc_redundancy_qualification_status.json"
WARMUP = REPO / "paper_trading/experiments/paper_regression_30m_20261006_035751/workers/BTCUSDT/warmup/BTCUSDT_1m.jsonl"
CANDIDATE = "pc_2fe14acb95eac19f88d4"


def write_status(**values) -> None:
    STATUS.parent.mkdir(parents=True, exist_ok=True)
    values.update({
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "production_market_data": "READ_ONLY",
        "production_exchange_orders": 0,
        "new_24h_started": False,
    })
    STATUS.write_text(json.dumps(values, indent=2) + "\n", encoding="utf-8")


def run(command: list[str]) -> None:
    subprocess.run(command, cwd=REPO, check=True)


def main() -> int:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    experiment_id = f"paper_bc_redundancy_qualification_120m_{stamp}"
    experiment = REPO / "paper_trading/experiments" / experiment_id
    try:
        if STATUS.exists():
            previous = json.loads(STATUS.read_text(encoding="utf-8-sig"))
            previous_id = previous.get("experiment_id", "unknown")
            previous_path = REPO / "paper_trading/experiments" / previous_id
            invalid = previous_path / "INVALID_PRECOMMIT_FORWARDING_RACE.json"
            if invalid.exists():
                previous.update({
                    "status": "SUPERSEDED_INVALID",
                    "authoritative": False,
                    "superseded_reason": "PRECOMMIT_FORWARDING_RACE",
                })
                archive = AUDIT / f"bc_redundancy_qualification_status_{previous_id}.json"
                archive.write_text(json.dumps(previous, indent=2) + "\n", encoding="utf-8")
        # This is only an endpoint reachability preflight; collector health is
        # validated from actual fresh quote/trade delivery in the 120m run.
        with socket.create_connection(("100.64.0.22", 7892), timeout=10):
            pass
        write_status(status="PREPARING", experiment_id=experiment_id, experiment_path=str(experiment))
        run([
            sys.executable, str(REPO / "scripts/internal/prepare_focused_paper_experiment.py"),
            "--parent", str(PARENT), "--output", str(experiment),
            "--experiment-id", experiment_id, "--candidate-id", CANDIDATE,
            "--purpose", "B_PLUS_DURABLE_COLLECTOR_C_120M_REDUNDANCY_QUALIFICATION",
        ])
        write_status(status="RUNNING", experiment_id=experiment_id, experiment_path=str(experiment))
        run([
            sys.executable, str(REPO / "scripts/internal/launch_active_active_phase.py"),
            "--repo", str(REPO), "--experiment", str(experiment),
            "--candidate-id", CANDIDATE, "--warmup-source", str(WARMUP),
            "--duration-seconds", "7200", "--expected-bars", "120",
            "--inject-reset-route", "ROUTE_B", "--inject-at-seconds", "3600",
        ])
        coverage_path = experiment / "active_active/canonical_minute_coverage.csv"
        coverage = pd.read_csv(coverage_path)
        present_b = (coverage.ROUTE_B_quote_count.gt(0) & coverage.ROUTE_B_trade_count.gt(0))
        present_c = (coverage.ROUTE_C_quote_count.gt(0) & coverage.ROUTE_C_trade_count.gt(0))
        canonical = coverage.canonical_quote_count.gt(0) & coverage.canonical_trade_count.gt(0)
        both = present_b & present_c
        collector = json.loads((experiment / "active_active/collector_validation.json").read_text())
        consumer = json.loads((experiment / "workers/BTCUSDT/dry_run_validation.json").read_text())
        checks = {
            "canonical_bars_120": int(canonical.sum()) == 120,
            "canonical_missing_zero": int((~canonical).sum()) == 0,
            "collector_c_coverage_120": int(present_c.sum()) == 120,
            "route_b_coverage_at_least_119": int(present_b.sum()) >= 119,
            "two_source_minutes_at_least_119": int(both.sum()) >= 119,
            "paper_bars_120": int(consumer.get("observed_bars", -1)) == 120,
            "fault_injection_performed": bool(collector.get("fault_injection_performed")),
            "production_orders_zero": int(collector.get("production_exchange_orders", -1)) == 0,
        }
        summary = {
            "status": "PASSED" if all(checks.values()) else "BLOCKED",
            "experiment_id": experiment_id,
            "experiment_path": str(experiment),
            "checks": checks,
            "canonical_bars": int(canonical.sum()),
            "canonical_missing": int((~canonical).sum()),
            "route_b_minutes": int(present_b.sum()),
            "collector_c_minutes": int(present_c.sum()),
            "two_source_minutes": int(both.sum()),
            "one_source_minutes": int((present_b ^ present_c).sum()),
            "zero_source_minutes": int((~present_b & ~present_c).sum()),
            "route_b_reconnects": int(collector["reconnects"]["ROUTE_B"]),
            "collector_c_reconnects": int(collector["reconnects"]["ROUTE_C"]),
            "new_24h_started": False,
        }
        (AUDIT / "bc_redundancy_qualification.json").write_text(json.dumps(summary, indent=2) + "\n")
        write_status(**summary)
        return 0 if summary["status"] == "PASSED" else 2
    except Exception as exc:
        write_status(
            status="BLOCKED", experiment_id=experiment_id, experiment_path=str(experiment),
            exception_type=type(exc).__name__, exception_message=str(exc),
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
