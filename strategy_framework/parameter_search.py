"""
Leakage-safe contracts for the versioned Phase 3B Wave 1 search.

This module owns selection/accounting identities only.  It deliberately has no
market-data or backtest access, so held-out results cannot enter selection.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
from typing import Any

from strategy_framework.parameter_adaptation import canonical_config_hash
from strategy_framework.parameter_adaptation import deterministic_candidate_id
from strategy_framework.parameter_adaptation import validate_parameter_constraints


PROTOCOL_VERSION = "PHASE3B_WAVE1_PROTOCOL_V1"
PARENT_PROTOCOL = "3A.1"
MIN_VALIDATION_TRADES = 5
WAVE1_PARAMETERS = frozenset(
    {
        "atr_window",
        "persistence_bars",
        "reduction_fraction",
        "recent_extreme_lookback",
        "fractal_side_bars",
    }
)


def protocol_amendment() -> dict[str, Any]:
    return {
        "protocol_version": PROTOCOL_VERSION,
        "parent_protocol": PARENT_PROTOCOL,
        "amendment_timestamp": datetime.now(UTC).isoformat(),
        "amendment_reason": "resolve the seven Phase 3B Wave 1 pre-run blockers without mutating Phase 3A",
        "amended_fields": [
            "eligibility.min_validation_trades",
            "eligibility.max_drawdown_constraint",
            "eligibility.minimum_break_even_bps_constraint",
            "selection.no_eligible_candidate_policy",
            "evaluation.baseline_oos_comparator",
            "checkpoint.logical_identity",
            "cache.physical_identity",
        ],
        "selection_dimensions": {
            "direction_mode": "ORIGINAL",
            "premium_mode": "INCLUDED",
            "timeframe": "1m",
            "lag": "lag1m",
        },
        "eligibility": {
            "min_validation_trades": MIN_VALIDATION_TRADES,
            "trade_count_definition": "completed trade episodes from canonical per-trade BE segmentation",
            "max_drawdown_constraint": {"enabled": False, "threshold": None},
            "minimum_break_even_bps_constraint": {"enabled": False, "threshold": None},
            "required": [
                "validity_status == VALID_RESULT",
                "trade_count >= 5",
                "finite turnover",
                "execution/integrity validations pass",
            ],
            "zero_trade_policy": "VALID_ZERO_TRADES_INELIGIBLE",
        },
        "selection": {
            "ranking": [
                "higher validation Return (1x)",
                "higher signed Global BE bps",
                "lower Max Drawdown",
                "lower Turnover",
                "deterministically smaller candidate_id",
            ],
            "weighted_score": False,
            "no_eligible_candidate_policy": "BASELINE_FALLBACK",
            "freeze_before_test": True,
        },
        "evaluation": {
            "pretest_logical_evaluations": 1470,
            "selected_test_logical_evaluations": 161,
            "baseline_test_logical_evaluations": 161,
            "logical_evaluation_count": 1792,
            "physical_execution_range": [1631, 1792],
            "baseline_oos_comparator": "independent held-out comparator after selection freeze",
            "physical_test_deduplication": "selected and baseline roles share one result when inputs match",
            "splits": ["TRAIN", "VALIDATION", "TEST"],
            "roles": ["CANDIDATE_PRESELECTION", "SELECTED_TEST", "BASELINE_TEST"],
        },
        "checkpoint": {
            "logical_identity": [
                "search_id",
                "candidate_id",
                "fold_id",
                "split",
                "evaluation_role",
                "protocol_version",
            ]
        },
        "cache": {
            "physical_identity": [
                "strategy_id",
                "search_id",
                "config_hash",
                "fold_id",
                "split",
                "timeframe",
                "lag",
                "premium_mode",
                "direction_mode",
                "protocol_version",
                "code_hash",
                "data_provenance",
            ]
        },
        "future_wave3": {
            "original_logical_estimate": 10073,
            "corrected_logical_estimate": 10318,
            "expected_physical_range": [10073, 10318],
            "execution_authorized": False,
        },
    }


def validate_protocol(value: dict[str, Any]) -> list[str]:  # noqa: C901 - explicit gate checklist
    errors: list[str] = []
    eligibility = value.get("eligibility", {})
    selection = value.get("selection", {})
    evaluation = value.get("evaluation", {})
    checkpoint = set(value.get("checkpoint", {}).get("logical_identity", []))
    cache = set(value.get("cache", {}).get("physical_identity", []))
    if value.get("protocol_version") != PROTOCOL_VERSION:
        errors.append("protocol_version")
    if eligibility.get("min_validation_trades") != 5:
        errors.append("min_validation_trades")
    if eligibility.get("max_drawdown_constraint") != {"enabled": False, "threshold": None}:
        errors.append("max_drawdown_constraint")
    if eligibility.get("minimum_break_even_bps_constraint") != {
        "enabled": False,
        "threshold": None,
    }:
        errors.append("minimum_break_even_bps_constraint")
    if selection.get("no_eligible_candidate_policy") != "BASELINE_FALLBACK":
        errors.append("fallback")
    if evaluation.get("logical_evaluation_count") != 1792:
        errors.append("logical_evaluation_count")
    if not evaluation.get("baseline_oos_comparator"):
        errors.append("baseline_oos_comparator")
    for field in ("split", "evaluation_role", "protocol_version"):
        if field not in checkpoint:
            errors.append(f"checkpoint.{field}")
    for field in ("split", "protocol_version"):
        if field not in cache:
            errors.append(f"cache.{field}")
    return errors


def is_wave1_spec(spec: dict[str, str]) -> bool:
    names = set(json.loads(spec["searchable_parameters"]))
    return spec.get("status") == "READY" and len(names) == 1 and names <= WAVE1_PARAMETERS


@dataclass(frozen=True)
class Candidate:
    search_id: str
    candidate_id: str
    config_hash: str
    parameters: tuple[tuple[str, Any], ...]
    candidate_role: str

    def as_parameters(self) -> dict[str, Any]:
        return dict(self.parameters)


def generate_candidates(spec: dict[str, str]) -> tuple[Candidate, ...]:
    search_id = spec["search_id"]
    fixed = json.loads(spec["fixed_parameters"])
    space = json.loads(spec["candidate_space"])
    baseline = json.loads(spec["baseline_candidate"])
    rows: list[Candidate] = []
    for values in itertools.product(*(space[name] for name in sorted(space))):
        parameters = {**fixed, **dict(zip(sorted(space), values, strict=True))}
        valid, _ = validate_parameter_constraints(parameters)
        if not valid:
            continue
        candidate_id = deterministic_candidate_id(search_id, parameters)
        rows.append(
            Candidate(
                search_id=search_id,
                candidate_id=candidate_id,
                config_hash=canonical_config_hash(parameters),
                parameters=tuple(sorted(parameters.items())),
                candidate_role="BASELINE" if candidate_id == baseline["candidate_id"] else "SEARCH",
            )
        )
    rows.sort(
        key=lambda row: json.dumps(row.as_parameters(), sort_keys=True, separators=(",", ":"))
    )
    if len(rows) != int(spec["estimated_candidate_count"]):
        raise ValueError(f"{search_id}: candidate count changed")
    baseline_rows = [row for row in rows if row.candidate_role == "BASELINE"]
    if len(baseline_rows) != 1:
        raise ValueError(f"{search_id}: expected exactly one baseline candidate")
    if baseline_rows[0].config_hash != baseline["config_hash"]:
        raise ValueError(f"{search_id}: baseline hash changed")
    return tuple(rows)


def validation_eligibility(metrics: dict[str, Any]) -> tuple[bool, tuple[str, ...]]:
    reasons: list[str] = []
    status = metrics.get("validity_status")
    if status != "VALID_RESULT":
        reasons.append("ZERO_TRADES" if status == "VALID_ZERO_TRADES" else "INVALID_RESULT")
    if int(metrics.get("trade_count", 0)) < MIN_VALIDATION_TRADES and status == "VALID_RESULT":
        reasons.append("INSUFFICIENT_VALIDATION_TRADES")
    turnover = metrics.get("turnover")
    if turnover is None or not math.isfinite(float(turnover)):
        reasons.append("NONFINITE_TURNOVER")
    if not bool(metrics.get("execution_validation_status", False)):
        reasons.append("EXECUTION_VALIDATION_FAILED")
    return not reasons, tuple(reasons)


def _finite_for_rank(value: Any, *, lower_is_better: bool = False) -> float:
    number = float(value)
    if math.isfinite(number):
        return -number if lower_is_better else number
    return float("-inf")


def rank_eligible(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    eligible = [dict(row) for row in rows if row.get("eligible") is True]
    eligible.sort(key=lambda row: row["candidate_id"])
    eligible.sort(
        key=lambda row: (
            _finite_for_rank(row["return_1x"]),
            _finite_for_rank(row["signed_global_be_bps"]),
            _finite_for_rank(row["max_drawdown"]),  # less negative / higher is lower drawdown
            _finite_for_rank(row["turnover"], lower_is_better=True),
        ),
        reverse=True,
    )
    for index, row in enumerate(eligible, start=1):
        row["ranking"] = index
    return eligible


def select_candidate(rows: Iterable[dict[str, Any]], baseline_candidate_id: str) -> dict[str, Any]:
    ranked = rank_eligible(rows)
    if ranked:
        return {
            "selected_candidate_id": ranked[0]["candidate_id"],
            "selection_status": "SELECTED",
            "ranked": ranked,
        }
    return {
        "selected_candidate_id": baseline_candidate_id,
        "selection_status": "BASELINE_FALLBACK",
        "ranked": [],
    }


def logical_checkpoint_id(**fields: str) -> str:
    required = (
        "search_id",
        "candidate_id",
        "fold_id",
        "split",
        "evaluation_role",
        "protocol_version",
    )
    payload = {name: fields[name] for name in required}
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def physical_cache_key(**fields: str) -> str:
    required = tuple(protocol_amendment()["cache"]["physical_identity"])
    payload = {name: fields[name] for name in required}
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
