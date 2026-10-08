#!/usr/bin/env python3
"""Deterministically validate persist-before-forward semantics."""

from __future__ import annotations

import argparse
import json
import tempfile
import time
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_durable_remote_collector import DurableRawWal


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    checks = {}
    with tempfile.TemporaryDirectory() as temporary:
        wal = DurableRawWal(Path(temporary))
        wal.last_sync = time.monotonic()
        row, committed = wal.append('{"stream":"test","data":{}}', time.time_ns())
        sequence = int(row["collector_sequence"])
        checks["append_does_not_imply_commit"] = not committed and not wal.is_committed(sequence)
        checks["checkpoint_not_advanced_before_fsync"] = wal.committed_sequence == 0
        wal.sync()
        checkpoint = json.loads(wal.commit.read_text())
        checks["sequence_promoted_after_fsync"] = wal.is_committed(sequence)
        checks["checkpoint_matches_committed_sequence"] = int(checkpoint["committed_sequence"]) == sequence
        checks["checkpoint_contains_committed_offset"] = int(checkpoint["committed_offset"]) > 0
        wal.close()
    result = {
        "status": "PASSED" if all(checks.values()) else "BLOCKED",
        "checks": checks,
        "invariant": "streamer_emits_only_sequence_lte_fsynced_committed_sequence",
        "production_exchange_orders": 0,
    }
    path = args.output / "collector_c_durability_validation.json"
    path.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    return 0 if result["status"] == "PASSED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
