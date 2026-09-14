#!/usr/bin/env python3
"""Resume the five-year study after already-running NORMAL shards complete."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path


def read_status(path: Path) -> str:
    try:
        return str(json.loads(path.read_text(encoding="utf-8-sig"))["status"])
    except (FileNotFoundError, KeyError, json.JSONDecodeError):
        return "MISSING"


def run(command: list[str], log: Path) -> None:
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("w", encoding="utf-8") as handle:
        result = subprocess.run(command, stdout=handle, stderr=subprocess.STDOUT, check=False)
    if result.returncode:
        raise RuntimeError(f"command failed ({result.returncode}), see {log}: {command}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--delivery-root", type=Path, required=True)
    parser.add_argument("--market-root", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, required=True)
    args = parser.parse_args()
    python = Path(sys.executable)
    scripts = args.repo_root / "scripts/internal"
    normal_progress = [
        *(args.root / f"matrix_progress_BTCUSDT_shard_{i}_of_4.json" for i in range(4)),
        *(args.root / "pre_workbook" / f"progress_BTCUSDT_shard_{i}_of_2.json" for i in range(2)),
    ]
    state_path = args.root / "completion_orchestrator_status.json"
    while True:
        statuses = {str(path): read_status(path) for path in normal_progress}
        if any(status == "COMPLETED_WITH_FAILURES" for status in statuses.values()):
            state_path.write_text(json.dumps({"status": "BLOCKED_NORMAL_FAILURE", "shards": statuses}, indent=2), encoding="utf-8")
            return 2
        if all(status == "PASSED" for status in statuses.values()):
            break
        state_path.write_text(json.dumps({"status": "WAITING_FOR_NORMAL", "shards": statuses}, indent=2), encoding="utf-8")
        time.sleep(30)

    state_path.write_text(json.dumps({"status": "FREEZING_SELECTION"}, indent=2), encoding="utf-8")
    run([
        str(python), str(scripts / "freeze_long_horizon_first_tick_selection.py"),
        "--root", str(args.root),
    ], args.root / "logs/freeze_selection.log")

    state_path.write_text(json.dumps({"status": "RUNNING_REVERSE"}, indent=2), encoding="utf-8")
    reverse_commands = [[
        str(python), str(scripts / "run_long_horizon_strict_reverse.py"),
        "--root", str(args.root), "--market-root", str(args.market_root),
        "--shard-count", "4", "--shard-index", str(index),
    ] for index in range(4)]
    processes = []
    for index, command in enumerate(reverse_commands):
        log = (args.root / f"logs/reverse_shard_{index}.log").open("w", encoding="utf-8")
        processes.append((subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT), log))
    failures = []
    for process, log in processes:
        code = process.wait()
        log.close()
        if code:
            failures.append(code)
    if failures:
        state_path.write_text(json.dumps({"status": "BLOCKED_REVERSE_FAILURE", "return_codes": failures}, indent=2), encoding="utf-8")
        return 3

    state_path.write_text(json.dumps({"status": "PACKAGING"}, indent=2), encoding="utf-8")
    run([
        str(python), str(scripts / "finalize_long_horizon_execution_reverse.py"),
        "--research-root", str(args.root), "--delivery-root", str(args.delivery_root),
    ], args.root / "logs/finalize.log")
    run([
        str(python), str(scripts / "validate_long_horizon_execution_reverse.py"),
        "--research-root", str(args.root), "--delivery-root", str(args.delivery_root),
    ], args.root / "logs/validate.log")
    state_path.write_text(json.dumps({"status": "SERVER_PASSED"}, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
