#!/usr/bin/env python3
"""Launch independent collector and paper consumer at one exact UTC boundary."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--experiment", type=Path, required=True)
    parser.add_argument("--candidate-id", required=True)
    parser.add_argument("--warmup-source", type=Path, required=True)
    parser.add_argument("--duration-seconds", type=int, required=True)
    parser.add_argument("--expected-bars", type=int, required=True)
    parser.add_argument("--inject-reset-route")
    parser.add_argument("--inject-at-seconds", type=float)
    parser.add_argument("--include-route-a", action="store_true")
    parser.add_argument("--collector-c", default="100.64.0.22:7892")
    args = parser.parse_args()
    repo = args.repo.resolve(); experiment = args.experiment.resolve()
    logs = experiment / "logs"; logs.mkdir(parents=True, exist_ok=True)
    active = experiment / "active_active"; active.mkdir(parents=True, exist_ok=True)
    # At least 20 seconds of connection warm-up, then an exact UTC minute.
    start_s = ((int(time.time()) + 79) // 60) * 60
    start_ns = start_s * 1_000_000_000
    end_ns = (start_s + args.duration_seconds) * 1_000_000_000
    timing = {
        "start_ns": start_ns, "end_ns": end_ns, "duration_seconds": args.duration_seconds,
        "expected_bars": args.expected_bars, "production_exchange_orders": 0,
    }
    (experiment / "active_active_timing.json").write_text(json.dumps(timing, indent=2) + "\n")
    collector_cmd = [
        sys.executable, str(repo / "scripts/run_active_active_collector.py"),
        "--output", str(active), "--symbol", "BTCUSDT", "--start-ns", str(start_ns),
        "--end-ns", str(end_ns), "--route", "ROUTE_B=100.64.0.5:7890",
        "--remote-source", f"ROUTE_C={args.collector_c}",
    ]
    if args.include_route_a:
        collector_cmd += ["--route", "ROUTE_A=100.64.0.6:7890"]
    if args.inject_reset_route:
        collector_cmd += ["--inject-reset-route", args.inject_reset_route,
                          "--inject-at-seconds", str(args.inject_at_seconds)]
    consumer_cmd = [
        sys.executable, str(repo / "scripts/run_canonical_paper_consumer.py"),
        "--repo", str(repo), "--experiment", str(experiment), "--wal-root", str(active / "canonical_wal"),
        "--start-ns", str(start_ns), "--end-ns", str(end_ns), "--expected-bars", str(args.expected_bars),
        "--candidate-id", args.candidate_id, "--warmup-source", str(args.warmup_source),
    ]
    with (logs / "active_active_collector.log").open("w") as collector_log, \
         (logs / "canonical_paper_consumer.log").open("w") as consumer_log:
        collector = subprocess.Popen(collector_cmd, cwd=repo, stdout=collector_log,
                                     stderr=subprocess.STDOUT)
        consumer = subprocess.Popen(consumer_cmd, cwd=repo, stdout=consumer_log,
                                    stderr=subprocess.STDOUT)
        collector_code = collector.wait()
        consumer_code = consumer.wait()
    result = {**timing, "collector_exit_code": collector_code,
              "consumer_exit_code": consumer_code}
    (experiment / "active_active_phase_result.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    return 0 if collector_code == 0 and consumer_code == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
