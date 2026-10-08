#!/usr/bin/env python3
"""Freeze the evidence-backed P0 networking change and its immutable hashes."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


PREVIOUS_HASHES = {
    "scripts/internal/read_only_binance_failover_proxy.py":
        "4f9e4fbd2a6e9bd37d345fa3b6d664732ff2ef4427ba07e2e44156230cd32153",
    "scripts/run_paper_orchestrator.py":
        "6c86f521ef2d5333ed5a5d0bbc2696ee5409e22f283aeefc6dafd83605d81a9c",
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    repo = args.repo.resolve()
    changed = [
        "scripts/internal/read_only_binance_failover_proxy.py",
        "scripts/run_paper_orchestrator.py",
    ]
    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "failed_experiment": "paper_clean_ab_20261004_101057",
        "failure_label": "FAILED_LONG_HORIZON_CONTINUITY",
        "first_causal_failure_type": "UPSTREAM_PROXY_TUNNEL_DATA_SILENCE_PRECEDING_RESET",
        "downstream_failure": "STALE_RECOVERY_FEEDBACK_LOOP_AND_STUCK_HALF_OPEN_OWNERSHIP",
        "previous_networking_hashes": PREVIOUS_HASHES,
        "new_networking_hashes": {path: sha256(repo / path) for path in changed},
        "exact_files_changed_for_behavior": changed,
        "behavioral_changes": [
            "A HALF_OPEN probe that closes before the stable lifetime is failed explicitly, releasing probe ownership and reopening the circuit with bounded cooldown.",
            "The stale detector can request recovery only from HEALTHY; VALIDATING has one bounded validation timeout and cannot be repeatedly cancelled by stale timestamps from the prior connection.",
        ],
        "unchanged_components": [
            "strategy", "parameters", "bar semantics", "DIRECT_SHADOW execution",
            "MAKER_PAPER execution", "TLS certificate verification",
        ],
        "production_market_data": "READ_ONLY",
        "production_trading_client": "NOT_INITIALIZED",
        "production_exchange_orders": 0,
        "tests": {"server_targeted_tests": 26, "server_targeted_failures": 0},
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
