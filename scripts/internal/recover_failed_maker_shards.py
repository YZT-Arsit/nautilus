#!/usr/bin/env python3
"""Recover missing maker shards with bounded concurrency.

This scheduler does not change any research inputs or execution semantics. It
waits for the original orchestrator to finish, then runs only shards whose
final metric/mapping artifacts are absent. Recovery runs at two shards at a
time and falls back to one shard at a time if a pair does not finish cleanly.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path


def read_status(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def complete(output_root: Path, shard: int, shard_count: int) -> bool:
    metrics = output_root / f"maker_metrics_shard_{shard}_of_{shard_count}.csv"
    mapping = output_root / f"maker_case_mapping_shard_{shard}_of_{shard_count}.csv"
    progress = output_root / f"progress_shard_{shard}_of_{shard_count}.json"
    return metrics.is_file() and mapping.is_file() and read_status(progress).get("status") == "PASSED"


def atomic_json(path: Path, payload: dict) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    temp.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--research-root", type=Path, required=True)
    parser.add_argument("--market-root", type=Path, required=True)
    parser.add_argument("--acquisition-manifest", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--shards", type=int, default=8)
    parser.add_argument("--poll-seconds", type=int, default=30)
    args = parser.parse_args()

    status_path = args.output_root / "recovery_status.json"
    original_status = args.output_root / "orchestrator_status.json"
    atomic_json(status_path, {"status": "WAITING_FOR_ORIGINAL", "shards": args.shards})
    while read_status(original_status).get("status") == "RUNNING":
        time.sleep(args.poll_seconds)

    orchestrator = args.repo / "scripts/internal/orchestrate_selected_partial_window_maker.py"

    def run(indices: list[int], label: str) -> int:
        atomic_json(status_path, {
            "status": "RUNNING_RECOVERY",
            "indices": indices,
            "label": label,
        })
        command = [
            sys.executable,
            str(orchestrator),
            "--repo", str(args.repo),
            "--research-root", str(args.research_root),
            "--market-root", str(args.market_root),
            "--acquisition-manifest", str(args.acquisition_manifest),
            "--output-root", str(args.output_root),
            "--shards", str(args.shards),
            "--shard-indices", *[str(index) for index in indices],
            "--status-name", f"recovery_{label}.json",
        ]
        return subprocess.run(command, check=False).returncode

    missing = [index for index in range(args.shards) if not complete(args.output_root, index, args.shards)]
    for offset in range(0, len(missing), 2):
        pair = missing[offset:offset + 2]
        run(pair, "pair_" + "_".join(map(str, pair)))
        still_missing = [index for index in pair if not complete(args.output_root, index, args.shards)]
        for index in still_missing:
            run([index], f"single_{index}")

    final_missing = [index for index in range(args.shards) if not complete(args.output_root, index, args.shards)]
    final = {
        "status": "PASSED" if not final_missing else "FAILED",
        "shards": args.shards,
        "missing_shards": final_missing,
    }
    atomic_json(status_path, final)
    print(json.dumps(final, indent=2))
    return 0 if not final_missing else 2


if __name__ == "__main__":
    raise SystemExit(main())
