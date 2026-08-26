#!/usr/bin/env python3
"""Compare Phase 5A before/after protection snapshots with a narrow allowlist."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
AUDIT = ROOT / "outputs/internal_audit/strategy_workbook"
ALLOWED_CHANGED = {
    "strategies/workbook_parametric/config.py",
    "strategies/workbook_parametric/plugin.py",
    "strategies/workbook_parametric/strategy.py",
    "outputs/internal_audit/strategy_workbook/strategy_workbook_conversion_manifest.csv",
    "outputs/internal_audit/strategy_workbook/registered_strategy_manifest.csv",
    "outputs/internal_audit/strategy_workbook/parameter_search_manifest.csv",
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--before", type=Path, default=AUDIT / "phase5a_protected_hashes_before.json")
    parser.add_argument("--after", type=Path, default=AUDIT / "phase5a_protected_hashes_after.json")
    parser.add_argument("--output", type=Path, default=AUDIT / "phase5a_integrity_validation.json")
    args = parser.parse_args()
    before = json.loads(args.before.read_text(encoding="utf-8")); after = json.loads(args.after.read_text(encoding="utf-8"))
    old = before["content"]["files"]; new = after["content"]["files"]
    missing = sorted(set(old) - set(new))
    changed = sorted(path for path in set(old) & set(new) if old[path] != new[path])
    unexpected = sorted((set(missing) | set(changed)) - ALLOWED_CHANGED)
    data_equal = before["data_inventories"] == after["data_inventories"]
    payload = {
        "allowed_shared_or_manifest_changes": sorted(set(changed) & ALLOWED_CHANGED),
        "missing_protected_files": missing, "unexpected_protected_changes": unexpected,
        "market_and_feature_inventory_unchanged": data_equal,
        "protected_artifact_changes": len(unexpected),
        "passed": not unexpected and not missing and data_equal,
    }
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2))
    return 0 if payload["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
