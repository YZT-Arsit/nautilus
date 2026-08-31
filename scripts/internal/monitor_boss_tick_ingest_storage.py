#!/usr/bin/env python3
"""Persist peak concurrent temporary storage for the streaming tick ingest."""

from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "outputs/baseline_evaluation/boss_multitimeframe_tick_screen/ingest_storage_monitor.json"
TEMP = ROOT / "outputs/tmp_tick_ingest"
STATE = ROOT / "outputs/baseline_evaluation/boss_multitimeframe_tick_screen/tick_execution_index_state"


def size() -> int:
    return sum(path.stat().st_size for path in TEMP.rglob("*") if path.is_file()) if TEMP.is_dir() else 0


def write(value: dict) -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    temporary = OUTPUT.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, OUTPUT)


def main() -> int:
    prior = json.loads(OUTPUT.read_text(encoding="utf-8")) if OUTPUT.is_file() else {}
    peak = int(prior.get("peak_concurrent_temporary_bytes", 0))
    minimum_free = int(prior.get("minimum_free_bytes", shutil.disk_usage(ROOT).free))
    while True:
        current = size()
        free = shutil.disk_usage(ROOT).free
        peak = max(peak, current)
        minimum_free = min(minimum_free, free)
        completed = len(list(STATE.glob("symbol=*/date=*.json"))) if STATE.is_dir() else 0
        status = "PASSED" if completed == 9 * 729 else "RUNNING"
        write(
            {
                "status": status,
                "completed_partitions": completed,
                "planned_partitions": 9 * 729,
                "current_temporary_bytes": current,
                "peak_concurrent_temporary_bytes": peak,
                "current_free_bytes": free,
                "minimum_free_bytes": minimum_free,
            }
        )
        if status == "PASSED":
            return 0
        time.sleep(5)


if __name__ == "__main__":
    raise SystemExit(main())
