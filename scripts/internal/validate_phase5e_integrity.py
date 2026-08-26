#!/usr/bin/env python3
"""Verify Phase 5E is additive and all earlier phases remain hash-identical."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
AUDIT = ROOT / "outputs/internal_audit/strategy_workbook"
ALLOWED_CHANGED = {
    "strategy_framework/registry.py",
    "outputs/internal_audit/strategy_workbook/strategy_workbook_conversion_manifest.csv",
    "outputs/internal_audit/strategy_workbook/registered_strategy_manifest.csv",
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--before", type=Path, default=AUDIT / "phase5e_protected_hashes_before.json")
    parser.add_argument("--after", type=Path, default=AUDIT / "phase5e_protected_hashes_after.json")
    parser.add_argument("--output", type=Path, default=AUDIT / "phase5e_integrity_validation.json")
    args = parser.parse_args()
    before = json.loads(args.before.read_text(encoding="utf-8-sig"))
    after = json.loads(args.after.read_text(encoding="utf-8-sig"))
    old, new = before["content"]["files"], after["content"]["files"]
    missing = sorted(set(old) - set(new))
    changed = sorted(path for path in set(old) & set(new) if old[path] != new[path])
    unexpected = sorted((set(missing) | set(changed)) - ALLOWED_CHANGED)
    prior = sorted(path for path in old if path.startswith("outputs/") and any(
        token in path for token in ("phase2", "phase3", "phase4", "phase5a", "phase5b", "phase5c", "phase5d")
    ))
    prior_changes = sorted(path for path in prior if path not in new or old[path] != new[path])
    phase5d = sorted(path for path in old if "phase5d" in path)
    phase5d_changes = sorted(path for path in phase5d if path not in new or old[path] != new[path])
    existing_strategy_changes = sorted(path for path in changed if path.startswith("strategies/xlsx_"))
    payload = {
        "baseline": str(args.before), "allowed_changes": sorted(set(changed) & ALLOWED_CHANGED),
        "missing_protected_files": missing, "unexpected_protected_changes": unexpected,
        "prior_phase_artifacts_checked": len(prior), "prior_phase_artifact_changes": prior_changes,
        "phase5d_artifacts_checked": len(phase5d), "phase5d_artifact_changes": phase5d_changes,
        "existing_strategy_package_changes": existing_strategy_changes,
        "market_and_feature_inventory_unchanged": before["data_inventories"] == after["data_inventories"],
    }
    payload["protected_artifact_hash_changes"] = len(set(unexpected + prior_changes + phase5d_changes + existing_strategy_changes))
    payload["passed"] = (not missing and not unexpected and not prior_changes and not phase5d_changes
                         and not existing_strategy_changes and payload["market_and_feature_inventory_unchanged"])
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
