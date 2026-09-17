#!/usr/bin/env python3
"""Run frozen partial-window maker shards and preserve auditable logs/status."""

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
    parser.add_argument("--research-root", type=Path, required=True)
    parser.add_argument("--market-root", type=Path, required=True)
    parser.add_argument("--acquisition-manifest", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--shards", type=int, default=14)
    parser.add_argument("--shard-indices", type=int, nargs="*")
    parser.add_argument("--status-name", default="orchestrator_status.json")
    args = parser.parse_args()
    args.output_root.mkdir(parents=True, exist_ok=True)
    log_root = args.output_root / "logs"
    log_root.mkdir(exist_ok=True)
    worker = args.repo / "scripts/internal/run_selected_partial_window_maker.py"
    processes: list[tuple[int, subprocess.Popen, object, object]] = []
    shard_indices = args.shard_indices if args.shard_indices is not None else list(range(args.shards))
    for shard in shard_indices:
        stdout = (log_root / f"shard_{shard:02d}.out.log").open("w", encoding="utf-8")
        stderr = (log_root / f"shard_{shard:02d}.err.log").open("w", encoding="utf-8")
        command = [
            sys.executable, str(worker), "--repo", str(args.repo),
            "--research-root", str(args.research_root), "--market-root", str(args.market_root),
            "--acquisition-manifest", str(args.acquisition_manifest),
            "--output-root", str(args.output_root), "--shard-count", str(args.shards),
            "--shard-index", str(shard),
        ]
        process = subprocess.Popen(command, stdout=stdout, stderr=stderr)
        processes.append((shard, process, stdout, stderr))
    status_path = args.output_root / args.status_name
    while True:
        running = sum(process.poll() is None for _, process, _, _ in processes)
        payload = {
            "status": "RUNNING" if running else "FINISHED",
            "shards": args.shards,
            "shard_indices": shard_indices,
            "running": running,
            "return_codes": {str(shard): process.poll() for shard, process, _, _ in processes},
        }
        status_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        if running == 0:
            break
        time.sleep(15)
    for _, _, stdout, stderr in processes:
        stdout.close(); stderr.close()
    failures = {shard: process.returncode for shard, process, _, _ in processes if process.returncode != 0}
    final = {
        "status": "PASSED" if not failures else "FAILED",
        "shards": args.shards,
        "shard_indices": shard_indices,
        "failures": failures,
    }
    status_path.write_text(json.dumps(final, indent=2), encoding="utf-8")
    print(json.dumps(final, indent=2))
    return 0 if not failures else 2


if __name__ == "__main__":
    raise SystemExit(main())
