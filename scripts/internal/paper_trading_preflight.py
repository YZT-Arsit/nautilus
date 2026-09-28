#!/usr/bin/env python3
"""
Read-only safety/config preflight for paper-trading v1.

This command opens no network connection.  It proves the frozen manifest and
resolved config agree and that no live-order or credential path is enabled.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd
import yaml

from strategy_framework.registry import get_entry


ROOT = Path(__file__).resolve().parents[2]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tree_hash(path: Path) -> str:
    digest = hashlib.sha256()
    for item in sorted(path.rglob("*.py")):
        digest.update(item.relative_to(path).as_posix().encode())
        digest.update(item.read_bytes())
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repo", type=Path, default=ROOT)
    args = parser.parse_args()
    root = args.manifest_dir.resolve()
    manifest = root / "paper_candidate_manifest.csv"
    expected_hash = (root / "paper_candidate_manifest.sha256").read_text().strip()
    freeze = json.loads((root / "paper_experiment_freeze.json").read_text())
    config_path = root / "paper_trading_v1.resolved.yaml"
    config = yaml.safe_load(config_path.read_text())
    candidates = pd.read_csv(manifest)
    code_mismatches = 0
    config_mismatches = 0
    for row in candidates.drop_duplicates("strategy_id").itertuples(index=False):
        entry = get_entry(str(row.strategy_id))
        if tree_hash(args.repo / "strategies" / str(row.strategy_id)) != str(row.strategy_code_hash):
            code_mismatches += 1
        if sha256(args.repo / str(entry.default_config_path)) != str(row.config_hash):
            config_mismatches += 1
    checks = [
        ("manifest_hash", sha256(manifest) == expected_hash, f"expected={expected_hash}"),
        ("freeze_hash", freeze["candidate_manifest_hash"] == expected_hash, "freeze matches manifest"),
        ("resolved_config_hash", sha256(config_path) == freeze["paper_config_hash"], f"expected={freeze['paper_config_hash']}"),
        ("strategy_code_hashes", code_mismatches == 0, f"mismatches={code_mismatches}"),
        ("strategy_config_hashes", config_mismatches == 0, f"mismatches={config_mismatches}"),
        ("order_submission", config["order_submission"] == "DISABLED", str(config["order_submission"])),
        ("production_order_endpoint", config["safety"]["production_order_endpoint"] == "DISABLED", str(config["safety"]["production_order_endpoint"])),
        ("live_execution_adapter", config["safety"]["live_execution_adapter"] == "NONE", str(config["safety"]["live_execution_adapter"])),
        ("credentials_required", config["credentials_required"] is False, str(config["credentials_required"])),
        ("api_key_forbidden", config["safety"]["api_key"] == "FORBIDDEN", "no key configured"),
        ("api_secret_forbidden", config["safety"]["api_secret"] == "FORBIDDEN", "no secret configured"),  # noqa: S105
        ("market_data_mode", config["market_data_environment"] == "PRODUCTION_READ_ONLY", str(config["market_data_environment"])),
        ("candidate_count", len(candidates) == int(config["candidate_count"]), f"rows={len(candidates)}"),
    ]
    frame = pd.DataFrame(checks, columns=["check", "passed", "detail"])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(args.output, index=False)
    return 0 if frame.passed.all() else 2


if __name__ == "__main__":
    raise SystemExit(main())
