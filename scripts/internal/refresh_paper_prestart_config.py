#!/usr/bin/env python3
"""Refresh generated paper config while the experiment is still pre-start."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import yaml


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--experiment", type=Path, required=True)
    parser.add_argument("--code-commit")
    args = parser.parse_args()
    freeze_path = args.experiment / "manifest/paper_experiment_freeze.json"
    freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
    if freeze.get("forward_start_timestamp") is not None:
        raise RuntimeError("cannot refresh config after forward start")
    config = yaml.safe_load(args.source.read_text(encoding="utf-8"))
    config["experiment_id"] = freeze["experiment_id"]
    config["candidate_manifest"] = str(args.experiment / "manifest/paper_candidate_manifest_9symbols.csv")
    audit = json.loads((args.experiment / "manifest/manifest_audit.json").read_text(encoding="utf-8"))
    config["symbols"] = sorted(audit["counts_by_symbol"])
    config["candidate_count"] = audit["frozen_candidate_variants"]
    path = args.experiment / "manifest/paper_trading_v1.resolved.yaml"
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    freeze["paper_config_hash"] = hashlib.sha256(path.read_bytes()).hexdigest()
    freeze["code_commit"] = str(args.code_commit or freeze.get("code_commit", "")).strip()
    freeze_path.write_text(json.dumps(freeze, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
