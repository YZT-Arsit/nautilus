#!/usr/bin/env python3
"""Replay all symbol workers and aggregate deterministic validation."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import subprocess
import sys
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=ROOT)
    parser.add_argument("--experiment", type=Path, required=True)
    parser.add_argument("--phase-root", type=Path)
    parser.add_argument("--max-workers", type=int, default=3)
    args = parser.parse_args()
    repo = args.repo.resolve()
    experiment = args.experiment.resolve()
    phase_root = (args.phase_root or experiment).resolve()
    worker_root = phase_root / "workers"
    symbols = sorted(path.name for path in worker_root.iterdir() if path.is_dir())

    def run(symbol: str) -> dict:
        root = worker_root / symbol
        command = [
            sys.executable,
            str(repo / "scripts/internal/replay_paper_experiment.py"),
            "--repo", str(repo),
            "--experiment", str(experiment),
            "--phase-root", str(root),
        ]
        completed = subprocess.run(command, cwd=repo, text=True, capture_output=True, check=False)
        result_path = root / "replay/replay_validation.json"
        result = json.loads(result_path.read_text()) if result_path.exists() else {
            "status": "BLOCKED", "mismatch_count": -1,
        }
        return {
            "symbol": symbol,
            "exit_code": completed.returncode,
            "status": result.get("status", "BLOCKED"),
            "mismatch_count": int(result.get("mismatch_count", -1)),
            "replayed_events": int(result.get("replayed_events", 0)),
            "stderr": completed.stderr[-2000:],
        }

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.max_workers)) as pool:
        rows = list(pool.map(run, symbols))
    frame = pd.DataFrame(rows)
    frame.to_csv(phase_root / "replay_validation.csv", index=False)
    passed = bool(len(rows)) and all(row["status"] == "PASSED" and row["exit_code"] == 0 for row in rows)
    aggregate = {
        "status": "PASSED" if passed else "BLOCKED",
        "symbols": symbols,
        "mismatch_count": int(frame.mismatch_count.clip(lower=0).sum()),
        "replayed_events": int(frame.replayed_events.sum()),
    }
    (phase_root / "replay_validation.json").write_text(json.dumps(aggregate, indent=2) + "\n")
    print(json.dumps(aggregate, indent=2))
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
