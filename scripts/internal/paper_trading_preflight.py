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


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.manifest_dir.resolve()
    manifest = root / "paper_candidate_manifest.csv"
    expected_hash = (root / "paper_candidate_manifest.sha256").read_text().strip()
    freeze = json.loads((root / "paper_experiment_freeze.json").read_text())
    config_path = root / "paper_trading_v1.resolved.yaml"
    config = yaml.safe_load(config_path.read_text())
    checks = [
        ("manifest_hash", sha256(manifest) == expected_hash, f"expected={expected_hash}"),
        ("freeze_hash", freeze["candidate_manifest_hash"] == expected_hash, "freeze matches manifest"),
        ("order_submission", config["order_submission"] == "DISABLED", str(config["order_submission"])),
        ("production_order_endpoint", config["safety"]["production_order_endpoint"] == "DISABLED", str(config["safety"]["production_order_endpoint"])),
        ("live_execution_adapter", config["safety"]["live_execution_adapter"] == "NONE", str(config["safety"]["live_execution_adapter"])),
        ("credentials_required", config["credentials_required"] is False, str(config["credentials_required"])),
        ("api_key_forbidden", config["safety"]["api_key"] == "FORBIDDEN", "no key configured"),
        ("api_secret_forbidden", config["safety"]["api_secret"] == "FORBIDDEN", "no secret configured"),  # noqa: S105
        ("market_data_mode", config["market_data_environment"] == "PRODUCTION_READ_ONLY", str(config["market_data_environment"])),
        ("candidate_count", len(pd.read_csv(manifest)) == int(config["candidate_count"]), f"rows={len(pd.read_csv(manifest))}"),
    ]
    frame = pd.DataFrame(checks, columns=["check", "passed", "detail"])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(args.output, index=False)
    return 0 if frame.passed.all() else 2


if __name__ == "__main__":
    raise SystemExit(main())
