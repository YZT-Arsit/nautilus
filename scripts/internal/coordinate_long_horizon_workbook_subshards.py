#!/usr/bin/env python3
"""Bridge accelerated subshards back to the original completion gate."""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path


def read(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {"status": "MISSING"}


def atomic_json(path: Path, payload: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    children = [args.root / f"matrix_progress_BTCUSDT_shard_{i}_of_12.json" for i in (1, 5, 9)]
    parent = args.root / "matrix_progress_BTCUSDT_shard_1_of_4.json"
    while True:
        values = [read(path) for path in children]
        statuses = [value.get("status") for value in values]
        if any(status == "COMPLETED_WITH_FAILURES" for status in statuses):
            atomic_json(parent, {
                "status": "COMPLETED_WITH_FAILURES", "symbol": "BTCUSDT",
                "logical_planned": 201, "logical_completed": 67 + sum(int(v.get("logical_completed", 0)) for v in values),
                "logical_failures": sum(int(v.get("logical_failures", 0)) for v in values),
                "completion_mode": "ACCELERATED_SUBSHARDS_1_5_9_OF_12",
            })
            return 2
        if all(status == "PASSED" for status in statuses):
            planned = sum(int(value["logical_planned"]) for value in values)
            completed = sum(int(value["logical_completed"]) for value in values)
            failures = sum(int(value["logical_failures"]) for value in values)
            if planned != 134 or completed != 134 or failures:
                raise ValueError(f"unexpected subshard totals planned={planned} completed={completed} failures={failures}")
            atomic_json(parent, {
                "status": "PASSED", "symbol": "BTCUSDT", "logical_planned": 201,
                "logical_completed": 201, "logical_failures": 0,
                "physical_runs_this_process": 90,
                "semantic_groups": 30,
                "completion_mode": "ACCELERATED_SUBSHARDS_1_5_9_OF_12",
            })
            return 0
        time.sleep(30)


if __name__ == "__main__":
    raise SystemExit(main())
