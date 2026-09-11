#!/usr/bin/env python3
"""Aggregate full-universe discovery shards and freeze reverse candidates."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = Path("outputs/baseline_evaluation/reverse_clean_validation")
SYMBOLS = (
    "XRPUSDT", "DOGEUSDT", "SUIUSDT", "BNBUSDT", "ETHUSDT",
    "BTCUSDT", "1000PEPEUSDT", "SOLUSDT", "ADAUSDT",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    frame.to_csv(temp, index=False)
    os.replace(temp, path)


def atomic_json(value: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    os.replace(temp, path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=ROOT)
    args = parser.parse_args()
    repo = args.repo.resolve()
    output = repo / OUTPUT
    frames = []
    for symbol in SYMBOLS:
        root = output / "discovery/shards" / symbol
        summary = json.loads((root / "run_summary.json").read_text())
        if summary["status"] != "PASSED" or summary["failures"] != 0:
            raise ValueError(f"discovery shard failed: {symbol}: {summary}")
        frame = pd.read_csv(root / "all_discovery_metrics.csv")
        if len(frame) != 331 * 3:
            raise ValueError(f"incomplete logical shard {symbol}: {len(frame)}")
        frames.append(frame)
    manifest = pd.concat(frames, ignore_index=True)
    keys = ["strategy_id", "symbol", "timeframe"]
    if len(manifest) != 8937 or manifest[keys].duplicated().any():
        raise ValueError("full eligible case reconciliation failed")
    if manifest.strategy_id.nunique() != 331:
        raise ValueError("strategy ID reconciliation failed")
    if manifest.semantic_group_id.nunique() != 183:
        raise ValueError("semantic-group reconciliation failed")
    forbidden = [column for column in manifest if "holdout" in column.lower() or "validation" in column.lower()]
    if forbidden:
        raise ValueError(f"holdout columns leaked into selection manifest: {forbidden}")
    manifest = manifest.sort_values(keys).reset_index(drop=True)
    path = output / "clean_reverse_candidate_manifest.csv"
    atomic_csv(manifest, path)
    digest = sha256(path)
    selected = manifest[manifest.reverse_selected.astype(str).str.lower().eq("true")]
    freeze = {
        "status": "FROZEN_BEFORE_FORWARD_PERFORMANCE",
        "full_eligible_cases_audited": len(manifest),
        "strategy_ids_audited": int(manifest.strategy_id.nunique()),
        "independent_semantic_groups_audited": int(manifest.semantic_group_id.nunique()),
        "discovery_only_reverse_candidates": len(selected),
        "candidate_strategy_ids": int(selected.strategy_id.nunique()),
        "candidate_independent_semantic_groups": int(selected.semantic_group_id.nunique()),
        "discovery_start": "2024-07-01",
        "discovery_end": "2025-07-01",
        "candidate_manifest_sha256": digest,
        "holdout_metrics_read_during_selection": 0,
        "threshold_changes": 0,
    }
    atomic_json(freeze, output / "candidate_manifest_freeze.json")
    print(json.dumps(freeze, indent=2))


if __name__ == "__main__":
    main()
