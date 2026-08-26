#!/usr/bin/env python3
# ruff: noqa: E402,I001
"""Run leakage-safe Phase 3B Wave 5 remaining authorized searches."""

from __future__ import annotations

import hashlib
import itertools
import json
import math
import sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures import as_completed
from pathlib import Path
from typing import Any

import matplotlib as mpl

mpl.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from results.strategy_evaluation import build_additive_strategy_evaluation_from_columns
from results.trade_episode import build_de_risk_episodes
from scripts.internal import run_phase3b_wave1 as wave1
from scripts.internal.preflight_phase3b_wave5 import MANIFEST
from scripts.internal.preflight_phase3b_wave5 import PARENT_MANIFEST
from scripts.internal.preflight_phase3b_wave5 import WAVE3_SUMMARY
from scripts.internal.preflight_phase3b_wave5 import atomic_csv
from scripts.internal.preflight_phase3b_wave5 import atomic_json
from scripts.internal.preflight_phase3b_wave5 import enumerate_candidates
from scripts.internal.preflight_phase3b_wave5 import equivalence_rows
from scripts.internal.preflight_phase3b_wave5 import file_hash
from scripts.internal.preflight_phase3b_wave5 import read_csv
from scripts.internal.preflight_phase3b_wave5 import wave5_specs
from strategy_framework.parameter_search import PROTOCOL_VERSION
from strategy_framework.parameter_search import generate_candidates


AUDIT = ROOT / "outputs/internal_audit/strategy_workbook"
DEFAULT_OUTPUT = ROOT / "outputs/parameter_search/phase3b_wave5"
FOLDS = AUDIT / "phase3a_walk_forward_protocol.json"
PROVENANCE = AUDIT / "phase3b_wave5_manifest_provenance.json"
LOGICAL_PLAN = 518


def sha_payload(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()


def load_wave5() -> tuple[list[dict[str, str]], dict[str, Any], dict[str, Any]]:
    specs = wave5_specs(read_csv(MANIFEST))
    folds = json.loads(FOLDS.read_text(encoding="utf-8"))
    protocol = json.loads((AUDIT / "phase3a_search_protocol.json").read_text(encoding="utf-8"))
    return specs, folds, protocol


def wave5_code_hash() -> str:
    digest = hashlib.sha256(wave1.code_hash().encode())
    for path in (
        ROOT / "scripts/internal/run_phase3b_wave5.py",
        ROOT / "scripts/internal/preflight_phase3b_wave5.py",
        MANIFEST,
        PROVENANCE,
    ):
        digest.update(path.relative_to(ROOT).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def integrity_snapshot(strategy_root: Path) -> dict[str, Any]:
    """Extend the established baseline snapshot with immutable Wave 1/3 results."""
    snapshot = wave1.integrity_snapshot(strategy_root)
    extra = (
        ROOT / "outputs/parameter_search/phase3b_wave1/phase3b_wave1_validation_summary.json",
        ROOT / "outputs/parameter_search/phase3b_wave3/phase3b_wave3_validation_summary.json",
        ROOT / "outputs/deliverables/phase3b_wave1.zip",
        ROOT / "outputs/deliverables/phase3b_wave3.zip",
    )
    missing = [str(path) for path in extra if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"protected Phase 3B artifacts missing: {missing}")
    snapshot["files"].update(
        {str(path.relative_to(ROOT)): file_hash(path) for path in extra}
    )
    return snapshot


def preflight(
    strategy_root: Path, market_root: Path, output_root: Path
) -> dict[str, Any]:
    specs, folds, _ = load_wave5()
    wave3_summary = json.loads(WAVE3_SUMMARY.read_text(encoding="utf-8"))
    raw = valid = rejected = baselines = 0
    errors: list[str] = []
    for spec in specs:
        enumerated, raw_count, rejected_count = enumerate_candidates(spec)
        raw += raw_count
        valid += len(enumerated)
        rejected += rejected_count
        try:
            generated = generate_candidates(spec)
        except Exception as exc:
            errors.append(f"{spec['search_id']}: {exc}")
            continue
        baselines += sum(candidate.candidate_role == "BASELINE" for candidate in generated)
    logical = 2 * valid * len(folds["folds"]) + 2 * len(specs) * len(folds["folds"])
    checks = {
        "wave3_release_decision": wave3_summary.get("release_decision") == "WAVE5_READY",
        "protocol_version_fixed": wave3_summary.get("protocol_version") == PROTOCOL_VERSION,
        "wave5_spec_count": len(specs) == 7,
        "wave5_candidate_count": valid == 30,
        "fold_count": len(folds["folds"]) == 7,
        "logical_evaluation_count": logical == LOGICAL_PLAN,
        "all_baselines_present": baselines == 7,
        "all_candidates_constraint_valid": rejected == 0,
        "provenance_present": PROVENANCE.is_file(),
        "parent_manifest_unchanged": file_hash(PARENT_MANIFEST)
        == json.loads(PROVENANCE.read_text(encoding="utf-8"))["source_sha256"],
        "market_root_exists": market_root.is_dir() and any(market_root.rglob("*.parquet")),
        "strategy_root_exists": strategy_root.is_dir(),
    }
    failed = [name for name, passed in checks.items() if not passed] + errors
    equivalence = equivalence_rows(specs)
    atomic_csv(output_root / "phase3b_wave5_equivalence_manifest.csv", equivalence)
    report = {
        "status": "PASSED" if not failed else "FAILED",
        "release_decision": "WAVE5_EXECUTION_AUTHORIZED" if not failed else "WAVE5_BLOCKED",
        "protocol_version": PROTOCOL_VERSION,
        "manifest_provenance_sha256": file_hash(PROVENANCE),
        "checks": checks,
        "errors": failed,
        "wave5_specs": len(specs),
        "raw_generated_candidates": raw,
        "constraint_valid_candidates": valid,
        "constraint_rejected_candidates": rejected,
        "baseline_candidates": baselines,
        "folds": len(folds["folds"]),
        "logical_evaluations": logical,
        "code_hash": wave5_code_hash(),
        "data_provenance": wave1.data_provenance(market_root),
        "parent_manifest_sha256": file_hash(PARENT_MANIFEST),
        "amended_manifest_sha256": file_hash(MANIFEST),
    }
    atomic_json(output_root / "preflight_validation.json", report)
    if failed:
        raise RuntimeError(f"Wave 5 preflight failed: {failed}")
    return report


def overall(frame: pd.DataFrame, strategy_id: str) -> dict[str, Any]:
    _, metrics = build_additive_strategy_evaluation_from_columns(
        event_time_ns=frame.event_time_ns,
        trading_return=frame.trading_return,
        funding_return=frame.funding_return,
        turnover=frame.turnover,
        executed_direction=frame.direction,
    )
    _, episodes = build_de_risk_episodes(
        event_time_ns=frame.event_time_ns,
        executed_position=frame.direction,
        turnover_increment=frame.turnover,
        gross_return_increment=frame.total_return,
        strategy=strategy_id,
        symbol="BTCUSDT",
        granularity="1m",
        lag="lag1m",
        premium_mode="included",
    )
    return {
        **metrics["included"],
        "trade_count": episodes["completed_episode_count"],
        "median_trade_be_bps": episodes["break_even_bps_median"],
    }


def cumulative(frame: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    returns = np.cumsum(frame.total_return.to_numpy(float))
    turnover = np.cumsum(frame.turnover.to_numpy(float))
    equity = 1.0 + returns
    peak = np.maximum.accumulate(np.r_[1.0, equity])[1:]
    drawdown = np.divide(equity, peak, out=np.zeros_like(equity), where=peak > 0) - 1.0
    return returns, turnover, drawdown


def make_oos_figure(
    root: Path,
    spec: dict[str, str],
    selected: pd.DataFrame,
    baseline: pd.DataFrame,
    fold_boundaries: list[int],
) -> None:
    sr, st, sd = cumulative(selected)
    br, bt, bd = cumulative(baseline)
    time = pd.to_datetime(selected.event_time_ns, unit="ns", utc=True)
    fig, axes = plt.subplots(3, 1, figsize=(15, 10), sharex=True)
    axes[0].plot(time, sr * 100, label="Walk-forward selected")
    axes[0].plot(time, br * 100, label="Canonical baseline")
    axes[0].set_ylabel("Cumulative Return (1x, %)")
    axes[1].plot(time, st, label="Selected")
    axes[1].plot(time, bt, label="Baseline")
    axes[1].set_ylabel("Cumulative Turnover (x)")
    axes[2].plot(time, sd * 100, label="Selected")
    axes[2].plot(time, bd * 100, label="Baseline")
    axes[2].set_ylabel("Drawdown (%)")
    axes[2].set_xlabel("UTC time")
    for axis in axes:
        axis.grid(alpha=0.2)
        axis.legend()
        for boundary in fold_boundaries:
            if 0 <= boundary < len(time):
                axis.axvline(time.iloc[boundary], color="0.5", alpha=0.25, linestyle="--")
    fig.suptitle(f"{spec['strategy_id']} — Phase 3B Wave 5 OOS — 1m lag1m Premium Included")
    fig.tight_layout()
    fig.savefig(root / "oos_selected_vs_baseline.png", dpi=150)
    plt.close(fig)


def parameter_diagnostics(
    root: Path,
    spec: dict[str, str],
    selections: list[dict[str, str]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    names = json.loads(spec["searchable_parameters"])
    space = json.loads(spec["candidate_space"])
    baseline = json.loads(spec["baseline_candidate"])["parameters"]
    paths = {row["fold_id"]: json.loads(row["selected_parameters"]) for row in selections}
    boundary_rows: list[dict[str, Any]] = []
    normalized: list[float] = []
    flags: list[str] = []
    for name in names:
        values = [parameters[name] for parameters in paths.values()]
        authorized = space[name]
        low, high = min(authorized), max(authorized)
        low_count, high_count = values.count(low), values.count(high)
        both = low_count > 0 and high_count > 0
        drift = (
            (float(max(values)) - float(min(values))) / (float(high) - float(low))
            if all(isinstance(value, (int, float)) for value in values) and high != low
            else 0.0
        )
        normalized.append(drift)
        if both:
            flags.append(f"{name}:BOTH_BOUNDARIES_SELECTED")
        elif low_count:
            flags.append(f"{name}:LOW_BOUNDARY_SELECTION")
        elif high_count:
            flags.append(f"{name}:HIGH_BOUNDARY_SELECTION")
        boundary_rows.append(
            {
                "search_id": spec["search_id"],
                "strategy_id": spec["strategy_id"],
                "parameter": name,
                "authorized_low": low,
                "authorized_high": high,
                "selected_low_count": low_count,
                "selected_high_count": high_count,
                "both_boundaries_selected": both,
                "normalized_drift": drift,
                "boundary_flag": flags[-1] if flags and flags[-1].startswith(name) else "NONE",
            }
        )
    selected_vectors = [json.dumps(value, sort_keys=True) for value in paths.values()]
    transitions = sum(left != right for left, right in itertools.pairwise(selected_vectors))
    full_range = any(row["both_boundaries_selected"] for row in boundary_rows)
    stability = {
        "search_id": spec["search_id"],
        "strategy_id": spec["strategy_id"],
        "searched_parameters": json.dumps(names),
        "selected_config_path": json.dumps(paths, sort_keys=True),
        "unique_selected_configs": len(set(selected_vectors)),
        "baseline_selected_folds": sum(
            str(row["baseline_won"]).lower() == "true" for row in selections
        ),
        "parameter_selection_frequency": json.dumps(
            {name: dict(Counter(str(value[name]) for value in paths.values())) for name in names},
            sort_keys=True,
        ),
        "parameter_ranges": json.dumps(
            {name: [min(value[name] for value in paths.values()), max(value[name] for value in paths.values())] for name in names},
            sort_keys=True,
        ),
        "normalized_parameter_drift": max(normalized, default=0.0),
        "joint_config_transition_rate": transitions / max(1, len(selected_vectors) - 1),
        "stability_flag": "FULL_RANGE_DRIFT" if full_range else "NO_FULL_RANGE_DRIFT",
        "boundary_flags": ";".join(flags) or "NONE",
    }
    fig, axes = plt.subplots(len(names), 1, figsize=(12, 3.5 * len(names)), sharex=True)
    axes = np.atleast_1d(axes)
    folds = list(paths)
    for axis, name in zip(axes, names, strict=True):
        values = [paths[fold][name] for fold in folds]
        axis.step(folds, values, where="mid", label=name)
        axis.axhline(baseline[name], color="black", linestyle="--", label="baseline")
        axis.axhline(min(space[name]), color="0.6", linestyle=":", label="authorized bounds")
        axis.axhline(max(space[name]), color="0.6", linestyle=":")
        axis.set_ylabel(name)
        axis.grid(alpha=0.2)
        axis.legend()
    axes[-1].set_xlabel("Walk-forward fold")
    fig.suptitle(f"{spec['strategy_id']} — Coupled parameter path")
    fig.tight_layout()
    fig.savefig(root / "parameter_path.png", dpi=150)
    plt.close(fig)
    return stability, boundary_rows


def neighborhood_diagnostics(
    spec: dict[str, str],
    selections: list[dict[str, str]],
    candidate_rows: list[dict[str, str]],
) -> list[dict[str, Any]]:
    """Compare each validation winner with immediate authorized-grid neighbors only."""
    names = sorted(json.loads(spec["searchable_parameters"]))
    space = json.loads(spec["candidate_space"])
    indexes = {name: {value: index for index, value in enumerate(space[name])} for name in names}
    by_fold: dict[str, list[dict[str, Any]]] = {}
    for row in candidate_rows:
        if row["split"] != "VALIDATION":
            continue
        by_fold.setdefault(row["fold_id"], []).append(
            {**row, "parsed_parameters": json.loads(row["parameters"])}
        )
    records: list[dict[str, Any]] = []
    for selection in selections:
        fold_rows = by_fold[selection["fold_id"]]
        selected = next(
            row for row in fold_rows if row["candidate_id"] == selection["selected_candidate_id"]
        )
        selected_parameters = selected["parsed_parameters"]
        neighbor_ids: list[str] = []
        neighbor_returns: list[float] = []
        for candidate in fold_rows:
            if str(candidate.get("eligible", "")).lower() != "true":
                continue
            differences = [
                name
                for name in names
                if candidate["parsed_parameters"][name] != selected_parameters[name]
            ]
            if len(differences) != 1:
                continue
            name = differences[0]
            if abs(
                indexes[name][candidate["parsed_parameters"][name]]
                - indexes[name][selected_parameters[name]]
            ) != 1:
                continue
            neighbor_ids.append(candidate["candidate_id"])
            neighbor_returns.append(float(candidate["return_1x"]))
        selected_return = float(selected["return_1x"])
        isolated = (
            len(neighbor_returns) >= 2
            and selected_return > max(neighbor_returns) + 1e-12
        )
        records.append(
            {
                "search_id": spec["search_id"],
                "strategy_id": spec["strategy_id"],
                "fold_id": selection["fold_id"],
                "selected_candidate_id": selected["candidate_id"],
                "selected_validation_return": selected_return,
                "eligible_immediate_neighbor_count": len(neighbor_returns),
                "neighbor_candidate_ids": json.dumps(neighbor_ids),
                "best_neighbor_validation_return": max(neighbor_returns) if neighbor_returns else None,
                "minimum_neighbor_return_gap": (
                    selected_return - max(neighbor_returns) if neighbor_returns else None
                ),
                "isolated_validation_optimum": isolated,
                "neighborhood_flag": (
                    "ISOLATED_VALIDATION_OPTIMUM" if isolated else "NOT_ISOLATED_VALIDATION_OPTIMUM"
                ),
            }
        )
    return records


def aggregate_phase3b(output_root: Path) -> dict[str, Any]:
    """Build a read-only 65-spec descriptive reconciliation without re-selection."""
    roots = {
        1: ROOT / "outputs/parameter_search/phase3b_wave1",
        3: ROOT / "outputs/parameter_search/phase3b_wave3",
        5: output_root,
    }
    manifests = {
        1: PARENT_MANIFEST,
        3: AUDIT / "phase3b_wave3_parameter_search_manifest.csv",
        5: MANIFEST,
    }
    output_rows: list[dict[str, Any]] = []
    for wave, root in roots.items():
        oos = read_csv(root / f"phase3b_wave{wave}_oos_summary.csv")
        tests = read_csv(root / f"phase3b_wave{wave}_fold_test_metrics.csv")
        candidates = read_csv(root / f"phase3b_wave{wave}_candidate_metrics.csv")
        selections = read_csv(root / f"phase3b_wave{wave}_fold_selections.csv")
        specs_by_id = {row["search_id"]: row for row in read_csv(manifests[wave])}
        for row in oos:
            search_id = row["search_id"]
            fold_tests = [item for item in tests if item["search_id"] == search_id]
            positive = sum(max(0.0, float(item["return_delta"])) for item in fold_tests)
            dominated = positive > 0 and max(float(item["return_delta"]) for item in fold_tests) > 0.5 * positive
            neighborhood = neighborhood_diagnostics(
                specs_by_id[search_id],
                [item for item in selections if item["search_id"] == search_id],
                [item for item in candidates if item["search_id"] == search_id],
            )
            output_rows.append(
                {
                    "wave": wave,
                    "search_id": search_id,
                    "strategy_id": row["strategy_id"],
                    "return_delta": row["return_delta"],
                    "be_delta": row["be_delta"],
                    "mdd_delta": row["mdd_delta"],
                    "oos_return_improved": float(row["return_delta"]) > 0,
                    "oos_return_equal": math.isclose(float(row["return_delta"]), 0.0, abs_tol=1e-12),
                    "oos_return_worsened": float(row["return_delta"]) < 0,
                    "oos_be_improved": float(row["be_delta"]) > 0,
                    "oos_mdd_improved": float(row["mdd_delta"]) > 0,
                    "return_and_be_improved": float(row["return_delta"]) > 0 and float(row["be_delta"]) > 0,
                    "full_range_drift": row.get("stability_flag") == "FULL_RANGE_DRIFT",
                    "single_fold_dominated": dominated,
                    "isolated_validation_optimum": any(item["isolated_validation_optimum"] for item in neighborhood),
                    "baseline_selected_at_least_4_folds": int(row["baseline_selected_folds"]) >= 4,
                }
            )
    atomic_csv(output_root / "phase3b_aggregate_summary.csv", output_rows)
    report = {
        "status": "PASSED" if len(output_rows) == 65 else "FAILED",
        "total_specs": len(output_rows),
        "wave1_specs": sum(row["wave"] == 1 for row in output_rows),
        "wave3_specs": sum(row["wave"] == 3 for row in output_rows),
        "wave5_specs": sum(row["wave"] == 5 for row in output_rows),
        "oos_return_improved": sum(row["oos_return_improved"] for row in output_rows),
        "oos_return_equal": sum(row["oos_return_equal"] for row in output_rows),
        "oos_return_worsened": sum(row["oos_return_worsened"] for row in output_rows),
        "oos_be_improved": sum(row["oos_be_improved"] for row in output_rows),
        "oos_mdd_improved": sum(row["oos_mdd_improved"] for row in output_rows),
        "return_and_be_improved": sum(row["return_and_be_improved"] for row in output_rows),
        "full_range_drift": sum(row["full_range_drift"] for row in output_rows),
        "single_fold_dominated": sum(row["single_fold_dominated"] for row in output_rows),
        "isolated_validation_optimum": sum(row["isolated_validation_optimum"] for row in output_rows),
        "baseline_selected_at_least_4_folds": sum(row["baseline_selected_at_least_4_folds"] for row in output_rows),
        "selection_performed": False,
    }
    atomic_json(output_root / "phase3b_aggregate_summary.json", report)
    return report


def finalize(
    output_root: Path,
    specs: list[dict[str, str]],
    integrity_before: dict[str, Any],
    strategy_root: Path,
) -> dict[str, Any]:
    equivalence = read_csv(output_root / "phase3b_wave5_equivalence_manifest.csv")
    equivalence_by_search = {row["search_id"]: row["equivalence_group_id"] for row in equivalence}
    all_manifest: list[dict[str, Any]] = []
    all_candidates: list[dict[str, Any]] = []
    all_selections: list[dict[str, Any]] = []
    all_tests: list[dict[str, Any]] = []
    oos_rows: list[dict[str, Any]] = []
    stability_rows: list[dict[str, Any]] = []
    boundary_rows: list[dict[str, Any]] = []
    consistency_rows: list[dict[str, Any]] = []
    neighborhood_rows: list[dict[str, Any]] = []
    for spec in specs:
        root = output_root / spec["search_id"]
        manifest = read_csv(root / "logical_manifest.csv")
        candidates = read_csv(root / "candidate_metrics.csv")
        selections = read_csv(root / "fold_selections.csv")
        tests = read_csv(root / "fold_test_metrics.csv")
        group = equivalence_by_search[spec["search_id"]]
        for row in manifest:
            row["logical_run_id"] = row.pop("run_id")
            row["physical_run_id"] = row["cache_key"]
            row["equivalence_group_id"] = group
        for row in candidates:
            row["candidate_count"] = spec["estimated_candidate_count"]
            row["search_dimension"] = len(json.loads(spec["searchable_parameters"]))
        for row in selections:
            row["selection_hash"] = sha_payload(
                {
                    "search_id": row["search_id"],
                    "fold_id": row["fold_id"],
                    "selected_candidate_id": row["selected_candidate_id"],
                    "selected_config_hash": row["selected_config_hash"],
                    "protocol_version": PROTOCOL_VERSION,
                }
            )
            row["selection_frozen"] = row["selection_frozen_before_test"]
        all_manifest.extend(manifest)
        all_candidates.extend(candidates)
        all_selections.extend(selections)
        all_tests.extend(tests)
        selected_frames: list[pd.DataFrame] = []
        baseline_frames: list[pd.DataFrame] = []
        boundaries: list[int] = []
        for selection in selections:
            logical = [
                row for row in manifest if row["fold_id"] == selection["fold_id"] and row["split"] == "TEST"
            ]
            for role, target in (("SELECTED_TEST", selected_frames), ("BASELINE_TEST", baseline_frames)):
                item = next(row for row in logical if row["evaluation_role"] == role)
                metrics = json.loads(Path(item["result_path"]).read_text(encoding="utf-8"))
                target.append(pd.read_parquet(metrics["timeseries_path"]))
            if selected_frames:
                boundaries.append(sum(len(frame) for frame in selected_frames) - 1)
        selected = pd.concat(selected_frames, ignore_index=True)
        baseline = pd.concat(baseline_frames, ignore_index=True)
        selected_metrics = overall(selected, spec["strategy_id"])
        baseline_metrics = overall(baseline, spec["strategy_id"])
        fold_deltas = [float(row["return_delta"]) for row in tests]
        positive = sum(max(0.0, value) for value in fold_deltas)
        dominated = positive > 0 and max(fold_deltas) > 0.5 * positive
        consistency_rows.append(
            {
                "search_id": spec["search_id"],
                "strategy_id": spec["strategy_id"],
                "fold_return_deltas": json.dumps(dict(zip([row["fold_id"] for row in tests], fold_deltas, strict=True))),
                "largest_positive_fold_delta": max(fold_deltas),
                "sum_positive_fold_delta": positive,
                "single_fold_dominated": dominated,
                "consistency_flag": "SINGLE_FOLD_DOMINATED" if dominated else "NOT_SINGLE_FOLD_DOMINATED",
            }
        )
        stability, boundaries_for_spec = parameter_diagnostics(root, spec, selections)
        stability_rows.append(stability)
        boundary_rows.extend(boundaries_for_spec)
        neighborhood_for_spec = neighborhood_diagnostics(spec, selections, candidates)
        neighborhood_rows.extend(neighborhood_for_spec)
        row = {
            "search_id": spec["search_id"],
            "strategy_id": spec["strategy_id"],
            "searched_parameters": spec["searchable_parameters"],
            "candidate_count": spec["estimated_candidate_count"],
            "selected_oos_return": selected_metrics["final_return_1x"],
            "baseline_oos_return": baseline_metrics["final_return_1x"],
            "return_delta": selected_metrics["final_return_1x"] - baseline_metrics["final_return_1x"],
            "selected_oos_turnover": selected_metrics["turnover"],
            "baseline_oos_turnover": baseline_metrics["turnover"],
            "turnover_delta": selected_metrics["turnover"] - baseline_metrics["turnover"],
            "selected_oos_be_bps": selected_metrics["break_even_bps"],
            "baseline_oos_be_bps": baseline_metrics["break_even_bps"],
            "be_delta": selected_metrics["break_even_bps"] - baseline_metrics["break_even_bps"],
            "selected_oos_mdd": selected_metrics["max_drawdown"],
            "baseline_oos_mdd": baseline_metrics["max_drawdown"],
            "mdd_delta": selected_metrics["max_drawdown"] - baseline_metrics["max_drawdown"],
            "selected_trade_count": selected_metrics["trade_count"],
            "baseline_trade_count": baseline_metrics["trade_count"],
            "selected_median_trade_be_bps": selected_metrics["median_trade_be_bps"],
            "baseline_median_trade_be_bps": baseline_metrics["median_trade_be_bps"],
            "baseline_selected_folds": stability["baseline_selected_folds"],
            "unique_selected_configs": stability["unique_selected_configs"],
            "boundary_flags": stability["boundary_flags"],
            "stability_flag": stability["stability_flag"],
            "fold_consistency_flag": consistency_rows[-1]["consistency_flag"],
            "neighborhood_stability_flag": (
                "ISOLATED_VALIDATION_OPTIMUM"
                if any(row["isolated_validation_optimum"] for row in neighborhood_for_spec)
                else "NOT_ISOLATED_VALIDATION_OPTIMUM"
            ),
        }
        oos_rows.append(row)
        make_oos_figure(root, spec, selected, baseline, boundaries[:-1])
    atomic_csv(output_root / "phase3b_wave5_run_manifest.csv", all_manifest)
    atomic_csv(output_root / "phase3b_wave5_candidate_metrics.csv", all_candidates)
    atomic_csv(output_root / "phase3b_wave5_fold_selections.csv", all_selections)
    atomic_csv(output_root / "phase3b_wave5_fold_test_metrics.csv", all_tests)
    atomic_csv(output_root / "phase3b_wave5_oos_summary.csv", oos_rows)
    atomic_csv(output_root / "phase3b_wave5_baseline_comparison.csv", oos_rows)
    atomic_csv(output_root / "phase3b_wave5_parameter_stability.csv", stability_rows)
    atomic_csv(output_root / "phase3b_wave5_boundary_drift.csv", boundary_rows)
    atomic_csv(output_root / "phase3b_wave5_fold_consistency.csv", consistency_rows)
    atomic_csv(output_root / "phase3b_wave5_neighborhood_stability.csv", neighborhood_rows)
    unique_physical = len({row["cache_key"] for row in all_manifest})
    frozen = sum(str(row["selection_frozen"]).lower() == "true" for row in all_selections)
    frozen_before = all(
        row["split"] != "TEST" or row.get("selection_frozen_at_utc", "") <= row["start_timestamp"]
        for row in all_manifest
    )
    integrity_after = integrity_snapshot(strategy_root)
    integrity_ok = integrity_before["files"] == integrity_after["files"]
    summary = {
        "status": "PASSED"
        if len(all_manifest) == LOGICAL_PLAN
        and len(oos_rows) == 7
        and frozen == 49
        and frozen_before
        and integrity_ok
        else "FAILED",
        "protocol_version": PROTOCOL_VERSION,
        "logical_planned": LOGICAL_PLAN,
        "logical_completed": len(all_manifest),
        "train_logical": sum(row["split"] == "TRAIN" for row in all_manifest),
        "validation_logical": sum(row["split"] == "VALIDATION" for row in all_manifest),
        "selected_test_logical": sum(row["evaluation_role"] == "SELECTED_TEST" for row in all_manifest),
        "baseline_test_logical": sum(row["evaluation_role"] == "BASELINE_TEST" for row in all_manifest),
        "physical_backtests": unique_physical,
        "within_spec_test_dedup": len(all_manifest) - unique_physical,
        "cross_spec_equivalence_reuse": 0,
        "wave5_specs_terminal": len(oos_rows),
        "frozen_selections": frozen,
        "selection_frozen_before_all_tests": frozen_before,
        "rejected_candidate_test_runs": sum(
            row["split"] == "TEST" and row["evaluation_role"] not in {"SELECTED_TEST", "BASELINE_TEST"}
            for row in all_manifest
        ),
        "test_informed_reselections": 0,
        "eligible_validation_results": sum(
            row["split"] == "VALIDATION" and str(row.get("eligible", "")).lower() == "true"
            for row in all_candidates
        ),
        "insufficient_trade_validation_results": sum(
            row["split"] == "VALIDATION" and "INSUFFICIENT_VALIDATION_TRADES" in row.get("ineligibility_reasons", "")
            for row in all_candidates
        ),
        "zero_trade_validation_results": sum(
            row["split"] == "VALIDATION" and row["validity_status"] == "VALID_ZERO_TRADES"
            for row in all_candidates
        ),
        "baseline_fallback_folds": sum(row["selection_status"] == "BASELINE_FALLBACK" for row in all_selections),
        "baseline_selected_folds": sum(str(row["baseline_won"]).lower() == "true" for row in all_selections),
        "oos_return_improved": sum(float(row["return_delta"]) > 0 for row in oos_rows),
        "oos_return_equal": sum(math.isclose(float(row["return_delta"]), 0.0, abs_tol=1e-12) for row in oos_rows),
        "oos_return_worse": sum(float(row["return_delta"]) < 0 for row in oos_rows),
        "oos_be_improved": sum(float(row["be_delta"]) > 0 for row in oos_rows),
        "oos_mdd_improved": sum(float(row["mdd_delta"]) > 0 for row in oos_rows),
        "return_and_be_improved": sum(float(row["return_delta"]) > 0 and float(row["be_delta"]) > 0 for row in oos_rows),
        "return_improved_be_worsened": sum(float(row["return_delta"]) > 0 and float(row["be_delta"]) < 0 for row in oos_rows),
        "return_improved_mdd_worsened": sum(float(row["return_delta"]) > 0 and float(row["mdd_delta"]) < 0 for row in oos_rows),
        "baseline_selected_at_least_4_folds": sum(int(row["baseline_selected_folds"]) >= 4 for row in oos_rows),
        "full_range_drift_count": sum(row["stability_flag"] == "FULL_RANGE_DRIFT" for row in stability_rows),
        "single_fold_dominated_count": sum(row["single_fold_dominated"] for row in consistency_rows),
        "isolated_validation_optimum_count": sum(
            any(
                item["search_id"] == row["search_id"] and item["isolated_validation_optimum"]
                for item in neighborhood_rows
            )
            for row in oos_rows
        ),
        "retry_count": 0,
        "failed_count": 0,
        "baseline_integrity_passed": integrity_ok,
    }
    aggregate = aggregate_phase3b(output_root)
    summary["aggregate_summary"] = aggregate
    if aggregate["status"] != "PASSED":
        summary["status"] = "FAILED"
    summary["release_decision"] = "PHASE3C_READY" if summary["status"] == "PASSED" else "PHASE3C_BLOCKED"
    atomic_json(output_root / "phase3b_wave5_validation_summary.json", summary)
    atomic_json(output_root / "baseline_integrity_after.json", integrity_after)
    return summary


def main() -> int:
    parser = wave1.argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market-root", type=Path, default=ROOT / "historical_data/market_data")
    parser.add_argument("--strategy-root", type=Path, default=ROOT / "strategies")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--preflight-only", action="store_true")
    args = parser.parse_args()
    if args.workers < 1:
        raise ValueError("workers must be positive")
    args.output_root.mkdir(parents=True, exist_ok=True)
    integrity_path = args.output_root / "baseline_integrity_before.json"
    if integrity_path.is_file():
        integrity_before = json.loads(integrity_path.read_text(encoding="utf-8"))
    else:
        integrity_before = integrity_snapshot(args.strategy_root)
        atomic_json(integrity_path, integrity_before)
    gate = preflight(args.strategy_root, args.market_root, args.output_root)
    if args.preflight_only:
        print(json.dumps(gate, indent=2))
        return 0
    specs, folds, _ = load_wave5()
    payloads = [
        {
            "spec": spec,
            "folds": folds["folds"],
            "output_root": str(args.output_root),
            "market_root": str(args.market_root),
            "strategy_root": str(args.strategy_root),
            "code_hash": gate["code_hash"],
            "data_provenance": gate["data_provenance"],
        }
        for spec in specs
    ]
    progress = {"status": "RUNNING", "planned_specs": 7, "completed_specs": 0, "failures": []}
    atomic_json(args.output_root / "progress.json", progress)
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(wave1.run_one_spec, payload): payload["spec"]["search_id"] for payload in payloads}
        for future in as_completed(futures):
            search_id = futures[future]
            try:
                result = future.result()
                progress["completed_specs"] += 1
                print(f"TERMINAL {search_id} logical={result['logical_rows']}", flush=True)
            except Exception as exc:
                progress["failures"].append({"search_id": search_id, "error": repr(exc)})
                print(f"FAILED {search_id}: {exc!r}", flush=True)
            atomic_json(args.output_root / "progress.json", progress)
    if progress["failures"]:
        progress["status"] = "FAILED"
        atomic_json(args.output_root / "progress.json", progress)
        return 2
    summary = finalize(args.output_root, specs, integrity_before, args.strategy_root)
    progress["status"] = "COMPLETE" if summary["status"] == "PASSED" else "VALIDATION_FAILED"
    atomic_json(args.output_root / "progress.json", progress)
    print(json.dumps(summary, indent=2))
    return 0 if summary["status"] == "PASSED" else 3


if __name__ == "__main__":
    raise SystemExit(main())
