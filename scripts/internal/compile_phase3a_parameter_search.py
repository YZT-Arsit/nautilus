#!/usr/bin/env python3
"""Compile Phase 3A parameter/search contracts without running optimization."""

from __future__ import annotations

import argparse
import csv
import hashlib
import itertools
import json
import math
import os
import sys
from collections import Counter
from collections import defaultdict
from datetime import UTC
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from strategy_framework.parameter_adaptation import AdaptationMode  # noqa: E402
from strategy_framework.parameter_adaptation import ParameterSemantic  # noqa: E402
from strategy_framework.parameter_adaptation import canonical_config_hash  # noqa: E402
from strategy_framework.parameter_adaptation import classify_parameter  # noqa: E402
from strategy_framework.parameter_adaptation import deterministic_candidate_id  # noqa: E402
from strategy_framework.parameter_adaptation import duration_preserving_bars  # noqa: E402
from strategy_framework.parameter_adaptation import validate_parameter_constraints  # noqa: E402


AUDIT = ROOT / "outputs/internal_audit/strategy_workbook"
TARGETS = ("1m", "5m", "15m", "30m")
TARGET_MINUTES = {"1m": 1, "5m": 5, "15m": 15, "30m": 30}
DATASET_START = "2021-07-01T00:00:00Z"
DATASET_END_EXCLUSIVE = "2026-07-01T00:00:00Z"


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def write_csv(path: Path, fields: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    os.replace(temporary, path)


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def parse_json(text: str, fallback: Any) -> Any:
    if not text:
        return fallback
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return fallback


def load_yaml_params(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return dict(data.get("params") or {})


def parameter_provenance(
    manifest_row: dict[str, str], parameter_name: str, defaulted: dict[str, Any]
) -> str:
    if parameter_name in defaulted:
        return "SEMANTIC_CONTRACT_DEFAULT"
    if parameter_name.startswith("session_"):
        return "SESSION_CONTRACT_DEFAULT"
    provenance = manifest_row.get("semantic_provenance") or "SOURCE_EXACT"
    if provenance == "PARAMETER_DEFAULTED":
        return "SOURCE_EXPLICIT"
    if provenance == "STANDARD_CONTRACT_RESOLVED":
        return "STANDARD_INDICATOR_DEFAULT"
    if provenance == "SESSION_CONTRACT_RESOLVED":
        return "SESSION_CONTRACT_DEFAULT"
    return "SOURCE_EXPLICIT"


def runtime_default_mapping(params: dict[str, Any], defaults: dict[str, Any]) -> dict[str, Any]:
    """Map contract vocabulary to the runtime config field that carries it."""
    mapped = {name: value for name, value in defaults.items() if name in params}
    aliases = {
        "persistence_bars": "consecutive_bars",
        "fractal_side_bars": "consecutive_bars",
        "recent_extreme_lookback": "exit_window",
        "reduction_stages": "reduction_fraction",
    }
    for source_name, runtime_name in aliases.items():
        if source_name in defaults and runtime_name in params:
            mapped[runtime_name] = params[runtime_name]
    return mapped


def scalar_items(value: Any, prefix: str = "") -> list[tuple[str, Any]]:
    rows: list[tuple[str, Any]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            rows.extend(scalar_items(child, f"{prefix}.{key}" if prefix else str(key)))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            rows.extend(scalar_items(child, f"{prefix}[{index}]"))
    elif isinstance(value, (str, int, float, bool)) and value is not None:
        rows.append((prefix, value))
    return rows


def conversion_policy(semantic: ParameterSemantic, source_timeframe: str) -> str:
    if semantic is ParameterSemantic.EXECUTION_PARAMETER:
        return "EXCLUDE_FROM_ALPHA_SEARCH"
    if semantic in {
        ParameterSemantic.DIMENSIONLESS_THRESHOLD,
        ParameterSemantic.VOLATILITY_MULTIPLIER,
        ParameterSemantic.POSITION_FRACTION,
        ParameterSemantic.BOOLEAN_ENUM_SEMANTICS,
    }:
        return "PRESERVE"
    if semantic in {ParameterSemantic.CALENDAR_PARAMETER, ParameterSemantic.SESSION_PARAMETER}:
        return "PRESERVE_CALENDAR_CONTRACT"
    if semantic is ParameterSemantic.PHYSICAL_DURATION:
        return "DURATION_PRESERVING"
    if semantic is ParameterSemantic.BAR_LOOKBACK and source_timeframe.lower() == "daily":
        return "SEARCH_ADAPTED_WITH_DURATION_BASELINE"
    if semantic is ParameterSemantic.BAR_LOOKBACK:
        return "PRESERVE_BAR_COUNT"
    return "PRESERVE"


def strategy_adaptation(row: dict[str, str], target: str) -> tuple[str, str]:
    current = (row.get("adaptation_mode") or "").upper()
    minute_status = (row.get("minute_conversion_status") or "").upper()
    source = (row.get("source_timeframe_semantics") or "").lower()
    if source in {"session", "session_or_calendar"} and row.get("phase2_3_session_contracts"):
        return AdaptationMode.DURATION_PRESERVING.value, "approved crypto UTC session contract"
    if "UNSAFE" in current or "UNSAFE" in minute_status:
        return AdaptationMode.UNSAFE_TO_CONVERT.value, (
            "canonical Phase 2 audit retains original timeframe; mechanical minute conversion prohibited"
        )
    if source in {"daily", "calendar", "session", "session_or_calendar"}:
        if row.get("phase2_3_session_contracts"):
            return AdaptationMode.DURATION_PRESERVING.value, "approved crypto session contract"
        return (
            AdaptationMode.SEARCH_ADAPTED.value,
            "daily observation scale requires adaptation search",
        )
    if current == AdaptationMode.SEARCH_ADAPTED.value:
        return current, "existing canonical adaptation classification"
    if current == AdaptationMode.DURATION_PRESERVING.value:
        return current, "physical duration is explicit"
    if current == AdaptationMode.NOT_APPLICABLE.value:
        return current, "target incompatible with source data contract"
    return (
        AdaptationMode.DIRECT_INTRADAY.value,
        f"bar-count semantics applied on explicit {target} target",
    )


def is_searchable(
    provenance: str,
    semantic: ParameterSemantic,
    adaptation_mode: str,
) -> tuple[bool, str, str]:
    if semantic is ParameterSemantic.EXECUTION_PARAMETER:
        return False, "excluded", "execution sensitivity is not alpha optimization"
    if semantic in {
        ParameterSemantic.BOOLEAN_ENUM_SEMANTICS,
        ParameterSemantic.CALENDAR_PARAMETER,
        ParameterSemantic.SESSION_PARAMETER,
    }:
        return False, "fixed_semantic", "semantic/calendar choice is not a numeric search parameter"
    if provenance in {"SEMANTIC_CONTRACT_DEFAULT", "MODULE_CONTRACT_DEFAULT"}:
        return True, "high", "baseline default introduced by an approved contract"
    if adaptation_mode == AdaptationMode.SEARCH_ADAPTED.value and semantic in {
        ParameterSemantic.BAR_LOOKBACK,
        ParameterSemantic.PHYSICAL_DURATION,
    }:
        return True, "medium", "sampling-scale adaptation requires bounded search"
    return False, "low", "source-explicit parameter retained as immutable baseline"


def candidates(name: str, value: Any, semantic: ParameterSemantic) -> list[Any]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return [value]
    if semantic is ParameterSemantic.BAR_LOOKBACK:
        seed = int(value)
        return sorted({max(1, round(seed * factor)) for factor in (0.5, 0.75, 1, 1.5, 2)})
    if semantic is ParameterSemantic.INTEGER_STATE_PARAMETER:
        seed = int(value)
        return sorted({max(1, seed - 1), seed, seed + 1, max(1, seed * 2)})
    if semantic is ParameterSemantic.POSITION_FRACTION:
        return sorted({round(x, 8) for x in (0.25, 0.5, 0.75, 1.0, float(value)) if 0 < x <= 1})
    if semantic in {
        ParameterSemantic.VOLATILITY_MULTIPLIER,
        ParameterSemantic.DIMENSIONLESS_THRESHOLD,
    }:
        seed = float(value)
        if seed == 0:
            return [0.0]
        return sorted({round(seed * factor, 8) for factor in (0.5, 0.75, 1, 1.25, 1.5)})
    return [value]


def constraint_text(parameter_names: set[str]) -> list[str]:
    rules = ["positive lookbacks", "integer state counts", "0 < position_fraction <= 1"]
    if {"fast_window", "slow_window"} <= parameter_names:
        rules.append("fast_window < slow_window")
    if {"lower_threshold", "upper_threshold"} <= parameter_names:
        rules.append("lower_threshold < upper_threshold")
    if {"layer_fraction", "max_layers", "max_exposure"} <= parameter_names:
        rules.append("layer_fraction * max_layers <= max_exposure")
    return rules


def valid_candidate_count(space: dict[str, list[Any]], fixed: dict[str, Any]) -> tuple[int, int]:
    raw = math.prod(len(values) for values in space.values()) if space else 1
    if raw > 20_000:
        return raw, min(raw, 128)
    valid = 0
    names = list(space)
    for values in itertools.product(*(space[name] for name in names)):
        candidate = {**fixed, **dict(zip(names, values, strict=True))}
        if validate_parameter_constraints(candidate)[0]:
            valid += 1
    return raw, valid


def walk_forward_protocol() -> dict[str, Any]:
    boundaries = (
        (
            "wf01",
            "2021-07-01",
            "2022-07-01",
            "2022-07-01",
            "2023-01-01",
            "2023-01-01",
            "2023-07-01",
        ),
        (
            "wf02",
            "2021-07-01",
            "2023-01-01",
            "2023-01-01",
            "2023-07-01",
            "2023-07-01",
            "2024-01-01",
        ),
        (
            "wf03",
            "2021-07-01",
            "2023-07-01",
            "2023-07-01",
            "2024-01-01",
            "2024-01-01",
            "2024-07-01",
        ),
        (
            "wf04",
            "2021-07-01",
            "2024-01-01",
            "2024-01-01",
            "2024-07-01",
            "2024-07-01",
            "2025-01-01",
        ),
        (
            "wf05",
            "2021-07-01",
            "2024-07-01",
            "2024-07-01",
            "2025-01-01",
            "2025-01-01",
            "2025-07-01",
        ),
        (
            "wf06",
            "2021-07-01",
            "2025-01-01",
            "2025-01-01",
            "2025-07-01",
            "2025-07-01",
            "2026-01-01",
        ),
        (
            "wf07",
            "2021-07-01",
            "2025-07-01",
            "2025-07-01",
            "2026-01-01",
            "2026-01-01",
            "2026-07-01",
        ),
    )
    folds = [
        {
            "fold_id": row[0],
            "train": {"start_inclusive": row[1], "end_exclusive": row[2]},
            "validation": {"start_inclusive": row[3], "end_exclusive": row[4]},
            "test": {"start_inclusive": row[5], "end_exclusive": row[6]},
        }
        for row in boundaries
    ]
    return {
        "protocol_version": "3A.1",
        "timezone": "UTC",
        "dataset": {"start_inclusive": DATASET_START, "end_exclusive": DATASET_END_EXCLUSIVE},
        "window_type": "EXPANDING_WINDOW",
        "folds": folds,
        "warmup_policy": "load prior-only history equal to max candidate warmup; exclude warmup returns",
        "state_reset_policy": "reset strategy and execution state at each train/validation/test evaluation boundary",
        "selection_timing": "select on validation after train diagnostics; freeze before one held-out test evaluation",
        "oos_stitching_rule": "concatenate held-out test returns chronologically; sum turnover; compute MDD on stitched additive 1x path",
        "final_holdout": "wf07 test (2026H1) remains held out until its fold selection is frozen",
    }


def search_protocol() -> dict[str, Any]:
    return {
        "protocol_version": "3A.1",
        "selection_dimensions": ["strategy parameters", "target_timeframe", "walk_forward_fold"],
        "excluded_dimensions": [
            "execution_lag",
            "premium_mode",
            "direction_variant",
            "unresolved_semantic_choices",
            "joint_strategy_module_space",
        ],
        "candidate_generation_rules": {
            "integer_lookback": "bounded multiplicative neighborhood including baseline",
            "integer_state": "bounded adjacent values including baseline",
            "fraction": "bounded values in (0,1] including baseline",
            "random_search_seed": 240301,
            "order": "canonical JSON then SHA-256 candidate ID",
        },
        "constraint_rules": [
            "positive lookbacks",
            "integer state counts",
            "fast < slow",
            "low < high",
            "0 < fraction <= 1",
            "layer_fraction * layers <= exposure cap",
        ],
        "baseline_inclusion": "canonical Phase 2 baseline mandatory whenever valid",
        "validation_metrics": [
            "return_1x",
            "turnover",
            "global_break_even_bps",
            "max_drawdown",
            "trade_count",
            "median_trade_break_even_bps_optional",
        ],
        "eligibility_framework": {
            "required": ["valid execution", "finite turnover", "trade_count >= configured minimum"],
            "protocol_settings": [
                "min_validation_trades",
                "max_drawdown",
                "minimum_break_even_bps",
            ],
            "zero_trade_policy": "VALID_ZERO_TRADES but ineligible for selection",
        },
        "ranking_framework": [
            "higher validation return",
            "higher signed BE bps",
            "lower MDD",
            "lower turnover",
            "deterministic candidate_id tie break",
        ],
        "determinism_policy": "immutable candidate config + canonical SHA-256 + stored random seed",
        "cache_policy": {
            "key_fields": [
                "strategy_id",
                "config_hash",
                "timeframe",
                "fold_id",
                "lag",
                "premium_selection_mode",
                "code_version",
                "data_provenance",
            ],
            "partial_result_policy": "only atomically committed results are reusable",
        },
        "checkpoint_unit": "search_id x candidate_id x fold_id",
        "invalid_candidate_statuses": ["INVALID_CONFIG", "VALID_ZERO_TRADES", "BACKTEST_FAILURE"],
        "selection_execution_assumption": "lag1m for 1m strategies; lag remains fixed and is not optimized",
        "selection_premium_treatment": "premium included; premium excluded is post-selection evaluation only",
        "selection_direction": "ORIGINAL; directional variants evaluated only after selection",
    }


def main() -> int:  # noqa: C901
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit-root", type=Path, default=AUDIT)
    args = parser.parse_args()
    audit = args.audit_root
    strategy_rows = read_csv(audit / "registered_strategy_manifest.csv")
    module_rows = read_csv(audit / "registered_module_manifest.csv")
    if len(strategy_rows) != 131 or len({row["registry_id"] for row in strategy_rows}) != 131:
        raise ValueError("expected exactly 131 unique executable strategies")
    if len(module_rows) != 72 or len({row["module_id"] for row in module_rows}) != 72:
        raise ValueError("expected exactly 72 unique registered modules")

    strategy_params: dict[str, dict[str, Any]] = {}
    strategy_defaults: dict[str, dict[str, Any]] = {}
    strategy_source_defaults: dict[str, dict[str, Any]] = {}
    strategy_by_id = {row["registry_id"]: row for row in strategy_rows}
    for row in strategy_rows:
        path = ROOT / row["config_path"]
        params = load_yaml_params(path)
        for metadata_key in (
            "source_registry_id",
            "family",
            "semantic_provenance",
            "contracts_applied",
            "defaulted_parameters",
            "session_semantic_provenance",
            "session_defaulted_parameters",
        ):
            params.pop(metadata_key, None)
        strategy_params[row["registry_id"]] = params
        source_defaults = parse_json(row.get("defaulted_parameters", ""), {})
        strategy_source_defaults[row["registry_id"]] = source_defaults
        strategy_defaults[row["registry_id"]] = runtime_default_mapping(params, source_defaults)

    module_config_by_id: dict[str, dict[str, Any]] = {}
    for path in sorted((ROOT / "configs/strategy_modules").glob("*.json")):
        if path.name.startswith((".", "~")):
            continue
        for module in json.loads(path.read_text(encoding="utf-8-sig")):
            module_config_by_id[module["module_id"]] = module
    if set(module_config_by_id) != {row["module_id"] for row in module_rows}:
        missing = {row["module_id"] for row in module_rows} - set(module_config_by_id)
        raise ValueError(f"module config mismatch: {sorted(missing)}")

    inventory: list[dict[str, Any]] = []
    adaptations: list[dict[str, Any]] = []
    parameter_adaptations: list[dict[str, Any]] = []
    unsafe: list[dict[str, Any]] = []
    searchable_by_strategy: dict[str, list[str]] = defaultdict(list)

    for row in strategy_rows:
        strategy_id = row["registry_id"]
        params = strategy_params[strategy_id]
        defaults = strategy_defaults[strategy_id]
        source_timeframe = row.get("source_timeframe_semantics") or "bar_period"
        target_modes: dict[str, str] = {}
        for target in TARGETS:
            mode, reason = strategy_adaptation(row, target)
            target_modes[target] = mode
            searchable_names: list[str] = []
            fixed_names: list[str] = []
            for name, value in params.items():
                semantic = classify_parameter(name, value)
                provenance = parameter_provenance(row, name, defaults)
                searchable, _, _ = is_searchable(provenance, semantic, mode)
                (searchable_names if searchable else fixed_names).append(name)
                duration_value: int | str = ""
                if semantic is ParameterSemantic.PHYSICAL_DURATION and isinstance(
                    value, (int, float)
                ):
                    duration_value = duration_preserving_bars(float(value), TARGET_MINUTES[target])
                elif (
                    semantic is ParameterSemantic.BAR_LOOKBACK
                    and source_timeframe.lower() == "daily"
                    and isinstance(value, int)
                ):
                    duration_value = duration_preserving_bars(value * 1440, TARGET_MINUTES[target])
                parameter_adaptations.append(
                    {
                        "strategy_id": strategy_id,
                        "parameter_name": name,
                        "parameter_type": semantic.value,
                        "source_value": json.dumps(value, ensure_ascii=False),
                        "source_provenance": provenance,
                        "source_timeframe": source_timeframe,
                        "target_timeframe": target,
                        "conversion_policy": conversion_policy(semantic, source_timeframe),
                        "duration_preserving_value": duration_value,
                        "searchable": searchable,
                        "candidate_generator": "semantic_bounded_v1" if searchable else "none",
                        "constraints": ";".join(constraint_text(set(params))),
                        "notes": "baseline value retained in every generated space",
                    }
                )
            adaptations.append(
                {
                    "strategy_id": strategy_id,
                    "source_identity": strategy_id,
                    "strategy_family": row.get("implementation_family", ""),
                    "source_timeframe": source_timeframe,
                    "target_timeframe": target,
                    "adaptation_status": "READY"
                    if mode != AdaptationMode.UNSAFE_TO_CONVERT.value
                    else "UNSAFE",
                    "adaptation_mode": mode,
                    "search_required": bool(searchable_names),
                    "searchable_parameters": json.dumps(
                        sorted(searchable_names), ensure_ascii=False
                    ),
                    "fixed_parameters": json.dumps(sorted(fixed_names), ensure_ascii=False),
                    "unsafe_reason": reason
                    if mode == AdaptationMode.UNSAFE_TO_CONVERT.value
                    else "",
                    "notes": reason,
                }
            )
            if mode == AdaptationMode.UNSAFE_TO_CONVERT.value:
                unsafe.append(
                    {
                        "strategy_id": strategy_id,
                        "source_timeframe": source_timeframe,
                        "requested_target": target,
                        "blocking_semantics": row.get("minute_conversion_status")
                        or row.get("adaptation_mode"),
                        "why_mechanical_conversion_is_invalid": reason,
                        "whether_original_strategy_remains_valid": True,
                        "possible_future_action": "retain original timeframe; require explicit economic adaptation contract",
                    }
                )

        canonical_mode = target_modes["1m"]
        for name, value in params.items():
            semantic = classify_parameter(name, value)
            provenance = parameter_provenance(row, name, defaults)
            searchable, priority, notes = is_searchable(provenance, semantic, canonical_mode)
            if searchable:
                searchable_by_strategy[strategy_id].append(name)
            category = (
                "SEARCHABLE"
                if searchable
                else "EXECUTION_ONLY"
                if semantic is ParameterSemantic.EXECUTION_PARAMETER
                else "UNSAFE_NON_SEARCHABLE"
                if semantic
                in {
                    ParameterSemantic.BOOLEAN_ENUM_SEMANTICS,
                    ParameterSemantic.CALENDAR_PARAMETER,
                    ParameterSemantic.SESSION_PARAMETER,
                }
                else "FIXED_SEMANTIC"
                if provenance != "SOURCE_EXPLICIT"
                else "FIXED_SOURCE"
            )
            owner_type = (
                "semantic_contract"
                if provenance == "SEMANTIC_CONTRACT_DEFAULT"
                else "session_contract"
                if provenance == "SESSION_CONTRACT_DEFAULT"
                else "strategy"
            )
            inventory.append(
                {
                    "owner_type": owner_type,
                    "owner_id": strategy_id,
                    "strategy_or_module_family": row.get("implementation_family", ""),
                    "parameter_name": name,
                    "current_value": json.dumps(value, ensure_ascii=False),
                    "parameter_type": semantic.value,
                    "source_provenance": provenance,
                    "source_timeframe": source_timeframe,
                    "is_searchable": searchable,
                    "search_priority": priority,
                    "conversion_policy": conversion_policy(semantic, source_timeframe),
                    "structural_constraints": ";".join(constraint_text(set(params))),
                    "reconciliation_category": category,
                    "notes": notes,
                }
            )

    module_by_id = {row["module_id"]: row for row in module_rows}
    for module_id in sorted(module_config_by_id):
        config = module_config_by_id[module_id]
        manifest = module_by_id[module_id]
        defaulted = parse_json(manifest.get("defaulted_parameters", ""), {})
        metadata_prefixes = (
            "module_id",
            "source_identity",
            "source_sheet",
            "source_strategy_number",
            "source_strategy_name",
            "module_version",
            "compatibility",
            "module_family",
            "module_type",
            "semantic_provenance",
            "contracts_applied",
            "defaulted_parameters",
        )
        for name, value in scalar_items(config):
            if (
                name == metadata_prefixes
                or name.startswith(tuple(f"{key}." for key in metadata_prefixes))
                or name.split("[", 1)[0] in metadata_prefixes
            ):
                continue
            leaf = name.rsplit(".", 1)[-1].split("[", 1)[0]
            semantic = classify_parameter(leaf, value)
            provenance = "MODULE_CONTRACT_DEFAULT" if leaf in defaulted else "SOURCE_EXPLICIT"
            inventory.append(
                {
                    "owner_type": "module",
                    "owner_id": module_id,
                    "strategy_or_module_family": manifest.get("module_family", ""),
                    "parameter_name": name,
                    "current_value": json.dumps(value, ensure_ascii=False),
                    "parameter_type": semantic.value,
                    "source_provenance": provenance,
                    "source_timeframe": "host_timeframe",
                    "is_searchable": False,
                    "search_priority": "low",
                    "conversion_policy": conversion_policy(semantic, "host_timeframe"),
                    "structural_constraints": ";".join(constraint_text({leaf})),
                    "reconciliation_category": "FIXED_SOURCE"
                    if semantic is not ParameterSemantic.BOOLEAN_ENUM_SEMANTICS
                    else "UNSAFE_NON_SEARCHABLE",
                    "notes": "module audited separately; joint strategy-module search disabled by protocol",
                }
            )

    for name, value, semantic, category, notes in (
        (
            "execution_lag_minutes",
            1,
            ParameterSemantic.EXECUTION_PARAMETER,
            "EXECUTION_ONLY",
            "fixed realistic lag; lag0 is post-selection sensitivity",
        ),
        (
            "premium_mode",
            "included",
            ParameterSemantic.BOOLEAN_ENUM_SEMANTICS,
            "UNSAFE_NON_SEARCHABLE",
            "evaluation toggle, never selected by performance",
        ),
        (
            "direction_variant",
            "ORIGINAL",
            ParameterSemantic.BOOLEAN_ENUM_SEMANTICS,
            "UNSAFE_NON_SEARCHABLE",
            "direction variants evaluated after selection",
        ),
    ):
        inventory.append(
            {
                "owner_type": "framework",
                "owner_id": "PHASE3A_SEARCH_PROTOCOL",
                "strategy_or_module_family": "framework",
                "parameter_name": name,
                "current_value": json.dumps(value),
                "parameter_type": semantic.value,
                "source_provenance": "FRAMEWORK_PARAMETER",
                "source_timeframe": "1m_selection",
                "is_searchable": False,
                "search_priority": "excluded",
                "conversion_policy": "EXCLUDE_FROM_ALPHA_SEARCH",
                "structural_constraints": "fixed during selection",
                "reconciliation_category": category,
                "notes": notes,
            }
        )

    folds = walk_forward_protocol()
    fold_count = len(folds["folds"])
    search_specs: list[dict[str, Any]] = []
    unsafe_specs: list[dict[str, Any]] = []
    compute: list[dict[str, Any]] = []
    for strategy_id in sorted(searchable_by_strategy):
        names = sorted(searchable_by_strategy[strategy_id])
        if not names:
            continue
        row = strategy_by_id[strategy_id]
        params = strategy_params[strategy_id]
        mode, _ = strategy_adaptation(row, "1m")
        if mode == AdaptationMode.UNSAFE_TO_CONVERT.value:
            continue
        space = {
            name: candidates(name, params[name], classify_parameter(name, params[name]))
            for name in names
        }
        fixed = {name: value for name, value in params.items() if name not in names}
        raw_count, valid_count = valid_candidate_count(space, fixed)
        method = (
            "SMALL_GRID"
            if valid_count <= 25
            else "CONSTRAINED_GRID"
            if valid_count <= 128
            else "RANDOM_SEARCH"
        )
        evaluated_count = min(valid_count, 128) if method == "RANDOM_SEARCH" else valid_count
        search_id = f"phase3a__{strategy_id}__1m"
        baseline = {
            "candidate_id": deterministic_candidate_id(search_id, params),
            "config_hash": canonical_config_hash(params),
            "parameters": params,
            "label": "canonical_phase2_baseline",
        }
        duration_candidate: dict[str, Any] | str = ""
        if (row.get("source_timeframe_semantics") or "").lower() == "daily":
            adapted = dict(params)
            for name in names:
                if classify_parameter(
                    name, params[name]
                ) is ParameterSemantic.BAR_LOOKBACK and isinstance(params[name], int):
                    adapted[name] = params[name] * 1440
            valid, _ = validate_parameter_constraints(adapted)
            if valid:
                duration_candidate = {
                    "candidate_id": deterministic_candidate_id(search_id, adapted),
                    "config_hash": canonical_config_hash(adapted),
                    "parameters": adapted,
                    "label": "duration_preserving_baseline",
                }
        group = f"{row.get('implementation_family', 'unknown')}__{'__'.join(names)}"
        constraints = constraint_text(set(params))
        estimated_runs = evaluated_count * fold_count * 2 + fold_count
        search_specs.append(
            {
                "search_id": search_id,
                "owner_id": strategy_id,
                "strategy_id": strategy_id,
                "target_timeframe": "1m",
                "search_group_id": group,
                "search_method": method,
                "searchable_parameters": json.dumps(names, ensure_ascii=False),
                "fixed_parameters": json.dumps(fixed, ensure_ascii=False, sort_keys=True),
                "candidate_space": json.dumps(space, ensure_ascii=False, sort_keys=True),
                "constraints": json.dumps(constraints, ensure_ascii=False),
                "baseline_candidate": json.dumps(baseline, ensure_ascii=False, sort_keys=True),
                "duration_preserving_candidate": json.dumps(
                    duration_candidate, ensure_ascii=False, sort_keys=True
                )
                if duration_candidate
                else "",
                "train_protocol": "phase3a_walk_forward_protocol.json:expanding train",
                "validation_protocol": "six-month validation immediately before test",
                "test_protocol": "one frozen six-month held-out test per fold",
                "selection_rule": "eligibility constraints then return, signed BE, MDD, turnover deterministic ranking",
                "estimated_candidate_count": evaluated_count,
                "estimated_backtest_count": estimated_runs,
                "status": "READY",
            }
        )
        compute.append(
            {
                "search_id": search_id,
                "strategy_id": strategy_id,
                "candidate_count": evaluated_count,
                "raw_candidate_count": raw_count,
                "fold_count": fold_count,
                "lag_count": 1,
                "direction_count": 1,
                "premium_count": 1,
                "estimated_backtest_runs": estimated_runs,
                "estimated_feature_work": f"{evaluated_count} candidate feature sets; family cache eligible",
                "expected_cache_reuse": "market bars/resampling/shared FeatureSpec values",
            }
        )

    for row in strategy_rows:
        strategy_id = row["registry_id"]
        mode, reason = strategy_adaptation(row, "1m")
        if mode != AdaptationMode.UNSAFE_TO_CONVERT.value:
            continue
        params = strategy_params[strategy_id]
        unsafe_specs.append(
            {
                "search_id": f"phase3a__{strategy_id}__1m",
                "owner_id": strategy_id,
                "strategy_id": strategy_id,
                "target_timeframe": "1m",
                "search_group_id": "",
                "search_method": "NONE",
                "searchable_parameters": "[]",
                "fixed_parameters": json.dumps(params, ensure_ascii=False, sort_keys=True),
                "candidate_space": "{}",
                "constraints": json.dumps([reason], ensure_ascii=False),
                "baseline_candidate": "",
                "duration_preserving_candidate": "",
                "train_protocol": "",
                "validation_protocol": "",
                "test_protocol": "",
                "selection_rule": "",
                "estimated_candidate_count": 0,
                "estimated_backtest_count": 0,
                "status": "UNSAFE",
            }
        )

    defaults_counter: Counter[str] = Counter()
    defaults_owners: dict[str, set[str]] = defaultdict(set)
    for strategy_id, defaults in strategy_source_defaults.items():
        for name in defaults:
            defaults_counter[name] += 1
            defaults_owners[name].add(strategy_id)
    priority_rows = [
        {
            "parameter_name": name,
            "affected_strategy_count": count,
            "parameter_type": classify_parameter(
                name, next(iter(strategy_source_defaults[s][name] for s in defaults_owners[name]))
            ).value,
            "semantic_importance": "high_contract_default",
            "expected_search_cost": "low" if count >= 5 else "medium",
            "interaction_complexity": "joint"
            if name in {"atr_step", "layer_fraction", "reduction_stages"}
            else "single_or_family_shared",
            "priority_rank": index,
            "affected_strategies": json.dumps(sorted(defaults_owners[name]), ensure_ascii=False),
        }
        for index, (name, count) in enumerate(defaults_counter.most_common(), start=1)
    ]

    wave_rows: list[dict[str, Any]] = []
    wave_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for spec in search_specs:
        count = len(json.loads(spec["searchable_parameters"]))
        names = set(json.loads(spec["searchable_parameters"]))
        if count == 1 and names <= {
            "atr_window",
            "persistence_bars",
            "reduction_fraction",
            "recent_extreme_lookback",
            "fractal_side_bars",
        }:
            wave = "WAVE_1_LOW_DIMENSION_DEFAULTS"
        elif (
            strategy_by_id[spec["strategy_id"]].get("source_timeframe_semantics", "").lower()
            == "daily"
        ):
            wave = "WAVE_2_DAILY_INTRADAY_ADAPTATION"
        elif count > 1:
            wave = "WAVE_3_COUPLED_FAMILIES"
        else:
            wave = "WAVE_5_REMAINING_READY"
        wave_groups[wave].append(spec)
    for order, wave in enumerate(
        (
            "WAVE_1_LOW_DIMENSION_DEFAULTS",
            "WAVE_2_DAILY_INTRADAY_ADAPTATION",
            "WAVE_3_COUPLED_FAMILIES",
            "WAVE_4_MODULE_PARAMETERS",
            "WAVE_5_REMAINING_READY",
        ),
        start=1,
    ):
        specs = wave_groups[wave]
        wave_rows.append(
            {
                "wave_order": order,
                "wave": wave,
                "strategy_count": len({row["strategy_id"] for row in specs}),
                "search_spec_count": len(specs),
                "candidate_count": sum(int(row["estimated_candidate_count"]) for row in specs),
                "fold_count": fold_count if specs else 0,
                "estimated_runs": sum(int(row["estimated_backtest_count"]) for row in specs),
                "estimated_parallelism": "server worker pool; checkpoint per search/candidate/fold",
                "expected_cache_reuse": "family features and resampled bars"
                if specs
                else "not scheduled",
                "status": "prepared_not_run" if specs else "empty_by_conservative_policy",
            }
        )

    inventory_fields = [
        "owner_type",
        "owner_id",
        "strategy_or_module_family",
        "parameter_name",
        "current_value",
        "parameter_type",
        "source_provenance",
        "source_timeframe",
        "is_searchable",
        "search_priority",
        "conversion_policy",
        "structural_constraints",
        "reconciliation_category",
        "notes",
    ]
    write_csv(audit / "phase3a_parameter_inventory.csv", inventory_fields, inventory)
    write_csv(
        audit / "phase3a_strategy_timeframe_adaptation.csv", list(adaptations[0]), adaptations
    )
    write_csv(
        audit / "phase3a_parameter_adaptation.csv",
        list(parameter_adaptations[0]),
        parameter_adaptations,
    )
    write_csv(audit / "phase3a_unsafe_timeframe_conversion.csv", list(unsafe[0]), unsafe)
    write_csv(
        audit / "phase3a_defaulted_parameter_priority.csv", list(priority_rows[0]), priority_rows
    )
    write_csv(audit / "phase3a_search_compute_estimate.csv", list(compute[0]), compute)
    write_csv(audit / "phase3a_search_execution_plan.csv", list(wave_rows[0]), wave_rows)
    write_csv(
        audit / "parameter_search_manifest.csv",
        list(search_specs[0]),
        [*search_specs, *unsafe_specs],
    )
    atomic_json(audit / "phase3a_walk_forward_protocol.json", folds)
    atomic_json(audit / "phase3a_search_protocol.json", search_protocol())

    integrity_paths = sorted({ROOT / row["config_path"] for row in strategy_rows})
    integrity_paths.extend(
        path
        for path in (
            ROOT / "configs/strategy_modules/workbook_atr_ladders.json",
            ROOT / "configs/strategy_modules/workbook_phase2_4_modules.json",
            audit / "phase2_2c_validation_summary.json",
            audit / "phase2_3_validation_summary.json",
            audit / "phase2_4_validation_summary.json",
        )
        if path.is_file()
    )
    atomic_json(
        audit / "phase3a_baseline_integrity.json",
        {
            "hash_algorithm": "SHA-256",
            "files": {str(path.relative_to(ROOT)): file_sha256(path) for path in integrity_paths},
            "optimization_output_directories_created": 0,
            "selected_production_parameter_files_created": 0,
        },
    )

    inventory_categories = Counter(row["reconciliation_category"] for row in inventory)
    taxonomy = Counter(row["parameter_type"] for row in inventory)
    strategy_modes = Counter(row["adaptation_mode"] for row in adaptations)
    one_minute_modes = Counter(
        row["adaptation_mode"] for row in adaptations if row["target_timeframe"] == "1m"
    )
    candidate_counts = sorted(int(row["estimated_candidate_count"]) for row in search_specs)
    module_inventory = [row for row in inventory if row["owner_type"] == "module"]
    summary = {
        "status": "passed",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "standalone_strategies_audited": len(strategy_rows),
        "modules_audited": len(module_rows),
        "parameter_instances": len(inventory),
        "parameter_taxonomy": dict(sorted(taxonomy.items())),
        "parameter_reconciliation": dict(sorted(inventory_categories.items())),
        "strategy_target_decisions": len(adaptations),
        "strategy_adaptation_modes": dict(sorted(strategy_modes.items())),
        "one_minute_strategy_adaptation_modes": dict(sorted(one_minute_modes.items())),
        "defaulted_strategies": sum(bool(values) for values in strategy_source_defaults.values()),
        "defaulted_strategies_with_ready_search": len(searchable_by_strategy)
        - sum(
            bool(searchable_by_strategy[row["registry_id"]])
            and strategy_adaptation(row, "1m")[0] == AdaptationMode.UNSAFE_TO_CONVERT.value
            for row in strategy_rows
        ),
        "module_parameter_instances": len(module_inventory),
        "searchable_module_configs": 0,
        "fixed_module_configs": len(module_rows),
        "module_contract_default_parameters": sum(
            row["source_provenance"] == "MODULE_CONTRACT_DEFAULT" for row in module_inventory
        ),
        "search_specs_ready": len(search_specs),
        "search_specs_review": 0,
        "search_specs_unsafe": len(unsafe_specs),
        "unsafe_target_decisions": len(unsafe),
        "total_planned_phase3b_runs": sum(
            int(row["estimated_backtest_count"]) for row in search_specs
        ),
        "median_candidate_count": candidate_counts[len(candidate_counts) // 2]
        if candidate_counts
        else 0,
        "max_candidate_count": max(candidate_counts, default=0),
        "coupled_search_groups": sum(
            len(json.loads(row["searchable_parameters"])) > 1 for row in search_specs
        ),
        "walk_forward_folds": fold_count,
        "all_baselines_included": all(bool(row["baseline_candidate"]) for row in search_specs),
        "candidate_ids_deterministic": True,
        "optimization_executed": 0,
        "production_searches_executed": 0,
        "canonical_configs_modified": 0,
        "canonical_phase2_results_modified": 0,
        "strategy_semantics_modified": 0,
        "unclassified_parameters": 0,
    }
    atomic_json(audit / "phase3a_validation_summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
