#!/usr/bin/env python3
"""Run bounded one-at-a-time persistence sensitivity on the completed screen.

The executor reuses the canonical strategy engine, completed 10m/15m bars and
the compact exact raw-trade execution index.  It never invokes the Phase 3
optimizer and never writes canonical strategy configuration files.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.internal.build_boss_persistence_v2 import directional_persistence_metrics
from scripts.internal.run_boss_multitimeframe_tick_screen import (
    ROOT,
    atomic_json,
    load_symbol,
    run_group_case,
)


RESULT_ROOT = ROOT / "outputs/baseline_evaluation/boss_multitimeframe_tick_screen"
FOLLOWUP_NAME = "persistent_v2_followup"
SENSITIVITY_SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
SENSITIVITY_TIMEFRAMES = ("10m", "15m")
PROVENANCE_KEYS = {
    "source_registry_id", "semantic_provenance", "contracts_applied",
    "defaulted_parameters",
}


def atomic_csv(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    frame.to_csv(temporary, index=False, encoding="utf-8-sig")
    os.replace(temporary, path)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_config_hashes(strategy_ids: list[str]) -> dict[str, str]:
    return {
        strategy_id: sha256_file(ROOT / "strategies" / strategy_id / "config.yaml")
        for strategy_id in sorted(strategy_ids)
    }


def config_payload(strategy_id: str) -> dict[str, Any]:
    return yaml.safe_load(
        (ROOT / "strategies" / strategy_id / "config.yaml").read_text(encoding="utf-8")
    ) or {}


def config_hash(source: dict[str, Any]) -> str:
    params = {
        key: value for key, value in source.get("params", {}).items()
        if key not in PROVENANCE_KEYS
    }
    return hashlib.sha256(
        json.dumps(params, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def load_authorized_spaces() -> dict[tuple[str, str], list[float]]:
    root = ROOT / "outputs/internal_audit/strategy_workbook"
    paths = [
        root / "parameter_search_manifest.csv",
        root / "phase3b_wave3_parameter_search_manifest.csv",
        root / "phase3b_wave5_parameter_search_manifest.csv",
    ]
    result: dict[tuple[str, str], set[float]] = {}
    for path in paths:
        if not path.is_file():
            continue
        for row in pd.read_csv(path).itertuples(index=False):
            try:
                spaces = json.loads(row.candidate_space)
            except (TypeError, json.JSONDecodeError):
                continue
            for parameter, values in spaces.items():
                for value in values:
                    if isinstance(value, (int, float)) and np.isfinite(value):
                        result.setdefault((str(row.strategy_id), str(parameter)), set()).add(float(value))
    return {key: sorted(values) for key, values in result.items()}


def bounded_values(canonical: Any) -> list[Any]:
    if isinstance(canonical, bool) or not isinstance(canonical, (int, float)):
        return [canonical]
    value = float(canonical)
    if isinstance(canonical, int):
        lower = max(1, int(np.floor(value * 0.75)))
        higher = max(lower + 1, int(np.ceil(value * 1.25)))
        values: list[Any] = [lower, canonical, higher]
    else:
        lower = value * 0.75
        higher = value * 1.25
        values = [lower, canonical, higher]
    return list(dict.fromkeys(values))


def admissible(params: dict[str, Any], parameter: str, value: Any) -> bool:
    candidate = dict(params)
    candidate[parameter] = value
    for fast, slow in (("fast_window", "slow_window"), ("ao_fast_window", "ao_slow_window")):
        if fast in candidate and slow in candidate and candidate[fast] >= candidate[slow]:
            return False
    for lower, upper in (
        ("lower_threshold", "upper_threshold"),
        ("exit_lower_threshold", "exit_upper_threshold"),
    ):
        if lower in candidate and upper in candidate and candidate[lower] >= candidate[upper]:
            return False
    if {"lower_threshold", "neutral_threshold", "upper_threshold"} <= candidate.keys():
        if not (
            candidate["lower_threshold"]
            < candidate["neutral_threshold"]
            < candidate["upper_threshold"]
        ):
            return False
    if candidate.get("family") == "ma_rsi_turn_filter":
        if not candidate["lower_threshold"] < 50.0 < candidate["upper_threshold"]:
            return False
    if "adx_entry_threshold" in candidate and "adx_exit_threshold" in candidate:
        if candidate["adx_entry_threshold"] < candidate["adx_exit_threshold"]:
            return False
    return bool(np.isfinite(float(value)) and float(value) > 0)


def select_values(
    *, members: list[str], parameter: str, canonical: Any,
    params: dict[str, Any], authorized: dict[tuple[str, str], list[float]],
) -> tuple[list[Any], str]:
    authorized_values = sorted(
        {
            type(canonical)(value)
            for member in members
            for value in authorized.get((member, parameter), [])
            if admissible(params, parameter, type(canonical)(value))
        }
    )
    if authorized_values:
        lower = [value for value in authorized_values if value < float(canonical)]
        higher = [value for value in authorized_values if value > float(canonical)]
        values = ([max(lower)] if lower else []) + [canonical] + ([min(higher)] if higher else [])
        return list(dict.fromkeys(values)), "PHASE3_AUTHORIZED_NEAREST_NEIGHBORS"
    values = [value for value in bounded_values(canonical) if admissible(params, parameter, value)]
    return values, "PREDECLARED_BOUNDED_0.75X_1.25X"


def prepare_manifest(root: Path, output: Path) -> pd.DataFrame:
    metrics = pd.read_csv(root / "persistent_position_metrics_v2.csv")
    audit = pd.read_csv(root / "persistence_parameter_audit.csv")
    classes = metrics[["strategy_id", "persistence_structure_class"]].drop_duplicates()
    eligible = metrics.groupby("strategy_id", as_index=False).agg(
        any_positive_Return=("Return", lambda values: bool((values > 0).any())),
        any_positive_BE=("BE", lambda values: bool((values > 0).any())),
        any_persistent=("directionally_persistent", "max"),
    )
    eligible = eligible.merge(classes, on="strategy_id", validate="one_to_one")
    eligible = eligible[
        eligible.persistence_structure_class.eq("PERSISTENCE_PARAMETER_TUNABLE")
        & (
            eligible.any_positive_Return
            | eligible.any_positive_BE
            | eligible.any_persistent.astype(bool)
        )
    ]
    eligible_ids = set(eligible.strategy_id)
    audit = audit[audit.strategy_id.isin(eligible_ids)].copy()
    identity = metrics[["strategy_id", "semantic_execution_hash"]].drop_duplicates()
    audit = audit.merge(identity, on="strategy_id", validate="many_to_one")
    authorized = load_authorized_spaces()
    rows: list[dict[str, Any]] = []
    for semantic_hash, group in audit.groupby("semantic_execution_hash", sort=True):
        members = sorted(group.strategy_id.unique())
        representative = members[0]
        source = config_payload(representative)
        params = {
            key: value for key, value in source.get("params", {}).items()
            if key not in PROVENANCE_KEYS
        }
        for parameter, parameter_group in group.groupby("parameter", sort=True):
            canonical_values = parameter_group.canonical_value.unique()
            if len(canonical_values) != 1:
                raise ValueError(f"canonical value mismatch for {semantic_hash}/{parameter}")
            canonical_from_config = params[parameter]
            if not np.isclose(float(canonical_values[0]), float(canonical_from_config)):
                raise ValueError(f"audit/config mismatch for {representative}/{parameter}")
            values, source_name = select_values(
                members=members, parameter=parameter, canonical=canonical_from_config,
                params=params, authorized=authorized,
            )
            if len(values) < 2:
                # An existing parameter with no valid neighbouring setting is
                # documented but cannot support sensitivity execution.
                values = [canonical_from_config]
            for value in values:
                modified = copy.deepcopy(source)
                modified["params"][parameter] = value
                modified_hash = config_hash(modified)
                is_canonical = bool(np.isclose(float(value), float(canonical_from_config)))
                for symbol in SENSITIVITY_SYMBOLS:
                    for timeframe in SENSITIVITY_TIMEFRAMES:
                        rows.append(
                            {
                                "semantic_execution_hash": semantic_hash,
                                "representative_strategy_id": representative,
                                "member_strategy_ids": ";".join(members),
                                "parameter": parameter,
                                "canonical_value": canonical_from_config,
                                "tested_value": value,
                                "value_relation": (
                                    "CANONICAL" if is_canonical else
                                    "LOWER" if float(value) < float(canonical_from_config) else "HIGHER"
                                ),
                                "candidate_source": source_name,
                                "symbol": symbol,
                                "timeframe": timeframe,
                                "sensitivity_config_hash": modified_hash,
                                "run_source": "REUSE_COMPLETED_CANONICAL_MATRIX" if is_canonical else "TARGETED_ONE_PARAMETER_SENSITIVITY",
                                "one_parameter_only": True,
                                "phase3_optimizer_invoked": False,
                            }
                        )
    manifest = pd.DataFrame(rows).sort_values(
        ["symbol", "timeframe", "semantic_execution_hash", "parameter", "tested_value"]
    )
    if manifest.empty:
        raise ValueError("empty sensitivity manifest")
    atomic_csv(output / "persistence_parameter_sensitivity_manifest.csv", manifest)
    atomic_json(
        output / "persistence_parameter_sensitivity_protocol.json",
        {
            "version": "PERSISTENCE_SENSITIVITY_V2_OAT_1",
            "strategy_eligibility": (
                "PERSISTENCE_PARAMETER_TUNABLE and any Return>0 or BE>0 or persistent case"
            ),
            "symbols": list(SENSITIVITY_SYMBOLS),
            "timeframes": list(SENSITIVITY_TIMEFRAMES),
            "one_parameter_at_a_time": True,
            "maximum_values_per_parameter": 3,
            "fallback_value_contract": "canonical plus admissible 0.75x and 1.25x neighbours",
            "selection_after_results": False,
            "phase3_optimizer_invoked": False,
            "eligible_strategy_count": int(len(eligible_ids)),
            "semantic_group_count": int(manifest.semantic_execution_hash.nunique()),
            "parameter_contract_count": int(
                manifest[["semantic_execution_hash", "parameter"]].drop_duplicates().shape[0]
            ),
            "logical_rows": len(manifest),
            "targeted_physical_cases": int(
                manifest[manifest.run_source.eq("TARGETED_ONE_PARAMETER_SENSITIVITY")]
                [["sensitivity_config_hash", "symbol", "timeframe"]].drop_duplicates().shape[0]
            ),
        },
    )
    return manifest


def case_path(output: Path, row: Any) -> Path:
    return (
        output / "sensitivity_cases" / f"symbol={row.symbol}" / f"timeframe={row.timeframe}"
        / f"config={row.sensitivity_config_hash}"
    )


def run_symbol(args: argparse.Namespace) -> int:
    output = args.output_root or args.root / FOLLOWUP_NAME
    manifest_path = output / "persistence_parameter_sensitivity_manifest.csv"
    manifest = pd.read_csv(manifest_path) if manifest_path.is_file() else prepare_manifest(args.root, output)
    work = manifest[
        manifest.symbol.eq(args.symbol)
        & manifest.run_source.eq("TARGETED_ONE_PARAMETER_SENSITIVITY")
    ].drop_duplicates(["sensitivity_config_hash", "symbol", "timeframe"])
    if args.shard_count > 1:
        shard = work.sensitivity_config_hash.map(
            lambda value: int(str(value)[:16], 16) % args.shard_count
        )
        work = work[shard.eq(args.shard_index)].copy()
    progress_path = output / (
        f"sensitivity_progress_{args.symbol}_shard{args.shard_index:02d}of{args.shard_count:02d}.json"
    )
    window = json.loads((args.root / "boss_tick_index_data_window.json").read_text(encoding="utf-8"))
    start = window["common_start"]
    end_exclusive = window["common_end_exclusive"]
    end_inclusive = (date.fromisoformat(end_exclusive) - timedelta(days=1)).isoformat()
    end_ns = int(pd.Timestamp(end_exclusive, tz="UTC").value)
    bars, funding, execution, tick_prices, waits = load_symbol(
        args.market_root, args.root / "tick_execution_index", args.symbol, start, end_inclusive
    )
    completed = 0
    failures = 0
    for item in work.itertuples(index=False):
        destination = case_path(output, item)
        summary_path = destination / "summary.json"
        if summary_path.is_file():
            saved = json.loads(summary_path.read_text(encoding="utf-8"))
            if saved.get("status") == "COMPLETED":
                completed += 1
                continue
        try:
            members = str(item.member_strategy_ids).split(";")
            source = config_payload(item.representative_strategy_id)
            source = copy.deepcopy(source)
            canonical_typed = source["params"][item.parameter]
            typed_value = type(canonical_typed)(item.tested_value)
            # CSV round-tripping promotes integer columns to float whenever a
            # family also contains float-valued candidates.  Recover the exact
            # predeclared representation by matching the frozen config hash.
            matched = False
            for possible in [typed_value, item.tested_value]:
                source["params"][item.parameter] = possible
                if config_hash(source) == item.sensitivity_config_hash:
                    matched = True
                    break
            if not matched:
                raise ValueError("sensitivity config hash drift")
            summary, review = run_group_case(
                representative=item.representative_strategy_id,
                members=members,
                source=source,
                semantic_hash=item.sensitivity_config_hash,
                symbol=item.symbol,
                timeframe=item.timeframe,
                bars=bars,
                funding=funding,
                execution=execution,
                tick_prices=tick_prices,
                waits=waits,
                end_ns=end_ns,
            )
            v2 = directional_persistence_metrics(
                review[["event_time_ns", "executed_position"]]
            )
            summary.update(
                {
                    **v2,
                    "parameter": item.parameter,
                    "canonical_value": item.canonical_value,
                    "tested_value": item.tested_value,
                    "sensitivity_config_hash": item.sensitivity_config_hash,
                    "run_contract": "PERSISTENCE_SENSITIVITY_V2_OAT_1",
                }
            )
            atomic_json(summary_path, summary)
            destination.mkdir(parents=True, exist_ok=True)
            temporary = destination / "review_timeseries.parquet.tmp"
            review.to_parquet(temporary, index=False, compression="zstd")
            os.replace(temporary, destination / "review_timeseries.parquet")
            completed += 1
        except Exception as exc:
            failures += 1
            atomic_json(
                summary_path,
                {
                    "status": "FAILED",
                    "symbol": item.symbol,
                    "timeframe": item.timeframe,
                    "sensitivity_config_hash": item.sensitivity_config_hash,
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )
        atomic_json(
            progress_path,
            {
                "status": "RUNNING",
                "symbol": args.symbol,
                "physical_planned": len(work),
                "physical_completed": completed,
                "physical_failures": failures,
            },
        )
    atomic_json(
        progress_path,
        {
            "status": "PASSED" if failures == 0 else "COMPLETED_WITH_FAILURES",
            "symbol": args.symbol,
            "physical_planned": len(work),
            "physical_completed": completed,
            "physical_failures": failures,
        },
    )
    return 0 if failures == 0 else 2


def physical_results(output: Path) -> pd.DataFrame:
    rows = []
    for path in sorted((output / "sensitivity_cases").glob("symbol=*/timeframe=*/config=*/summary.json")):
        row = json.loads(path.read_text(encoding="utf-8"))
        row["summary_path"] = str(path)
        row["review_timeseries_path"] = str(path.parent / "review_timeseries.parquet")
        rows.append(row)
    return pd.DataFrame(rows)


def finalize(root: Path, output: Path) -> dict[str, Any]:
    manifest = pd.read_csv(output / "persistence_parameter_sensitivity_manifest.csv")
    metrics = pd.read_csv(root / "persistent_position_metrics_v2.csv")
    expected_physical = manifest[manifest.run_source.eq("TARGETED_ONE_PARAMETER_SENSITIVITY")][
        ["sensitivity_config_hash", "symbol", "timeframe"]
    ].drop_duplicates()
    physical = physical_results(output)
    if len(physical):
        physical = physical.merge(
            expected_physical,
            on=["sensitivity_config_hash", "symbol", "timeframe"],
            how="inner",
            validate="many_to_one",
        )
    failures = physical[physical.status.ne("COMPLETED")] if len(physical) else physical
    completed_keys = physical.loc[
        physical.status.eq("COMPLETED"), ["sensitivity_config_hash", "symbol", "timeframe"]
    ].drop_duplicates() if len(physical) else pd.DataFrame(columns=expected_physical.columns)
    missing = expected_physical.merge(
        completed_keys, on=["sensitivity_config_hash", "symbol", "timeframe"], how="left", indicator=True
    ).query("_merge != 'both'")
    if len(failures) or len(missing):
        raise ValueError(f"sensitivity not terminal: failures={len(failures)}, missing={len(missing)}")

    physical_columns = {
        "Return_fee0": "Return",
        "Return_5bp": "Return_5bp",
        "BE_bps": "BE",
        "Turnover_raw": "turnover_raw",
        "Turnover_pct": "turnover_percent",
    }
    physical = physical.rename(columns=physical_columns)
    result_rows: list[dict[str, Any]] = []
    canonical_lookup = metrics.set_index(
        ["strategy_id", "symbol", "timeframe"]
    )
    physical_lookup = physical.set_index(
        ["sensitivity_config_hash", "symbol", "timeframe"]
    )
    for row in manifest.itertuples(index=False):
        for strategy_id in str(row.member_strategy_ids).split(";"):
            if row.run_source == "REUSE_COMPLETED_CANONICAL_MATRIX":
                source = canonical_lookup.loc[(strategy_id, row.symbol, row.timeframe)]
                review_path = source.review_timeseries_path
            else:
                source = physical_lookup.loc[(row.sensitivity_config_hash, row.symbol, row.timeframe)]
                review_path = source.review_timeseries_path
            result_rows.append(
                {
                    "strategy_id": strategy_id,
                    "representative_strategy_id": row.representative_strategy_id,
                    "semantic_execution_hash": row.semantic_execution_hash,
                    "parameter": row.parameter,
                    "canonical_value": row.canonical_value,
                    "tested_value": row.tested_value,
                    "value_relation": row.value_relation,
                    "candidate_source": row.candidate_source,
                    "symbol": row.symbol,
                    "timeframe": row.timeframe,
                    "nonflat_fraction": float(source.nonflat_fraction_v2),
                    "long_fraction": float(source.long_fraction_v2),
                    "short_fraction": float(source.short_fraction_v2),
                    "flat_fraction": float(source.flat_fraction_v2),
                    "median_directional_run_hours": float(source.median_directional_run_hours),
                    "P90_directional_run_hours": float(source.P90_directional_run_hours),
                    "sign_switch_count": int(source.sign_switch_count_v2),
                    "sign_switches_per_day": float(source.sign_switches_per_day),
                    "turnover_raw": float(source.turnover_raw),
                    "turnover_percent": float(source.turnover_percent),
                    "Return": float(source.Return),
                    "BE": float(source.BE),
                    "Return_5bp": float(source.Return_5bp),
                    "MDD": float(source.MDD),
                    "directionally_persistent": bool(source.directionally_persistent),
                    "review_timeseries_path": review_path,
                    "run_source": row.run_source,
                    "one_parameter_only": True,
                }
            )
    sensitivity = pd.DataFrame(result_rows)
    canonical = sensitivity[sensitivity.value_relation.eq("CANONICAL")][
        [
            "strategy_id", "parameter", "symbol", "timeframe",
            "nonflat_fraction", "median_directional_run_hours", "P90_directional_run_hours",
            "sign_switches_per_day", "turnover_raw", "Return", "BE", "Return_5bp",
        ]
    ].rename(columns=lambda value: value if value in {
        "strategy_id", "parameter", "symbol", "timeframe"
    } else f"canonical_{value}")
    sensitivity = sensitivity.merge(
        canonical, on=["strategy_id", "parameter", "symbol", "timeframe"],
        how="left", validate="many_to_one",
    )
    for metric in (
        "nonflat_fraction", "median_directional_run_hours", "P90_directional_run_hours",
        "sign_switches_per_day", "turnover_raw", "Return", "BE", "Return_5bp",
    ):
        sensitivity[f"delta_{metric}"] = sensitivity[metric] - sensitivity[f"canonical_{metric}"]
    sensitivity["persistence_improved"] = (
        (sensitivity.delta_median_directional_run_hours > 1e-12)
        | (sensitivity.delta_sign_switches_per_day < -1e-12)
    ) & sensitivity.value_relation.ne("CANONICAL")
    sensitivity["acceptable_economics"] = (sensitivity.Return > 0) & (sensitivity.BE > 0)
    sensitivity["survives_5bp"] = sensitivity.Return_5bp > 0
    sensitivity["parameter_effect"] = np.select(
        [
            (sensitivity.delta_median_directional_run_hours > 0) & (sensitivity.delta_sign_switches_per_day < 0),
            sensitivity.delta_median_directional_run_hours > 0,
            sensitivity.delta_sign_switches_per_day < 0,
        ],
        ["LONGER_RUNS_AND_FEWER_SWITCHES", "LONGER_RUNS", "FEWER_SWITCHES"],
        default="NO_PERSISTENCE_IMPROVEMENT",
    )
    atomic_csv(output / "persistence_parameter_sensitivity_v2.csv", sensitivity)

    improved_rows = []
    noncanonical = sensitivity[sensitivity.value_relation.ne("CANONICAL")]
    for strategy_id, group in noncanonical.groupby("strategy_id", sort=True):
        improved = group[group.persistence_improved]
        acceptable = improved[improved.acceptable_economics]
        improved_rows.append(
            {
                "strategy_id": strategy_id,
                "tested_parameter_count": int(group.parameter.nunique()),
                "tested_noncanonical_case_count": len(group),
                "persistence_improved": bool(len(improved)),
                "persistence_improved_case_count": len(improved),
                "classification": (
                    "PERSISTENCE_IMPROVABLE_WITH_EXISTING_PARAMETER"
                    if len(improved) else "NO_TESTED_PERSISTENCE_IMPROVEMENT"
                ),
                "acceptable_economics_case_count": len(acceptable),
                "structural_economic_label": (
                    "PERSISTENCE_IMPROVABLE_WITH_ACCEPTABLE_ECONOMICS"
                    if len(acceptable) else "NO_TESTED_ACCEPTABLE_ECONOMIC_IMPROVEMENT"
                ),
                "5bp_surviving_improved_case_count": int((improved.Return_5bp > 0).sum()),
                "parameters_with_improvement": ";".join(sorted(improved.parameter.unique())),
                "canonical_config_modified": False,
            }
        )
    improvable = pd.DataFrame(improved_rows)
    atomic_csv(output / "persistence_improvable_strategies.csv", improvable)

    before = json.loads((output / "canonical_config_hashes_before.json").read_text(encoding="utf-8"))
    after = canonical_config_hashes(list(before))
    if before != after:
        raise ValueError("canonical strategy configuration changed during sensitivity")
    validation = {
        "status": "PASSED",
        "protocol": "PERSISTENCE_SENSITIVITY_V2_OAT_1",
        "eligible_strategy_count": int(sensitivity.strategy_id.nunique()),
        "semantic_group_count": int(sensitivity.semantic_execution_hash.nunique()),
        "logical_sensitivity_rows": len(sensitivity),
        "targeted_physical_cases_expected": len(expected_physical),
        "targeted_physical_cases_completed": len(completed_keys),
        "failures": 0,
        "strategies_with_persistence_improvement": int(improvable.persistence_improved.sum()),
        "strategies_with_acceptable_economic_improvement": int(
            improvable.structural_economic_label.eq(
                "PERSISTENCE_IMPROVABLE_WITH_ACCEPTABLE_ECONOMICS"
            ).sum()
        ),
        "one_parameter_only": bool(sensitivity.one_parameter_only.all()),
        "canonical_config_changes": 0,
        "phase3_optimizer_invoked": False,
        "full_matrix_rerun": 0,
        "tick_index_rebuilt": 0,
    }
    atomic_json(output / "persistence_parameter_sensitivity_validation.json", validation)
    return validation


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=RESULT_ROOT)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--market-root", type=Path, default=ROOT / "historical_data/market_data")
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--symbol", choices=SENSITIVITY_SYMBOLS)
    parser.add_argument("--shard-count", type=int, default=1)
    parser.add_argument("--shard-index", type=int, default=0)
    parser.add_argument("--finalize", action="store_true")
    args = parser.parse_args()
    if args.shard_count < 1 or not 0 <= args.shard_index < args.shard_count:
        parser.error("require shard_count>=1 and 0<=shard_index<shard_count")
    output = args.output_root or args.root / FOLLOWUP_NAME
    if args.prepare:
        manifest = prepare_manifest(args.root, output)
        strategy_ids = sorted(
            set(";".join(manifest.member_strategy_ids.astype(str)).split(";"))
        )
        atomic_json(output / "canonical_config_hashes_before.json", canonical_config_hashes(strategy_ids))
        print(json.dumps({"status": "PREPARED", "rows": len(manifest)}, indent=2))
        return 0
    if args.symbol:
        return run_symbol(args)
    if args.finalize:
        print(json.dumps(finalize(args.root, output), indent=2))
        return 0
    parser.error("choose --prepare, --symbol, or --finalize")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
