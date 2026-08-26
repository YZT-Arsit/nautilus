#!/usr/bin/env python3
"""Verify that Phase 5D changed no pre-existing protected artifact."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
AUDIT = ROOT / "outputs/internal_audit/strategy_workbook"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--before", type=Path, default=AUDIT / "phase5d_protected_hashes_before.json")
    parser.add_argument("--after", type=Path, default=AUDIT / "phase5d_protected_hashes_after.json")
    parser.add_argument("--output", type=Path, default=AUDIT / "phase5d_integrity_validation.json")
    args = parser.parse_args()
    before = json.loads(args.before.read_text(encoding="utf-8-sig"))
    after = json.loads(args.after.read_text(encoding="utf-8-sig"))
    old, new = before["content"]["files"], after["content"]["files"]
    missing = sorted(set(old) - set(new))
    changed = sorted(path for path in set(old) & set(new) if old[path] != new[path])
    prior_results = sorted(path for path in old if path.startswith("outputs/") and any(token in path for token in ("phase2", "phase3", "phase4", "phase5a", "phase5b", "phase5c")))
    prior_result_changes = sorted(path for path in prior_results if path not in new or old[path] != new[path])
    protected_runtime_changes = sorted(path for path in changed if path.startswith(("strategies/", "strategy_framework/", "feature_engine/", "data_engine/", "configs/semantic_contracts/")))
    payload = {
        "baseline": str(args.before),
        "missing_protected_files": missing,
        "changed_protected_files": changed,
        "prior_phase_artifacts_checked": len(prior_results),
        "prior_phase_artifact_changes": prior_result_changes,
        "runtime_or_contract_changes": protected_runtime_changes,
        "market_and_feature_inventory_unchanged": before["data_inventories"] == after["data_inventories"],
    }
    payload["protected_artifact_change_count"] = len(set(missing + changed + prior_result_changes + protected_runtime_changes))
    payload["passed"] = payload["protected_artifact_change_count"] == 0 and payload["market_and_feature_inventory_unchanged"]
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
