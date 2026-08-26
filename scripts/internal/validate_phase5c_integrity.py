#!/usr/bin/env python3
"""Prove Phase 5C did not mutate Phase 5B or earlier protected artifacts."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
AUDIT = ROOT / "outputs/internal_audit/strategy_workbook"
ALLOWED_CHANGED = {
    "strategy_framework/workbook_dsl.py", "strategy_framework/registry.py",
    "scripts/internal/generate_workbook_strategy_packages.py",
    "scripts/internal/materialize_phase5a_equivalence.py",
    "outputs/internal_audit/strategy_workbook/strategy_workbook_conversion_manifest.csv",
    "outputs/internal_audit/strategy_workbook/registered_strategy_manifest.csv",
    "outputs/internal_audit/strategy_workbook/parameter_search_manifest.csv",
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--before", type=Path, default=AUDIT / "phase5b_protected_hashes_after.json")
    parser.add_argument("--after", type=Path, default=AUDIT / "phase5c_protected_hashes_after.json")
    parser.add_argument("--output", type=Path, default=AUDIT / "phase5c_integrity_validation.json")
    args = parser.parse_args()
    before = json.loads(args.before.read_text(encoding="utf-8-sig")); after = json.loads(args.after.read_text(encoding="utf-8-sig"))
    old, new = before["content"]["files"], after["content"]["files"]
    missing = sorted(set(old) - set(new))
    changed = sorted(path for path in set(old) & set(new) if old[path] != new[path])
    unexpected = sorted((set(missing) | set(changed)) - ALLOWED_CHANGED)
    old_results = sorted(path for path in old if path.startswith("outputs/batches/workbook_strategies_phase5") and path.endswith(("summary.json", "timeseries.parquet", "execution_events.csv")))
    result_changes = sorted(path for path in old_results if path not in new or old[path] != new[path])
    strategy_changes = sorted(path for path in changed if path.startswith("strategies/xlsx_"))
    payload = {"baseline": str(args.before), "allowed_changes": sorted(set(changed) & ALLOWED_CHANGED),
               "missing_protected_files": missing, "unexpected_protected_changes": unexpected,
               "protected_phase5_result_files_checked": len(old_results), "protected_phase5_result_hash_changes": result_changes,
               "existing_strategy_package_changes": strategy_changes,
               "market_and_feature_inventory_unchanged": before["data_inventories"] == after["data_inventories"],
               "protected_hash_change_count": len(unexpected) + len(result_changes) + len(strategy_changes)}
    payload["passed"] = not missing and not unexpected and not result_changes and not strategy_changes and payload["market_and_feature_inventory_unchanged"]
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
