#!/usr/bin/env python3
# ruff: noqa: E402,I001
"""Validate the immutable Phase 3B Wave 5 plan without running backtests."""

from __future__ import annotations

import argparse
import csv
import hashlib
import itertools
import json
import os
import sys
from collections import defaultdict
from datetime import UTC
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from strategy_framework.parameter_adaptation import canonical_config_hash
from strategy_framework.parameter_adaptation import deterministic_candidate_id
from strategy_framework.parameter_adaptation import validate_parameter_constraints
from strategy_framework.parameter_search import PROTOCOL_VERSION


AUDIT = ROOT / "outputs/internal_audit/strategy_workbook"
PARENT_MANIFEST = AUDIT / "parameter_search_manifest.csv"
MANIFEST = AUDIT / "phase3b_wave5_parameter_search_manifest.csv"
FOLDS = AUDIT / "phase3a_walk_forward_protocol.json"
WAVE3_SUMMARY = ROOT / "outputs/parameter_search/phase3b_wave3/phase3b_wave3_validation_summary.json"
DEFAULT_OUTPUT = ROOT / "outputs/parameter_search/phase3b_wave5"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as stream:
        return list(csv.DictReader(stream))


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def atomic_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    fields = list(rows[0]) if rows else []
    with temporary.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        if fields:
            writer.writeheader()
            writer.writerows(rows)
    os.replace(temporary, path)


def digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode()).hexdigest()


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def wave5_specs(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    return [
        row
        for row in rows
        if row["status"] == "READY" and len(json.loads(row["searchable_parameters"])) == 1
    ]


def enumerate_candidates(spec: dict[str, str]) -> tuple[list[dict[str, Any]], int, int]:
    fixed = json.loads(spec["fixed_parameters"])
    space = json.loads(spec["candidate_space"])
    rows: list[dict[str, Any]] = []
    rejected = 0
    for values in itertools.product(*(space[name] for name in sorted(space))):
        parameters = {**fixed, **dict(zip(sorted(space), values, strict=True))}
        valid, failures = validate_parameter_constraints(parameters)
        if not valid:
            rejected += 1
            continue
        rows.append(
            {
                "candidate_id": deterministic_candidate_id(spec["search_id"], parameters),
                "config_hash": canonical_config_hash(parameters),
                "parameters": parameters,
                "constraint_failures": failures,
            }
        )
    raw = len(rows) + rejected
    return rows, raw, rejected


def runtime_base_parameters(strategy_id: str) -> dict[str, Any]:
    source = yaml.safe_load((ROOT / "strategies" / strategy_id / "config.yaml").read_text())
    params = dict(source["params"])
    for field in (
        "source_registry_id",
        "semantic_provenance",
        "contracts_applied",
        "defaulted_parameters",
    ):
        params.pop(field, None)
    return params


def equivalence_rows(specs: list[dict[str, str]]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for spec in specs:
        candidates, _, _ = enumerate_candidates(spec)
        base = runtime_base_parameters(spec["strategy_id"])
        semantic_payload = {
            "implementation_family": base.get("family"),
            "runtime_base_parameters": base,
            "candidate_parameter_vectors": [row["parameters"] for row in candidates],
            "target_timeframe": spec["target_timeframe"],
            "lag": "lag1m",
            "premium_mode": "INCLUDED",
            "direction_mode": "ORIGINAL",
            "protocol_version": PROTOCOL_VERSION,
        }
        semantic_hash = digest(semantic_payload)
        search_space_hash = digest([row["parameters"] for row in candidates])
        execution_context_hash = digest(
            {
                "timeframe": "1m",
                "lag": "lag1m",
                "premium_mode": "INCLUDED",
                "direction_mode": "ORIGINAL",
                "protocol_version": PROTOCOL_VERSION,
            }
        )
        record = {
            "search_id": spec["search_id"],
            "strategy_id": spec["strategy_id"],
            "source_identity": spec["owner_id"],
            "implementation_family": base.get("family", ""),
            "effective_semantic_hash": semantic_hash,
            "search_space_hash": search_space_hash,
            "execution_context_hash": execution_context_hash,
        }
        grouped[semantic_hash].append(record)
    for index, members in enumerate(sorted(grouped.values(), key=lambda value: value[0]["search_id"]), 1):
        group_id = f"wave5_equivalence_{index:03d}"
        representative = min(item["search_id"] for item in members)
        equivalent = len(members) > 1
        for item in members:
            records.append(
                {
                    "equivalence_group_id": group_id,
                    **item,
                    "equivalent_for_physical_compute": equivalent,
                    "canonical_compute_representative": representative,
                    "reason": (
                        "same family, source-independent runtime parameters, candidate vectors, and execution context"
                        if equivalent
                        else "no equivalent Wave 5 spec"
                    ),
                }
            )
    return sorted(records, key=lambda row: row["search_id"])


def main() -> int:
    args = parse_args()
    all_rows = read_csv(MANIFEST)
    specs = wave5_specs(all_rows)
    folds = json.loads(FOLDS.read_text(encoding="utf-8"))["folds"]
    wave3 = json.loads(WAVE3_SUMMARY.read_text(encoding="utf-8"))
    raw_total = valid_total = rejected_total = 0
    baseline_failures: list[dict[str, Any]] = []
    count_failures: list[dict[str, Any]] = []
    deterministic_failures: list[str] = []
    for spec in specs:
        candidates, raw, rejected = enumerate_candidates(spec)
        second, _, _ = enumerate_candidates(spec)
        raw_total += raw
        valid_total += len(candidates)
        rejected_total += rejected
        if candidates != second:
            deterministic_failures.append(spec["search_id"])
        expected = int(spec["estimated_candidate_count"])
        if len(candidates) != expected:
            count_failures.append(
                {"search_id": spec["search_id"], "expected": expected, "actual": len(candidates)}
            )
        baseline = json.loads(spec["baseline_candidate"])
        matches = [row for row in candidates if row["candidate_id"] == baseline["candidate_id"]]
        if len(matches) != 1 or (matches and matches[0]["config_hash"] != baseline["config_hash"]):
            closest = None
            searchable = json.loads(spec["searchable_parameters"])
            for row in candidates:
                if all(
                    isinstance(row["parameters"].get(name), (int, float))
                    and isinstance(baseline["parameters"].get(name), (int, float))
                    for name in searchable
                ):
                    distance = sum(
                        abs(float(row["parameters"][name]) - float(baseline["parameters"][name]))
                        for name in searchable
                    )
                    if closest is None or distance < closest[0]:
                        closest = (distance, row)
            baseline_failures.append(
                {
                    "search_id": spec["search_id"],
                    "strategy_id": spec["strategy_id"],
                    "baseline_candidate_id": baseline["candidate_id"],
                    "baseline_config_hash": baseline["config_hash"],
                    "baseline_parameters": baseline["parameters"],
                    "matching_candidate_count": len(matches),
                    "nearest_candidate_id": closest[1]["candidate_id"] if closest else None,
                    "nearest_parameters": closest[1]["parameters"] if closest else None,
                    "numeric_distance": closest[0] if closest else None,
                }
            )
    equivalence = equivalence_rows(specs)
    atomic_csv(args.output_root / "phase3b_wave5_equivalence_manifest.csv", equivalence)
    checks = {
        "wave3_release_decision": wave3.get("release_decision") == "WAVE5_READY",
        "protocol_version_fixed": wave3.get("protocol_version") == PROTOCOL_VERSION,
        "wave5_spec_count": len(specs) == 7,
        "wave5_candidate_count": valid_total == 30,
        "fold_count": len(folds) == 7,
        "train_logical_count": valid_total * len(folds) == 210,
        "validation_logical_count": valid_total * len(folds) == 210,
        "selected_test_logical_count": len(specs) * len(folds) == 49,
        "baseline_test_logical_count": len(specs) * len(folds) == 49,
        "logical_evaluation_count": 2 * valid_total * len(folds) + 2 * len(specs) * len(folds) == 518,
        "candidate_counts_match_manifest": not count_failures,
        "candidate_generation_deterministic": not deterministic_failures,
        "all_baseline_candidates_present_exactly_once": not baseline_failures,
        "all_candidates_constraint_valid": rejected_total == 0,
    }
    errors = [name for name, passed in checks.items() if not passed]
    protected = {
        str(path.relative_to(ROOT)): file_hash(path)
        for path in (
            PARENT_MANIFEST,
            MANIFEST,
            FOLDS,
            AUDIT / "phase3a_search_protocol.json",
            AUDIT / "phase3a_search_execution_plan.csv",
            AUDIT / "phase3a_parameter_inventory.csv",
            WAVE3_SUMMARY,
        )
    }
    group_sizes: dict[str, int] = defaultdict(int)
    for row in equivalence:
        group_sizes[row["equivalence_group_id"]] += 1
    report = {
        "status": "PASSED" if not errors else "FAILED",
        "release_decision": "WAVE5_EXECUTION_AUTHORIZED" if not errors else "WAVE5_BLOCKED",
        "checked_at_utc": datetime.now(UTC).isoformat(),
        "protocol_version": PROTOCOL_VERSION,
        "checks": checks,
        "errors": errors,
        "wave5_specs": len(specs),
        "raw_generated_candidates": raw_total,
        "constraint_valid_candidates": valid_total,
        "constraint_rejected_candidates": rejected_total,
        "folds": len(folds),
        "logical_evaluations": 2 * valid_total * len(folds) + 2 * len(specs) * len(folds),
        "baseline_membership_failures": baseline_failures,
        "candidate_count_failures": count_failures,
        "deterministic_generation_failures": deterministic_failures,
        "equivalence_groups": sum(size > 1 for size in group_sizes.values()),
        "specs_in_equivalence_groups": sum(size for size in group_sizes.values() if size > 1),
        "production_backtests_started": 0,
        "protected_file_hashes": protected,
    }
    atomic_json(args.output_root / "preflight_validation.json", report)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
