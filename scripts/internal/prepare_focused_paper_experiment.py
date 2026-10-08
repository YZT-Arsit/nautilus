#!/usr/bin/env python3
"""Prepare a new one-candidate paper experiment without changing strategy/config."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

import pandas as pd


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--parent", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--experiment-id", required=True)
    parser.add_argument("--candidate-id", required=True)
    parser.add_argument("--purpose", required=True)
    args = parser.parse_args()
    parent = args.parent.resolve()
    output = args.output.resolve()
    repo = parent.parents[2]
    if output.exists():
        raise RuntimeError(f"refusing to overwrite existing experiment: {output}")
    source_manifest = parent / "manifest/paper_candidate_manifest_9symbols.csv"
    source_hash = (parent / "manifest/paper_candidate_manifest_9symbols.sha256").read_text().strip()
    if sha256(source_manifest) != source_hash:
        raise RuntimeError("parent frozen candidate manifest hash mismatch")
    frame = pd.read_csv(source_manifest)
    selected = frame.loc[frame.experiment_candidate_id.eq(args.candidate_id)].copy()
    if len(selected) != 1:
        raise RuntimeError(f"candidate must resolve to one row, got {len(selected)}")
    shutil.copytree(parent / "manifest", output / "manifest")
    scope = output / "manifest/focused_run_scope.csv"
    selected.to_csv(scope, index=False)
    scope_hash = sha256(scope)
    (output / "manifest/focused_run_scope.sha256").write_text(scope_hash + "\n", encoding="utf-8")
    freeze_path = output / "manifest/paper_experiment_freeze.json"
    freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
    freeze.update({
        "experiment_id": args.experiment_id,
        "status": "FROZEN_PRE_START",
        "forward_start_timestamp": None,
        "candidate_count": 1,
        "base_logical_case_count": 1,
        "focused_candidate_id": args.candidate_id,
        "focused_scope_hash": scope_hash,
        "parent_experiment_id": parent.name,
        "parent_manifest_hash": source_hash,
        "purpose": args.purpose,
        "production_market_data": "READ_ONLY",
        "production_trading_client": "NOT_INITIALIZED",
        "production_exchange_orders": 0,
        "order_submission": "DISABLED",
        "orchestration_files_sha256": {
            str(path.relative_to(repo)): sha256(path)
            for path in (
                repo / "scripts/run_paper_orchestrator.py",
                repo / "data_engine/live/binance_ws_client.py",
                repo / "scripts/internal/read_only_binance_failover_proxy.py",
                repo / "scripts/internal/run_focused_continuity.ps1",
                repo / "scripts/internal/run_continuity_gate_and_clean_ab.ps1",
                repo / "scripts/internal/run_p0_long_horizon_gate.ps1",
                repo / "scripts/run_active_active_collector.py",
                repo / "scripts/run_durable_remote_collector.py",
                repo / "scripts/run_canonical_paper_consumer.py",
                repo / "strategy_framework/paper_trading/active_active.py",
                repo / "scripts/internal/launch_active_active_phase.py",
                repo / "scripts/internal/run_active_active_delivery_pipeline.py",
                repo / "scripts/internal/probe_active_active_routes.py",
                repo / "scripts/internal/read_only_connect_proxy.py",
            )
        },
    })
    freeze_path.write_text(json.dumps(freeze, indent=2) + "\n", encoding="utf-8")
    for folder in (
        "market_data", "bars", "strategy_state", "decisions", "orders", "fills",
        "funding", "fees", "daily_metrics", "health", "figures", "audit_log",
    ):
        (output / folder).mkdir(parents=True, exist_ok=True)
    print(json.dumps({
        "experiment_id": args.experiment_id,
        "candidate_id": args.candidate_id,
        "parent_manifest_hash": source_hash,
        "focused_scope_hash": scope_hash,
        "output": str(output),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
