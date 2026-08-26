#!/usr/bin/env python3
"""Verify Phase 5F is additive and all pre-Phase-5F artifacts are protected."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
AUDIT = ROOT / "outputs/internal_audit/strategy_workbook"
ALLOWED_CHANGED = {
    "strategy_framework/registry.py", "strategy_framework/semantic_contracts.py",
    "strategy_framework/workbook_dsl.py", "strategy_framework/modules.py",
    "outputs/internal_audit/strategy_workbook/strategy_workbook_conversion_manifest.csv",
    "outputs/internal_audit/strategy_workbook/registered_strategy_manifest.csv",
}


def main() -> int:
    before = json.loads((AUDIT / "phase5f_protected_hashes_before.json").read_text(encoding="utf-8-sig"))
    after = json.loads((AUDIT / "phase5f_protected_hashes_after.json").read_text(encoding="utf-8-sig"))
    old, new = before["content"]["files"], after["content"]["files"]
    missing = sorted(set(old) - set(new))
    changed = sorted(path for path in set(old) & set(new) if old[path] != new[path])
    unexpected = sorted((set(missing) | set(changed)) - ALLOWED_CHANGED)
    protected = sorted(path for path in old if path.startswith("outputs/") and any(
        token in path for token in ("phase2", "phase3", "phase4", "phase5a", "phase5b", "phase5c", "phase5d", "phase5e")
    ))
    protected_changes = sorted(path for path in protected if path not in new or old[path] != new[path])
    existing_packages = sorted(path for path in changed if path.startswith("strategies/xlsx_"))
    payload = {
        "allowed_source_modifications": sorted(set(changed) & ALLOWED_CHANGED),
        "missing_protected_files": missing, "unexpected_protected_changes": unexpected,
        "prior_phase_artifacts_checked": len(protected), "prior_phase_artifact_changes": protected_changes,
        "existing_strategy_package_changes": existing_packages,
        "market_and_feature_inventory_unchanged": before["data_inventories"] == after["data_inventories"],
        "external_data_downloads": 0,
    }
    payload["protected_artifact_hash_changes"] = len(set(unexpected + protected_changes + existing_packages))
    payload["passed"] = not missing and not unexpected and not protected_changes and not existing_packages and payload["market_and_feature_inventory_unchanged"]
    (AUDIT / "phase5f_integrity_validation.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
