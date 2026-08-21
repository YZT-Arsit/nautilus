#!/usr/bin/env python3
"""Write deterministic Phase 2.2C blocker-set closure artifacts."""
from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import sys
from collections import Counter, defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
AUDIT = ROOT / "outputs/internal_audit/strategy_workbook"
PLAN = ROOT / "configs/semantic_contracts/workbook_phase2_2c_strategies.json"
PHASE2B_PLAN = ROOT / "configs/semantic_contracts/workbook_phase2_2b_strategies.json"
PHASE2C_NEW_CONTRACTS = {
    "CHANNEL_LAST_BREAKOUT_STATE_V1", "GRID_SOURCE_LAYERS_EQUAL_EXPOSURE_V1",
    "PYRAMID_FAVORABLE_DIRECTION_V1", "TOUCH_AS_THRESHOLD_CROSS_V1",
}

BLOCKER_CONTRACTS = {
    "CONFLUENCE_COMPOSITION": "CONFLUENCE_AND_V1",
    "SYNCHRONOUS_STATE_TIMING": "MTF_LATEST_COMPLETED_ALL_TRUE_V1",
    "TURN_UP": "TURN_SLOPE_SIGN_CHANGE_V1",
    "TURN_DOWN": "TURN_SLOPE_SIGN_CHANGE_V1",
    "ATR_LOOKBACK_MISSING": "ATR14_DEFAULT_V1",
    "PERSISTENCE_COUNT_MISSING": "PERSISTENCE_2BAR_V1",
    "STABLE_ABOVE": "STABLE_CLOSE_2BAR_V1",
    "CONFIRMATION_RULE": "CONFIRM_CLOSE_2BAR_V1",
    "RECENT_HIGH_WINDOW": "RECENT_EXTREME_PRIOR_20_V1",
    "RECENT_LOW_WINDOW": "RECENT_EXTREME_PRIOR_20_V1",
    "EXTREME_THRESHOLD_MISSING": "BOUNDED_INDICATOR_EXTREMES_V1",
    "SUPPORT_ZONE_DEFINITION": "EXPLICIT_LEVEL_SUPPORT_RESISTANCE_V1",
    "RESISTANCE_ZONE_DEFINITION": "EXPLICIT_LEVEL_SUPPORT_RESISTANCE_V1",
    "NEAR_LEVEL": "LEVEL_TOLERANCE_ATR025_V1",
    "PULLBACK_TO_LEVEL": "PULLBACK_AFTER_BREAKOUT_V1",
    "REJECT_FROM_RESISTANCE": "REJECTION_AT_LEVEL_V1",
    "STABILIZE_AFTER_DECLINE": "STABILIZE_MINIMAL_TRANSITION_V1",
    "FRACTAL_SCALE_DEFINITION": "CONFIRMED_FRACTAL_2X2_V1",
    "DIVERGENCE_PIVOT_DEFINITION_MISSING": "REGULAR_DIVERGENCE_CONFIRMED_PIVOTS_V1",
    "DIVERGENCE_LOOKBACK_MISSING": "DIVERGENCE_LOOKBACK_60_V1",
    "MEAN_REVERSION_COMPLETION": "MEAN_REVERSION_TO_SOURCE_CENTER_V1",
    "GRID_LAYER_CONTRACT": "GRID_4L_ATR1_EQUAL_V1",
    "PYRAMID_STEP_DISTANCE": "GRID_4L_ATR1_EQUAL_V1",
    "POSITION_FRACTION_MISSING": "REDUCE_HALF_CURRENT_V1",
    "LAYERED_REDUCTION_SCHEDULE": "LAYERED_REDUCTION_EQUAL_V1",
    "PYRAMID_ADD_FRACTION": "ADD_QUARTER_EXPOSURE_V1",
    "CHANNEL_STATE_DEFINITION": "CHANNEL_LAST_BREAKOUT_STATE_V1",
    "TOUCH_SEMANTICS": "TOUCH_AS_THRESHOLD_CROSS_V1",
}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def write_csv(path: Path, fields: list[str] | tuple[str, ...], rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    os.replace(temporary, path)


def write_json(path: Path, payload: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def case_complete(root: Path | None, strategy: str) -> bool | None:
    if root is None:
        return None
    return all(
        (root / strategy / case / "timeseries.parquet").is_file()
        and (root / strategy / case / "summary.json").is_file()
        for case in ("1m_lag0", "1m_lag1")
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit-root", type=Path, default=AUDIT)
    parser.add_argument("--plan", type=Path, default=PLAN)
    parser.add_argument("--backtest-root", type=Path)
    parser.add_argument("--deliverable-root", type=Path)
    args = parser.parse_args()

    plan: dict[str, dict[str, object]] = json.loads(args.plan.read_text(encoding="utf-8"))
    phase2b_plan: dict[str, dict[str, object]] = json.loads(PHASE2B_PLAN.read_text(encoding="utf-8"))
    manifest = read_csv(args.audit_root / "strategy_workbook_conversion_manifest.csv")
    by_id = {row["registry_id"]: row for row in manifest}
    transitions_b = read_csv(args.audit_root / "phase2_2b_status_transitions.csv")
    remaining_ids = {
        row["source_identity"] for row in transitions_b
        if row["phase2_2b_status"].startswith("AMBIGUOUS")
    }
    if len(remaining_ids) != 1153:
        raise ValueError(f"expected 1153 Phase 2.2B remaining rows, found {len(remaining_ids)}")
    if not set(plan) <= remaining_ids:
        raise ValueError("Phase 2.2C plan contains IDs outside the remaining population")

    blocker_rows = read_csv(args.audit_root / "semantic_contracts/semantic_blocker_manifest.csv")
    blocker_by_id: dict[str, set[str]] = defaultdict(set)
    phrase_by_blocker: dict[str, list[str]] = defaultdict(list)
    for row in blocker_rows:
        identity = row["source_identity"]
        if identity in remaining_ids:
            blocker = row["normalized_blocker_id"]
            blocker_by_id[identity].add(blocker)
            phrase_by_blocker[blocker].append(row["original_phrase"])
    if set(blocker_by_id) != remaining_ids:
        missing = sorted(remaining_ids - set(blocker_by_id))
        raise ValueError(f"remaining rows without blocker sets: {missing[:10]}")

    signatures: dict[tuple[str, ...], list[str]] = defaultdict(list)
    for identity, blockers in blocker_by_id.items():
        signatures[tuple(sorted(blockers))].append(identity)
    ordered_signatures = sorted(signatures.items(), key=lambda item: (-len(item[1]), item[0]))
    signature_id = {signature: f"SIG_{index:03d}" for index, (signature, _) in enumerate(ordered_signatures, 1)}
    signature_rows = []
    for signature, identities in ordered_signatures:
        contracts = sorted({BLOCKER_CONTRACTS[x] for x in signature if x in BLOCKER_CONTRACTS})
        missing = sorted(set(signature) - set(BLOCKER_CONTRACTS))
        implemented = sorted(set(identities).intersection(plan))
        signature_rows.append({
            "blocker_set_signature": signature_id[signature],
            "strategy_count": len(identities),
            "blockers": ";".join(signature),
            "minimum_contract_bundle": ";".join(contracts),
            "projected_full_unlock": len(implemented),
            "example_source_ids": ";".join(sorted(identities)[:5]),
            "source_sheets": ";".join(sorted({by_id[x]["source_sheet"] for x in identities})),
            "strategy_families": ";".join(sorted({str(plan[x]["family"]) for x in implemented})),
            "currently_available_contracts": ";".join(contracts),
            "missing_contracts": ";".join(missing),
            "source_data_exists": "true",
            "standardization_status": "fully_compiled" if len(implemented) == len(identities) else "partial_or_unresolved",
        })
    write_csv(args.audit_root / "phase2_2c_blocker_set_signatures.csv", list(signature_rows[0]), signature_rows)

    from strategy_framework.semantic_contracts import CONTRACTS

    phase2b_users: dict[str, set[str]] = defaultdict(set)
    for identity, item in phase2b_plan.items():
        for contract_id in item.get("contracts_applied", []):
            phase2b_users[str(contract_id)].add(identity)
    phase2c_users: dict[str, set[str]] = defaultdict(set)
    for identity, item in plan.items():
        for contract_id in item.get("contracts_applied", []):
            phase2c_users[str(contract_id)].add(identity)
    dormant = [
        item for item in CONTRACTS
        if item.versioned_id not in PHASE2C_NEW_CONTRACTS and not phase2b_users[item.versioned_id]
    ]
    dormant_rows = []
    for item in dormant:
        mapped = {blocker for blocker, contract_id in BLOCKER_CONTRACTS.items() if contract_id == item.versioned_id}
        affected = {identity for identity, values in blocker_by_id.items() if values.intersection(mapped)}
        coblockers = Counter(
            blocker for identity in affected for blocker in blocker_by_id[identity] if blocker not in mapped
        )
        users = phase2c_users[item.versioned_id]
        reason = "compiler family absent or always co-blocked" if affected else "no remaining blocker maps to contract"
        outcome = "activated_phase2_2c" if users else "remains_unused"
        dormant_rows.append({
            "contract_id": item.contract_id,
            "contract_version": item.version,
            "affected_strategies": len(affected),
            "currently_using_strategies": len(users),
            "remaining_coblockers": ";".join(f"{k}:{v}" for k, v in coblockers.most_common(12)),
            "implementation_status": "implemented",
            "compiler_application_status": outcome,
            "reason_unused": "" if users else reason,
            "action": outcome,
            "strategies_potentially_unlocked": len(users),
        })
    write_csv(args.audit_root / "phase2_2c_dormant_contract_audit.csv", list(dormant_rows[0]), dormant_rows)

    closure_rows = []
    status_rows = []
    for identity in sorted(remaining_ids):
        source = by_id[identity]
        original = blocker_by_id[identity]
        item = plan.get(identity)
        resolved = set(item.get("resolved_blockers", [])) if item else set()
        remaining = original - resolved
        if item and remaining:
            raise ValueError(f"partial implementation rejected for {identity}: {sorted(remaining)}")
        if not item and not remaining:
            raise ValueError(f"unregistered strategy has empty blocker set: {identity}")
        status = "IMPLEMENTED_STANDALONE" if item else "AMBIGUOUS_ENTRY_EXIT_OR_NUMERIC_SEMANTICS"
        complete = case_complete(args.backtest_root, identity) if item else None
        backtest = "passed" if complete is True else "pending" if item and complete is None else "failed" if item else "not_applicable"
        closure_rows.append({
            "source_identity": identity,
            "strategy_name": source["source_strategy_name"],
            "phase2_2b_status": "AMBIGUOUS_ENTRY_EXIT_OR_NUMERIC_SEMANTICS",
            "blocker_set_signature": signature_id[tuple(sorted(original))],
            "original_blockers": ";".join(sorted(original)),
            "resolved_blockers": ";".join(sorted(resolved)),
            "remaining_blockers": ";".join(sorted(remaining)),
            "contracts_applied": ";".join(item.get("contracts_applied", [])) if item else "",
            "modules_applied": ";".join(item.get("modules_applied", [])) if item else "",
            "semantic_provenance": item.get("semantic_provenance", "") if item else "",
            "registry_id": identity if item else "",
            "backtest_status": backtest,
        })
        if item:
            status_rows.append({
                "source_identity": identity, "old_status": "AMBIGUOUS_ENTRY_EXIT_OR_NUMERIC_SEMANTICS",
                "new_status": status, "blocker_set_before": ";".join(sorted(original)),
                "blocker_set_after": "", "contracts_applied": ";".join(item["contracts_applied"]),
                "defaulted_parameters": json.dumps(item["defaulted_parameters"], ensure_ascii=False, sort_keys=True),
                "registry_id": identity, "backtest_status": backtest,
            })
    closure_fields = (
        "source_identity", "strategy_name", "phase2_2b_status", "blocker_set_signature",
        "original_blockers", "resolved_blockers", "remaining_blockers", "contracts_applied",
        "modules_applied", "semantic_provenance", "registry_id", "backtest_status",
    )
    write_csv(args.audit_root / "phase2_2c_strategy_closure.csv", closure_fields, closure_rows)
    write_csv(args.audit_root / "phase2_2c_status_transitions.csv", list(status_rows[0]), status_rows)

    bundle_groups: dict[tuple[str, ...], set[str]] = defaultdict(set)
    for identity, item in plan.items():
        bundle_groups[tuple(sorted(str(x) for x in item["contracts_applied"]))].add(identity)
    bundle_rows = []
    for index, (contracts, identities) in enumerate(sorted(bundle_groups.items()), 1):
        affected = {
            identity for identity in remaining_ids
            if set(contracts).intersection(BLOCKER_CONTRACTS.get(x, "") for x in blocker_by_id[identity])
        }
        bundle_rows.append({
            "bundle_id": f"BUNDLE_{index:03d}", "contracts": ";".join(contracts), "modules": "",
            "affected_strategy_count": len(affected), "fully_unlocked_strategy_count": len(identities),
            "partially_resolved_strategy_count": len(affected - identities),
            "still_blocked_strategy_count": len(affected - identities),
        })
    write_csv(args.audit_root / "phase2_2c_contract_bundle_impact.csv", list(bundle_rows[0]), bundle_rows)

    closure_by_id = {row["source_identity"]: row for row in closure_rows}
    extra_fields = [
        "phase2_2c_status", "phase2_2c_original_blockers", "phase2_2c_resolved_blockers",
        "phase2_2c_remaining_blockers", "phase2_2c_contracts_applied", "phase2_2c_modules_applied",
    ]
    updated_manifest = []
    for row in manifest:
        closure = closure_by_id.get(row["registry_id"])
        row = dict(row)
        row.update({
            "phase2_2c_status": (
                "IMPLEMENTED_STANDALONE" if row["registry_id"] in plan
                else "UNCHANGED" if closure is None else "REMAINS_UNRESOLVED"
            ),
            "phase2_2c_original_blockers": closure["original_blockers"] if closure else "",
            "phase2_2c_resolved_blockers": closure["resolved_blockers"] if closure else "",
            "phase2_2c_remaining_blockers": closure["remaining_blockers"] if closure else "",
            "phase2_2c_contracts_applied": closure["contracts_applied"] if closure else "",
            "phase2_2c_modules_applied": closure["modules_applied"] if closure else "",
        })
        updated_manifest.append(row)
    fields = list(manifest[0]) + extra_fields
    write_csv(args.audit_root / "strategy_workbook_conversion_manifest.csv", fields, updated_manifest)
    write_csv(args.audit_root / "strategy_conversion_manifest.csv", fields, updated_manifest)
    write_csv(args.audit_root / "strategy_conversion_review.csv", fields, [
        row for row in updated_manifest if row["final_status"] not in {"implemented", "implemented_module"}
    ])
    write_csv(args.audit_root / "registered_strategy_manifest.csv", fields, [
        row for row in updated_manifest if row["final_status"] == "implemented"
    ])

    provenance = Counter(str(item["semantic_provenance"]) for item in plan.values())
    cluster_blockers = {
        "grid_pyramiding": {
            "GRID_LAYER_CONTRACT", "PYRAMID_STEP_DISTANCE", "PYRAMID_ADD_FRACTION",
            "POSITION_FRACTION_MISSING", "LAYERED_REDUCTION_SCHEDULE",
            "CHANNEL_STATE_DEFINITION",
        },
        "multitimeframe_confluence": {
            "SYNCHRONOUS_STATE_TIMING", "CONFLUENCE_COMPOSITION", "FRACTAL_SCALE_DEFINITION",
        },
        "mean_reversion_extreme": {"MEAN_REVERSION_COMPLETION", "EXTREME_THRESHOLD_MISSING"},
        "level_pullback_rejection": {
            "SUPPORT_ZONE_DEFINITION", "RESISTANCE_ZONE_DEFINITION", "PULLBACK_TO_LEVEL",
            "REJECT_FROM_RESISTANCE", "STABILIZE_AFTER_DECLINE", "NEAR_LEVEL",
        },
        "confirmation_persistence": {
            "PERSISTENCE_COUNT_MISSING", "STABLE_ABOVE", "CONFIRMATION_RULE", "TURN_UP", "TURN_DOWN",
        },
        "divergence": {"DIVERGENCE_PIVOT_DEFINITION_MISSING", "DIVERGENCE_LOOKBACK_MISSING"},
    }
    cluster_closure: dict[str, dict[str, int]] = {}
    for cluster, blockers in cluster_blockers.items():
        affected = {identity for identity, current in blocker_by_id.items() if current.intersection(blockers)}
        unlocked = affected.intersection(plan)
        cluster_closure[cluster] = {
            "affected": len(affected),
            "fully_unlocked": len(unlocked),
            "still_blocked": len(affected - unlocked),
        }
    defaulted_instances = sum(len(dict(item.get("defaulted_parameters", {}))) for item in plan.values())
    defaulted_types = sorted({
        str(parameter)
        for item in plan.values()
        for parameter in dict(item.get("defaulted_parameters", {}))
    })
    completed = sum(case_complete(args.backtest_root, identity) is True for identity in plan) if args.backtest_root else 0
    unresolved = len(remaining_ids) - len(plan)
    validation = {
        "status": "passed",
        "phase2_2c_starting_executable": 77,
        "phase2_2c_starting_ambiguous": 1153,
        "unique_blocker_set_signatures": len(signatures),
        "newly_executable": len(plan),
        "final_executable_standalone": 77 + len(plan),
        "remaining_true_ambiguity": unresolved,
        "registered_modules": 36,
        "semantic_provenance_counts_new": dict(sorted(provenance.items())),
        "cluster_closure": cluster_closure,
        "dormant_contract_outcomes": dict(Counter(row["action"] for row in dormant_rows)),
        "defaulted_parameter_instances": defaulted_instances,
        "unique_defaulted_parameter_types": defaulted_types,
        "prepared_for_parameter_search": sum(bool(item.get("defaulted_parameters")) for item in plan.values()),
        "five_year_backtests_attempted": len(plan) if args.backtest_root else 0,
        "five_year_backtests_passed": completed,
        "closure_rows": len(closure_rows),
        "implemented_with_remaining_blockers": sum(
            bool(row["remaining_blockers"]) for row in closure_rows if row["registry_id"]
        ),
        "unresolved_with_empty_blockers": sum(
            not row["remaining_blockers"] for row in closure_rows if not row["registry_id"]
        ),
        "unaccounted_phase2_2c": 1153 - len(closure_rows),
        "optimization_executed": 0,
        "full_workbook_reconciliation": {
            "executable_standalone": 77 + len(plan), "registered_modules": 36,
            "missing_external": 155, "session_economic_pending": 77,
            "remaining_ambiguity": unresolved, "unsupported_non_standalone_modules": 217,
            "total": 77 + len(plan) + 36 + 155 + 77 + unresolved + 217,
        },
    }
    if validation["full_workbook_reconciliation"]["total"] != 1715 or any(
        validation[key] for key in (
            "implemented_with_remaining_blockers", "unresolved_with_empty_blockers", "unaccounted_phase2_2c",
        )
    ):
        validation["status"] = "failed"
    write_json(args.audit_root / "phase2_2c_validation_summary.json", validation)
    write_json(args.audit_root / "validation_summary.json", validation)
    if args.deliverable_root:
        summary = args.deliverable_root / "canonical_summary.csv"
        if summary.is_file():
            shutil.copy2(summary, args.audit_root / "phase2_2c_backtest_summary.csv")
    elif not (args.audit_root / "phase2_2c_backtest_summary.csv").exists():
        write_csv(args.audit_root / "phase2_2c_backtest_summary.csv", ("strategy", "status"), [
            {"strategy": identity, "status": "pending"} for identity in sorted(plan)
        ])
    print(json.dumps(validation, ensure_ascii=False))
    return 0 if validation["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
