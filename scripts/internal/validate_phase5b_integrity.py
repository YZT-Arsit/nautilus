#!/usr/bin/env python3
"""Validate Phase 5B as an additive change against the frozen server baseline."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
AUDIT = ROOT / "outputs/internal_audit/strategy_workbook"
ALLOWED_CHANGED = {
    "strategy_framework/registry.py", "strategies/workbook_parametric/config.py",
    "strategies/workbook_parametric/plugin.py", "strategies/workbook_parametric/strategy.py",
    "feature_engine/api.py", "feature_engine/builders.py", "feature_engine/compute/backend.py",
    "feature_engine/compute/feature_lib/__init__.py",
    "feature_engine/compute/feature_lib/session.py",
    "scripts/internal/audit_strategy_workbook.py",
    "scripts/internal/materialize_phase5a_equivalence.py",
    "outputs/internal_audit/strategy_workbook/strategy_workbook_conversion_manifest.csv",
    "outputs/internal_audit/strategy_workbook/registered_strategy_manifest.csv",
    "outputs/internal_audit/strategy_workbook/parameter_search_manifest.csv",
}


def main() -> int:
    parser=argparse.ArgumentParser(); parser.add_argument("--before",type=Path,default=AUDIT/"phase5b_protected_hashes_before.json"); parser.add_argument("--after",type=Path,default=AUDIT/"phase5b_protected_hashes_after.json"); parser.add_argument("--output",type=Path,default=AUDIT/"phase5b_integrity_validation.json"); args=parser.parse_args()
    before=json.loads(args.before.read_text(encoding="utf-8-sig")); after=json.loads(args.after.read_text(encoding="utf-8-sig"))
    old,new=before["content"]["files"],after["content"]["files"]
    missing=sorted(set(old)-set(new)); changed=sorted(p for p in set(old)&set(new) if old[p]!=new[p]); unexpected=sorted((set(missing)|set(changed))-ALLOWED_CHANGED)
    phase5a_changed=[
        p for p in changed
        if p not in ALLOWED_CHANGED
        and ("phase5a" in p.lower() or p.startswith("strategies/xlsx_"))
    ]
    data_equal=before["data_inventories"]==after["data_inventories"]
    phase5a_results=sorted(p for p in old if p.startswith("outputs/batches/workbook_strategies_phase5a/") and p.endswith(("summary.json","timeseries.parquet","execution_events.csv")))
    phase5a_result_changes=sorted(p for p in phase5a_results if p not in new or old[p]!=new[p])
    representative_ids=sorted({p.split("/")[3] for p in phase5a_results})[:3]
    representative_regression=[]
    for strategy_id in representative_ids:
        paths=[p for p in phase5a_results if p.split("/")[3]==strategy_id]
        unchanged=all(p in new and old[p]==new[p] for p in paths)
        representative_regression.append({
            "strategy_id":strategy_id,
            "signals_unchanged":unchanged and any(p.endswith("execution_events.csv") for p in paths),
            "positions_unchanged":unchanged and any(p.endswith("timeseries.parquet") for p in paths),
            "metrics_unchanged":unchanged and any(p.endswith("summary.json") for p in paths),
            "protected_files":len(paths),
        })
    payload={"allowed_runtime_or_manifest_changes":sorted(set(changed)&ALLOWED_CHANGED),"missing_protected_files":missing,"unexpected_protected_changes":unexpected,"phase5a_or_prior_strategy_changes":phase5a_changed,"phase5a_result_files_checked":len(phase5a_results),"phase5a_result_hash_changes":phase5a_result_changes,"representative_existing_strategy_regression":representative_regression,"existing_strategy_regression_failures":sum(not all((r["signals_unchanged"],r["positions_unchanged"],r["metrics_unchanged"])) for r in representative_regression),"market_and_feature_inventory_unchanged":data_equal,"passed":not missing and not unexpected and not phase5a_changed and not phase5a_result_changes and data_equal}
    args.output.write_text(json.dumps(payload,indent=2)+"\n",encoding="utf-8"); print(json.dumps(payload,indent=2)); return 0 if payload["passed"] else 1


if __name__=="__main__": raise SystemExit(main())
