#!/usr/bin/env python3
"""Build Phase 4A baseline-first cross-strategy research triage without backtests."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import os
import re
import zipfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import matplotlib as mpl

mpl.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import yaml


ROOT = Path(__file__).resolve().parents[2]
AUDIT = ROOT / "outputs/internal_audit/strategy_workbook"
DEFAULT_OUTPUT = ROOT / "outputs/baseline_evaluation/phase4a"
DEFAULT_DELIVERABLE = ROOT / "outputs/deliverables"
TOLERANCE = 1e-10
PERIOD_ORDER = ["2021H2", "2022H1", "2022H2", "2023H1", "2023H2", "2024H1", "2024H2", "2025H1", "2025H2", "2026H1"]
WORKBOOK_DELIVERABLES = [
    "workbook_strategies_baseline",
    "workbook_strategies_phase2_1",
    "workbook_strategies_phase2_2b",
    "workbook_strategies_phase2_2c",
    "workbook_strategies_phase2_3",
]


def numeric(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return math.nan


def truthy(value: Any) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes"}


def atomic_csv(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    frame.to_csv(temporary, index=False, encoding="utf-8-sig")
    os.replace(temporary, path)


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def protected_snapshot(paths: list[Path]) -> dict[str, Any]:
    files: dict[str, str] = {}
    for path in paths:
        candidates = [path] if path.is_file() else sorted(item for item in path.rglob("*") if item.is_file())
        for item in candidates:
            relative = item.relative_to(ROOT).as_posix()
            files[relative] = file_hash(item)
    encoded = json.dumps(files, sort_keys=True, separators=(",", ":")).encode()
    return {"aggregate_sha256": hashlib.sha256(encoded).hexdigest(), "file_count": len(files), "files": files}


def protected_paths(deliverables: Path) -> list[Path]:
    paths = [ROOT / "strategies"]
    for directory in ["existing_registered_strategies_corrected", *WORKBOOK_DELIVERABLES, "phase3b_wave1", "phase3b_wave3", "phase3b_wave5", "phase3c_robustness"]:
        root = deliverables / directory
        if not root.exists():
            continue
        if directory.startswith("workbook_") or directory == "existing_registered_strategies_corrected":
            for name in ("canonical_summary.csv", "validation_summary.json", "episode_metric_summary.csv"):
                if (root / name).exists():
                    paths.append(root / name)
        else:
            paths.append(root)
    for name in (
        "parameter_search_manifest.csv", "phase3a_search_protocol.json", "phase3a_walk_forward_protocol.json",
        "registered_strategy_manifest.csv", "registered_module_manifest.csv", "phase2_3_session_contract_registry.csv",
    ):
        if (AUDIT / name).exists():
            paths.append(AUDIT / name)
    for root in (AUDIT / "semantic_contracts", AUDIT / "module_contracts"):
        if root.exists():
            paths.append(root)
    phase3c_canonical = ROOT / "outputs/parameter_search/phase3c"
    if phase3c_canonical.exists():
        paths.append(phase3c_canonical)
    return paths


def family_from_original(strategy_id: str) -> str:
    return re.sub(r"_(long|short)$", "", strategy_id)


def normalized_config(strategy_id: str, family: str, intrinsic_direction: str) -> dict[str, Any]:
    path = ROOT / "strategies" / strategy_id / "config.yaml"
    params: dict[str, Any] = {}
    if path.is_file():
        source = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        params = dict(source.get("params", {}))
        for key in ("source_registry_id", "semantic_provenance", "contracts_applied", "defaulted_parameters", "instrument_id"):
            params.pop(key, None)
    return {"family": family, "params": params, "intrinsic_direction": intrinsic_direction}


def semantic_groups(universe: pd.DataFrame) -> pd.DataFrame:
    hashes: list[str] = []
    for row in universe.itertuples():
        payload = normalized_config(row.strategy_id, row.strategy_family, row.intrinsic_direction)
        hashes.append(hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest())
    unique = {value: f"baseline_semantic_{index:03d}" for index, value in enumerate(sorted(set(hashes)), 1)}
    result = universe.copy()
    result["equivalence_group_id"] = [unique[value] for value in hashes]
    return result


def load_universe(deliverables: Path) -> tuple[pd.DataFrame, dict[str, pd.DataFrame]]:
    summaries: dict[str, pd.DataFrame] = {}
    original_path = deliverables / "existing_registered_strategies_corrected/canonical_summary.csv"
    original = pd.read_csv(original_path)
    summaries["PRE_WORKBOOK"] = original
    intrinsic_path = deliverables / "existing_registered_strategies_corrected/original_strategy_intrinsic_direction.csv"
    intrinsic = pd.read_csv(intrinsic_path).set_index("strategy_id")["intrinsic_direction"].to_dict()
    registered = pd.read_csv(AUDIT / "registered_strategy_manifest.csv", dtype=str).fillna("")
    registry = registered.set_index("registry_id").to_dict("index")
    workbook_by_strategy: dict[str, pd.DataFrame] = {}
    source_result: dict[str, str] = {}
    for directory in WORKBOOK_DELIVERABLES:
        path = deliverables / directory / "canonical_summary.csv"
        if not path.is_file():
            continue
        frame = pd.read_csv(path)
        for strategy_id, group in frame.groupby("strategy"):
            workbook_by_strategy[strategy_id] = group.copy()
            source_result[strategy_id] = directory
    workbook = pd.concat(workbook_by_strategy.values(), ignore_index=True)
    summaries["WORKBOOK"] = workbook
    rows: list[dict[str, Any]] = []
    for strategy_id in sorted(original.strategy.unique()):
        group = original[original.strategy == strategy_id]
        primary = group[(group.variant == "normal") & (group.premium == "included")].sort_values("lag_minutes").iloc[-1]
        rows.append({
            "strategy_id": strategy_id, "source_group": "PRE_WORKBOOK", "source_identity": strategy_id,
            "strategy_name": strategy_id, "strategy_family": family_from_original(strategy_id),
            "semantic_provenance": "PRE_WORKBOOK_SOURCE", "intrinsic_direction": intrinsic.get(strategy_id, "UNKNOWN"),
            "canonical_timeframe": primary.timeframe, "canonical_baseline_config": str(ROOT / "strategies" / strategy_id / "config.yaml"),
            "canonical_result_source": str(original_path),
        })
    for strategy_id in sorted(workbook_by_strategy):
        metadata = registry.get(strategy_id, {})
        group = workbook_by_strategy[strategy_id]
        primary = group[(group.variant == "original") & (group.premium == "included")].sort_values("lag_minutes").iloc[-1]
        provenance = metadata.get("semantic_provenance") or (group.semantic_provenance.iloc[0] if "semantic_provenance" in group else "SOURCE_EXACT")
        rows.append({
            "strategy_id": strategy_id, "source_group": "WORKBOOK", "source_identity": strategy_id,
            "strategy_name": metadata.get("source_strategy_name", strategy_id),
            "strategy_family": metadata.get("implementation_family", "UNKNOWN"), "semantic_provenance": provenance or "SOURCE_EXACT",
            "intrinsic_direction": "SOURCE_DEFINED_BIDIRECTIONAL", "canonical_timeframe": primary.timeframe,
            "canonical_baseline_config": str(ROOT / "strategies" / strategy_id / "config.yaml"),
            "canonical_result_source": str(deliverables / source_result[strategy_id] / "canonical_summary.csv"),
        })
    universe = semantic_groups(pd.DataFrame(rows))
    if len(universe) != 195 or (universe.source_group == "PRE_WORKBOOK").sum() != 64 or (universe.source_group == "WORKBOOK").sum() != 131:
        raise ValueError(f"strategy universe reconciliation failed: {universe.source_group.value_counts().to_dict()}")
    return universe, summaries


def primary_rows(strategy_id: str, source_group: str, summaries: dict[str, pd.DataFrame]) -> dict[str, pd.Series]:
    group = summaries[source_group]
    group = group[group.strategy == strategy_id]
    normal = "normal" if source_group == "PRE_WORKBOOK" else "original"
    primary = group[group.variant == normal]
    lag0_included = primary[(primary.premium == "included") & (primary.lag_minutes == 0)]
    realistic_included = primary[(primary.premium == "included") & (primary.lag_minutes != 0)].sort_values("lag_minutes")
    realistic_excluded = primary[(primary.premium == "excluded") & (primary.lag_minutes != 0)].sort_values("lag_minutes")
    reverse = group[(group.variant == "strict_reverse") & (group.premium == "included") & (group.lag_minutes != 0)].sort_values("lag_minutes")
    if any(frame.empty for frame in (lag0_included, realistic_included, realistic_excluded, reverse)):
        raise ValueError(f"{strategy_id}: incomplete canonical lag/premium/reverse rows")
    return {"lag0": lag0_included.iloc[0], "realistic": realistic_included.iloc[0], "excluded": realistic_excluded.iloc[0], "reverse": reverse.iloc[0]}


def drawdown(increments: np.ndarray) -> float:
    equity = 1.0 + np.cumsum(increments, dtype=np.float64)
    peak = np.maximum.accumulate(np.r_[1.0, equity])[1:]
    values = np.divide(equity, peak, out=np.zeros_like(equity), where=peak > 0) - 1.0
    return float(values.min(initial=0.0))


def period_label(times: pd.Series) -> pd.Series:
    timestamp = pd.to_datetime(times, unit="ns", utc=True)
    return timestamp.dt.year.astype(str) + np.where(timestamp.dt.month <= 6, "H1", "H2")


def episode_metrics(path: Path, premium: str = "included") -> tuple[dict[str, Any], pd.DataFrame]:
    columns = ["premium_mode", "completion_timestamp", "start_timestamp", "delta_turnover", "delta_gross_return", "break_even_bps"]
    frame = pd.read_csv(path, usecols=columns)
    frame = frame[frame.premium_mode == premium].copy()
    if frame.empty:
        return {
            "completed_episode_count": 0, "episode_be_mean": math.nan, "episode_be_median": math.nan, "episode_be_p10": math.nan,
            "episode_be_p25": math.nan, "episode_be_p75": math.nan, "episode_be_p90": math.nan, "episode_be_positive_fraction": math.nan,
            "episode_return_median_bps": math.nan, "episode_return_positive_fraction": math.nan, "episode_turnover_median": math.nan,
            "episode_turnover_p95": math.nan, "holding_duration_median_minutes": math.nan, "holding_duration_p95_minutes": math.nan,
        }, frame
    be = frame.break_even_bps.to_numpy(float)
    returns = frame.delta_gross_return.to_numpy(float) * 10_000.0
    turnover = frame.delta_turnover.to_numpy(float)
    start = pd.to_datetime(frame.start_timestamp, utc=True)
    end = pd.to_datetime(frame.completion_timestamp, utc=True)
    duration = (end - start).dt.total_seconds().to_numpy(float) / 60.0
    frame["period"] = end.dt.year.astype(str) + np.where(end.dt.month <= 6, "H1", "H2")
    metrics = {
        "completed_episode_count": len(frame), "episode_be_mean": float(np.mean(be)), "episode_be_median": float(np.median(be)),
        "episode_be_p10": float(np.quantile(be, 0.10)), "episode_be_p25": float(np.quantile(be, 0.25)),
        "episode_be_p75": float(np.quantile(be, 0.75)), "episode_be_p90": float(np.quantile(be, 0.90)),
        "episode_be_positive_fraction": float(np.mean(be > 0)), "episode_return_median_bps": float(np.median(returns)),
        "episode_return_positive_fraction": float(np.mean(returns > 0)), "episode_turnover_median": float(np.median(turnover)),
        "episode_turnover_p95": float(np.quantile(turnover, 0.95)), "holding_duration_median_minutes": float(np.median(duration)),
        "holding_duration_p95_minutes": float(np.quantile(duration, 0.95)),
    }
    return metrics, frame


def period_metrics(strategy_id: str, timeseries_path: Path, episode_frame: pd.DataFrame) -> tuple[list[dict[str, Any]], dict[str, Any], str]:
    columns = ["event_time_ns", "normal_total_return", "normal_turnover", "normal_direction"]
    frame = pd.read_parquet(timeseries_path, columns=columns)
    frame["period"] = period_label(frame.event_time_ns)
    execution_hash = hashlib.sha256()
    for name in ("normal_direction", "normal_total_return", "normal_turnover"):
        execution_hash.update(np.ascontiguousarray(frame[name].to_numpy()).tobytes())
    episodes = episode_frame.period.value_counts().to_dict() if not episode_frame.empty else {}
    rows: list[dict[str, Any]] = []
    for period in PERIOD_ORDER:
        child = frame[frame.period == period]
        if child.empty:
            continue
        returns = child.normal_total_return.to_numpy(float)
        turnover = float(child.normal_turnover.sum())
        total = float(returns.sum())
        rows.append({
            "strategy_id": strategy_id, "period": period, "return_1x": total, "turnover": turnover,
            "turnover_display_pct": turnover * 100.0, "signed_be_bps": total * 10_000.0 / turnover if turnover > 0 else math.nan,
            "max_drawdown": drawdown(returns), "trade_count": int(episodes.get(period, 0)), "premium_mode": "included",
        })
    returns = [row["return_1x"] for row in rows]
    turnovers = [row["turnover"] for row in rows]
    total_return = sum(returns)
    positive_total = sum(max(0.0, value) for value in returns)
    largest = max(returns, default=0.0)
    dominated = total_return > TOLERANCE and positive_total > TOLERANCE and largest > 0.5 * positive_total
    leave_out_returns = [total_return - value for value in returns]
    leave_out_be = [
        (total_return - returns[index]) * 10_000.0 / (sum(turnovers) - turnovers[index])
        if sum(turnovers) - turnovers[index] > 0 else math.nan
        for index in range(len(rows))
    ]
    diagnostics = {
        "positive_return_period_count": sum(value > TOLERANCE for value in returns),
        "positive_be_period_count": sum(math.isfinite(row["signed_be_bps"]) and row["signed_be_bps"] > TOLERANCE for row in rows),
        "negative_return_period_count": sum(value < -TOLERANCE for value in returns), "evaluation_period_count": len(rows),
        "positive_return_fraction": sum(value > TOLERANCE for value in returns) / len(rows) if rows else 0.0,
        "positive_be_fraction": sum(math.isfinite(row["signed_be_bps"]) and row["signed_be_bps"] > TOLERANCE for row in rows) / len(rows) if rows else 0.0,
        "return_positive_majority_periods": sum(value > TOLERANCE for value in returns) > len(rows) / 2,
        "be_positive_majority_periods": sum(math.isfinite(row["signed_be_bps"]) and row["signed_be_bps"] > TOLERANCE for row in rows) > len(rows) / 2,
        "baseline_single_period_dominated": dominated,
        "baseline_lopo_return_robust": bool(rows) and all(value > TOLERANCE for value in leave_out_returns),
        "baseline_lopo_be_robust": bool(rows) and all(math.isfinite(value) and value > TOLERANCE for value in leave_out_be),
        "largest_period_return": largest, "largest_period_share_of_positive_return": largest / positive_total if positive_total > 0 else math.nan,
        "period_return_reconciliation": total_return, "period_turnover_reconciliation": sum(turnovers),
    }
    return rows, diagnostics, execution_hash.hexdigest()


def directional_diagnostic(normal_return: float, reverse_return: float) -> str:
    if normal_return > TOLERANCE and reverse_return < -TOLERANCE:
        return "DIRECTIONALLY_CONSISTENT"
    if normal_return < -TOLERANCE and reverse_return < -TOLERANCE:
        return "BOTH_NEGATIVE"
    if normal_return > TOLERANCE and reverse_return > TOLERANCE:
        return "BOTH_POSITIVE"
    if reverse_return > normal_return + TOLERANCE:
        return "REVERSE_OUTPERFORMS_NORMAL"
    return "MIXED_OR_NEUTRAL"


def baseline_tier(row: dict[str, Any], integrity_ok: bool) -> tuple[str, str]:
    if not integrity_ok:
        return "F", "canonical result, episode table, or reconciliation integrity is insufficient"
    return_positive = row["return_realistic_lag"] > TOLERANCE
    be_positive = math.isfinite(row["be_realistic_lag"]) and row["be_realistic_lag"] > TOLERANCE
    if all((return_positive, be_positive, row["return_positive_majority_periods"], row["be_positive_majority_periods"], not row["baseline_single_period_dominated"], row["baseline_lopo_return_robust"], row["completed_episode_count"] > 0)):
        return "A", "positive Return/BE, majority-period persistence, no single-period dominance, and LOPO Return robustness"
    if return_positive and be_positive:
        warnings = []
        if row["baseline_single_period_dominated"]: warnings.append("single-period dominated")
        if not row["return_positive_majority_periods"]: warnings.append("Return not positive in majority periods")
        if not row["be_positive_majority_periods"]: warnings.append("BE not positive in majority periods")
        if not row["baseline_lopo_return_robust"]: warnings.append("LOPO Return not robust")
        if row["sign_flips_under_lag"]: warnings.append("lag sign flip")
        if row["episode_be_positive_fraction"] < 0.5: warnings.append("minority positive-BE episodes")
        return "B", "positive baseline with robustness warning: " + "; ".join(warnings or ["does not meet all Tier A conditions"])
    if return_positive != be_positive or row["positive_at_lag0_only"]:
        return "C", "partial positive quality or lag0-only positivity"
    if abs(row["return_realistic_lag"]) <= TOLERANCE or not math.isfinite(row["be_realistic_lag"]):
        return "D", "near-neutral/zero-trade or mixed weak evidence"
    return "E", "realistic-lag Return and signed BE are non-positive"


def load_phase3c(deliverables: Path) -> dict[str, dict[str, Any]]:
    path = ROOT / "outputs/parameter_search/phase3c/phase3c_master_robustness_table.csv"
    if not path.is_file():
        path = deliverables / "phase3c_robustness/phase3c_master_robustness_table.csv"
    if not path.is_file():
        return {}
    return pd.read_csv(path).set_index("strategy_id").to_dict("index")


def coverage_row(strategy_id: str, rows: dict[str, pd.Series], source_group: str) -> dict[str, Any]:
    timeseries = Path(rows["realistic"].source_timeseries)
    episode = Path(rows["realistic"].per_trade_BE_table)
    return {
        "strategy_id": strategy_id, "source_group": source_group, "canonical_baseline_result": True,
        "lag0_result": True, "realistic_lag_result": True, "premium_included": True, "premium_excluded": True,
        "normal_or_original_result": True, "strict_reverse_result": True, "executed_position": timeseries.is_file(),
        "episode_table": episode.is_file(), "per_episode_be": episode.is_file(), "episode_return": episode.is_file(),
        "episode_turnover": episode.is_file(), "holding_duration": episode.is_file(),
        "coverage_status": "COMPLETE" if timeseries.is_file() and episode.is_file() else "MISSING_CANONICAL_ARTIFACT",
        "timeseries_path": str(timeseries), "episode_path": str(episode),
    }


def build_outputs(deliverables: Path) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    universe, summaries = load_universe(deliverables)
    phase3c = load_phase3c(deliverables)
    master_rows: list[dict[str, Any]] = []
    period_rows: list[dict[str, Any]] = []
    episode_rows: list[dict[str, Any]] = []
    coverage_rows: list[dict[str, Any]] = []
    evidence_hashes: dict[str, str] = {}
    for number, item in enumerate(universe.itertuples(), 1):
        rows = primary_rows(item.strategy_id, item.source_group, summaries)
        coverage = coverage_row(item.strategy_id, rows, item.source_group)
        coverage_rows.append(coverage)
        episode_stats, episodes = episode_metrics(Path(rows["realistic"].per_trade_BE_table)) if coverage["episode_table"] else ({}, pd.DataFrame())
        periods, persistence, evidence_hash = period_metrics(item.strategy_id, Path(rows["realistic"].source_timeseries), episodes) if coverage["executed_position"] else ([], {}, "")
        period_rows.extend({**row, "source_group": item.source_group, "canonical_lag": rows["realistic"].lag} for row in periods)
        evidence_hashes[item.strategy_id] = evidence_hash
        lag0_return = numeric(rows["lag0"].final_return_1x); realistic_return = numeric(rows["realistic"].final_return_1x)
        lag0_be = numeric(rows["lag0"].global_BE_bps); realistic_be = numeric(rows["realistic"].global_BE_bps)
        phase = phase3c.get(item.strategy_id, {})
        result = {
            **item._asdict(), "timeframe": rows["realistic"].timeframe, "canonical_realistic_lag": rows["realistic"].lag,
            "return_lag0": lag0_return, "return_realistic_lag": realistic_return, "return_lag_delta": realistic_return - lag0_return,
            "be_lag0": lag0_be, "be_realistic_lag": realistic_be, "be_lag_delta": realistic_be - lag0_be,
            "mdd_lag0": numeric(rows["lag0"].max_drawdown), "mdd_realistic_lag": numeric(rows["realistic"].max_drawdown),
            "turnover_lag0": numeric(rows["lag0"].turnover), "turnover_realistic_lag": numeric(rows["realistic"].turnover),
            "turnover_realistic_lag_display_pct": numeric(rows["realistic"].turnover) * 100.0,
            "premium_return_delta": realistic_return - numeric(rows["excluded"].final_return_1x),
            "premium_be_delta": realistic_be - numeric(rows["excluded"].global_BE_bps),
            "premium_mdd_delta": numeric(rows["realistic"].max_drawdown) - numeric(rows["excluded"].max_drawdown),
            "strict_reverse_return": numeric(rows["reverse"].final_return_1x), "strict_reverse_be": numeric(rows["reverse"].global_BE_bps),
            "strict_reverse_mdd": numeric(rows["reverse"].max_drawdown),
            "directional_diagnostic": directional_diagnostic(realistic_return, numeric(rows["reverse"].final_return_1x)),
            "abs_return_positive": realistic_return > TOLERANCE, "abs_be_positive": math.isfinite(realistic_be) and realistic_be > TOLERANCE,
            "abs_return_and_be_positive": realistic_return > TOLERANCE and math.isfinite(realistic_be) and realistic_be > TOLERANCE,
            "positive_at_lag0_only": lag0_return > TOLERANCE and realistic_return <= TOLERANCE,
            "positive_at_realistic_lag": realistic_return > TOLERANCE, "be_positive_at_realistic_lag": math.isfinite(realistic_be) and realistic_be > TOLERANCE,
            "sign_flips_under_lag": (lag0_return > TOLERANCE) != (realistic_return > TOLERANCE),
            "be_sign_flips_under_lag": (lag0_be > TOLERANCE) != (realistic_be > TOLERANCE),
            "positive_cost_capacity": math.isfinite(realistic_be) and realistic_be > TOLERANCE,
            "negative_cost_capacity": math.isfinite(realistic_be) and realistic_be < -TOLERANCE,
            "return_positive_be_negative": realistic_return > TOLERANCE and realistic_be < -TOLERANCE,
            **episode_stats, **persistence,
            "phase3c_tier": phase.get("tier", "NOT_SEARCHED"), "phase3c_search_return_delta": phase.get("return_delta", math.nan),
            "phase3c_search_be_delta": phase.get("be_delta", math.nan),
            "phase3c_warning_flags": ";".join(name for key, name in (("full_range_drift", "FULL_RANGE_DRIFT"), ("single_fold_dominated", "SINGLE_FOLD_DOMINATED"), ("isolated_validation_optimum", "ISOLATED_VALIDATION_OPTIMUM")) if truthy(phase.get(key, False))) or "NONE",
            "baseline_only_selection": True, "source_timeseries": rows["realistic"].source_timeseries,
            "source_episode_table": rows["realistic"].per_trade_BE_table,
        }
        be_residual = realistic_return - numeric(rows["realistic"].turnover) * realistic_be / 10_000.0 if numeric(rows["realistic"].turnover) > 0 and math.isfinite(realistic_be) else (realistic_return if numeric(rows["realistic"].turnover) == 0 else math.nan)
        return_residual = numeric(persistence.get("period_return_reconciliation")) - realistic_return
        turnover_residual = numeric(persistence.get("period_turnover_reconciliation")) - numeric(rows["realistic"].turnover)
        result.update({"be_formula_residual": be_residual, "period_return_residual": return_residual, "period_turnover_residual": turnover_residual})
        turnover_tolerance = max(TOLERANCE, abs(numeric(rows["realistic"].turnover)) * 2e-12)
        integrity_ok = coverage["coverage_status"] == "COMPLETE" and (not math.isfinite(be_residual) or abs(be_residual) <= TOLERANCE) and abs(return_residual) <= TOLERANCE and abs(turnover_residual) <= turnover_tolerance
        tier, reason = baseline_tier(result, integrity_ok)
        result.update({"baseline_tier": tier, "baseline_tier_reasons": reason, "result_integrity_passed": integrity_ok})
        master_rows.append(result)
        episode_rows.append({"strategy_id": item.strategy_id, "source_group": item.source_group, **episode_stats})
        if number % 10 == 0:
            print(f"PROCESSED {number}/{len(universe)}", flush=True)
    master = pd.DataFrame(master_rows)
    # Evidence hashes make execution-equivalent identities explicit while preserving source IDs.
    unique_evidence = {value: f"evidence_group_{index:03d}" for index, value in enumerate(sorted(set(evidence_hashes.values())), 1)}
    master["executable_evidence_group_id"] = master.strategy_id.map(lambda value: unique_evidence[evidence_hashes[value]])
    return universe, pd.DataFrame(coverage_rows), master, pd.DataFrame(period_rows), pd.DataFrame(episode_rows)


def refresh_integrity_and_tiers(master: pd.DataFrame, coverage: pd.DataFrame) -> pd.DataFrame:
    """Reapply transparent validation/tier rules to complete cached derived rows."""
    coverage_status = coverage.set_index("strategy_id").coverage_status.to_dict()
    refreshed = master.copy()
    for index, row in refreshed.iterrows():
        turnover_tolerance = max(TOLERANCE, abs(numeric(row.turnover_realistic_lag)) * 2e-12)
        integrity_ok = (
            coverage_status.get(row.strategy_id) == "COMPLETE"
            and (not math.isfinite(numeric(row.be_formula_residual)) or abs(numeric(row.be_formula_residual)) <= TOLERANCE)
            and abs(numeric(row.period_return_residual)) <= TOLERANCE
            and abs(numeric(row.period_turnover_residual)) <= turnover_tolerance
        )
        tier, reason = baseline_tier(row.to_dict(), integrity_ok)
        refreshed.loc[index, "result_integrity_passed"] = integrity_ok
        refreshed.loc[index, "baseline_tier"] = tier
        refreshed.loc[index, "baseline_tier_reasons"] = reason
    return refreshed


def representative_rows(master: pd.DataFrame) -> pd.DataFrame:
    order = {tier: index for index, tier in enumerate("ABCDEF")}
    sorted_master = master.assign(_tier=master.baseline_tier.map(order)).sort_values(
        ["executable_evidence_group_id", "_tier", "be_realistic_lag", "strategy_id"], ascending=[True, True, False, True]
    )
    return sorted_master.groupby("executable_evidence_group_id", as_index=False).first().drop(columns="_tier")


def grouped_summary(representatives: pd.DataFrame, raw: pd.DataFrame, dimension: str) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for value, frame in representatives.groupby(dimension):
        raw_frame = raw[raw[dimension] == value]
        rows.append({
            dimension: value, "raw_strategy_identities": len(raw_frame), "unique_semantic_groups": len(frame),
            "positive_return_groups": int(frame.abs_return_positive.sum()), "positive_be_groups": int(frame.abs_be_positive.sum()),
            "tier_a_groups": int((frame.baseline_tier == "A").sum()), "tier_b_groups": int((frame.baseline_tier == "B").sum()),
            "median_return": float(frame.return_realistic_lag.median()), "median_be": float(frame.be_realistic_lag.median()),
            "median_mdd": float(frame.mdd_realistic_lag.median()), "median_turnover": float(frame.turnover_realistic_lag.median()),
        })
    return pd.DataFrame(rows).sort_values("unique_semantic_groups", ascending=False)


def semantic_group_summary(master: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for group_id, frame in master.groupby("executable_evidence_group_id"):
        representative = representative_rows(frame).iloc[0]
        rows.append({
            "executable_evidence_group_id": group_id, "representative_strategy_id": representative.strategy_id,
            "member_source_ids": ";".join(sorted(frame.strategy_id)), "member_count": len(frame),
            "baseline_tier": representative.baseline_tier, "return_realistic_lag": representative.return_realistic_lag,
            "be_realistic_lag": representative.be_realistic_lag, "mdd_realistic_lag": representative.mdd_realistic_lag,
            "turnover_realistic_lag": representative.turnover_realistic_lag, "positive_return_period_count": representative.positive_return_period_count,
            "positive_be_period_count": representative.positive_be_period_count, "evaluation_period_count": representative.evaluation_period_count,
            "warnings": representative.baseline_tier_reasons,
        })
    return pd.DataFrame(rows)


def boss_shortlist(representatives: pd.DataFrame) -> pd.DataFrame:
    shortlist = representatives[representatives.baseline_tier.isin(["A", "B", "C"])].copy()
    rank = {tier: index for index, tier in enumerate("ABC")}
    shortlist["_rank"] = shortlist.baseline_tier.map(rank)
    shortlist = shortlist.sort_values(
        ["_rank", "abs_return_positive", "abs_be_positive", "positive_return_period_count", "baseline_lopo_return_robust", "be_realistic_lag", "mdd_realistic_lag", "turnover_realistic_lag", "strategy_id"],
        ascending=[True, False, False, False, False, False, False, True, True],
    ).drop(columns="_rank")
    return shortlist


def make_figures(master: pd.DataFrame, representatives: pd.DataFrame, periods: pd.DataFrame, output: Path) -> None:
    figures = output / "figures"; figures.mkdir(parents=True, exist_ok=True)
    colors = representatives.baseline_tier.map({"A": "#198754", "B": "#5ca76d", "C": "#d4a72c", "D": "#6c757d", "E": "#dc3545", "F": "#6f42c1"})
    def scatter(x: str, y: str, title: str, xlabel: str, ylabel: str, name: str, diagonal: bool = False, xscale_pct: bool = False) -> None:
        fig, axis = plt.subplots(figsize=(9, 7)); xv = representatives[x] * (100 if xscale_pct else 1); yv = representatives[y]
        axis.scatter(xv, yv, c=colors, alpha=0.75)
        axis.axhline(0, color="0.5", linewidth=0.8); axis.axvline(0, color="0.5", linewidth=0.8)
        if diagonal:
            finite = np.r_[xv.replace([np.inf, -np.inf], np.nan).dropna(), yv.replace([np.inf, -np.inf], np.nan).dropna()]
            if len(finite): low, high = finite.min(), finite.max(); axis.plot([low, high], [low, high], "k--", linewidth=1)
        axis.set(title=title, xlabel=xlabel, ylabel=ylabel); axis.grid(alpha=0.2); fig.tight_layout(); fig.savefig(figures / name, dpi=160); plt.close(fig)
    scatter("be_realistic_lag", "return_realistic_lag", "Phase 4A — Baseline Return vs Cost Capacity", "Signed Global BE (bps)", "Cumulative Return (1x arithmetic)", "01_return_vs_be.png")
    scatter("turnover_realistic_lag", "return_realistic_lag", "Phase 4A — Baseline Return vs Turnover", "Cumulative Turnover (% capital)", "Cumulative Return (1x arithmetic)", "02_return_vs_turnover.png", xscale_pct=True)
    scatter("mdd_realistic_lag", "return_realistic_lag", "Phase 4A — Baseline Return vs MDD", "Max Drawdown", "Cumulative Return (1x arithmetic)", "03_return_vs_mdd.png")
    scatter("return_lag0", "return_realistic_lag", "Phase 4A — Lag0 vs Realistic-Lag Return", "Lag0 Return", "Realistic-lag Return", "04_lag0_vs_realistic_return.png", diagonal=True)
    finite_be = representatives.be_realistic_lag.replace([np.inf, -np.inf], np.nan).dropna()
    fig, axis = plt.subplots(figsize=(9, 6)); counts, bins, _ = axis.hist(finite_be, bins=30, alpha=0.65); centers = (bins[:-1] + bins[1:]) / 2; axis.plot(centers, counts, marker="o", linewidth=1); axis.axvline(0, color="black", linestyle="--"); axis.set(title="Phase 4A — Global Signed BE Distribution", xlabel="BE (bps)", ylabel="Semantic groups"); axis.grid(alpha=0.2); fig.tight_layout(); fig.savefig(figures / "05_global_be_distribution.png", dpi=160); plt.close(fig)
    fig, axis = plt.subplots(figsize=(8, 5)); representatives.baseline_tier.value_counts().reindex(list("ABCDEF"), fill_value=0).plot.bar(ax=axis, color=["#198754", "#5ca76d", "#d4a72c", "#6c757d", "#dc3545", "#6f42c1"]); axis.set(title="Phase 4A — Baseline Tier Counts (semantic groups)", xlabel="Tier", ylabel="Groups"); axis.grid(axis="y", alpha=0.2); fig.tight_layout(); fig.savefig(figures / "06_baseline_tier_counts.png", dpi=160); plt.close(fig)
    reps = representatives.sort_values(["baseline_tier", "strategy_id"])
    pivot = periods[periods.strategy_id.isin(reps.strategy_id)].pivot(index="strategy_id", columns="period", values="return_1x").reindex(index=reps.strategy_id, columns=PERIOD_ORDER)
    values = np.sign(pivot.to_numpy(float)); fig, axis = plt.subplots(figsize=(13, max(10, len(pivot) * 0.12))); image = axis.imshow(values, aspect="auto", cmap="RdYlGn", vmin=-1, vmax=1); axis.set_xticks(range(len(PERIOD_ORDER)), PERIOD_ORDER, rotation=45, ha="right"); axis.set_yticks(range(len(pivot)), pivot.index, fontsize=5); axis.set_title("Phase 4A — Period Return Sign by Semantic-Group Representative"); fig.colorbar(image, ax=axis, label="Return sign"); fig.tight_layout(); fig.savefig(figures / "07_period_persistence_heatmap.png", dpi=160); plt.close(fig)


def build_html(master: pd.DataFrame, reps: pd.DataFrame, shortlist: pd.DataFrame, source_summary: pd.DataFrame, output: Path) -> None:
    tier_counts = reps.baseline_tier.value_counts()
    cards = {
        "Strategy identities": len(master), "Semantic groups": len(reps), "Return > 0": int(reps.abs_return_positive.sum()),
        "BE > 0": int(reps.abs_be_positive.sum()), "Both > 0": int(reps.abs_return_and_be_positive.sum()),
        **{f"Tier {tier}": int(tier_counts.get(tier, 0)) for tier in "ABCDEF"},
    }
    card_html = "".join(f"<div class='card'><b>{html.escape(str(key))}</b><span>{value}</span></div>" for key, value in cards.items())
    columns = ["strategy_id", "strategy_family", "source_group", "baseline_tier", "return_realistic_lag", "be_realistic_lag", "mdd_realistic_lag", "turnover_realistic_lag_display_pct", "positive_return_period_count", "evaluation_period_count", "episode_be_median", "episode_be_positive_fraction", "research_warnings", "baseline_tier_reasons"]
    def table(frame: pd.DataFrame) -> str:
        return frame[columns].to_html(index=False, border=0, classes="sortable", float_format=lambda x: f"{x:.6g}")
    negative = reps[reps.baseline_tier.isin(["E", "F"])]
    lag_sensitive = reps[reps.positive_at_lag0_only]
    weak_cost = reps[reps.abs_return_positive & ~reps.abs_be_positive]
    search_poor = reps[(reps.phase3c_tier != "NOT_SEARCHED") & ~reps.abs_return_positive]
    document = f"""<!doctype html><html><head><meta charset='utf-8'><title>Phase 4A Boss Strategy Review</title><style>body{{font:14px system-ui;margin:28px;color:#202124}}.cards{{display:flex;gap:10px;flex-wrap:wrap}}.card{{padding:10px 16px;border:1px solid #ddd;border-radius:8px;display:flex;gap:12px}}.card span{{font-size:20px}}table{{border-collapse:collapse;width:100%;font-size:11px}}th,td{{padding:5px;border-bottom:1px solid #ddd;text-align:right}}th:first-child,td:first-child{{text-align:left}}th{{background:#f4f6f8;cursor:pointer}}h2{{margin-top:30px}}.note{{padding:12px;background:#fff8dc;border-left:4px solid #d4a72c}}</style></head><body><h1>Phase 4A — Baseline Strategy Research Review</h1><p class='note'>Not “Best Strategies”. Primary evidence is canonical Premium-Included realistic-lag baseline. Phase 3 searched 65 specs, produced Tier A = 0, and is annotation only.</p><div class='cards'>{card_html}</div><h2>1. Highest-priority baseline research candidates</h2>{table(shortlist)}<h2>2. Positive but lag-sensitive</h2>{table(lag_sensitive)}<h2>3. Positive Return but weak cost capacity</h2>{table(weak_cost)}<h2>4. Phase 3 search evidence with poor baseline</h2>{table(search_poor)}<h2>5. Negative / integrity-low-priority</h2>{table(negative)}<h2>6. Source-group comparison</h2>{source_summary.to_html(index=False, border=0)}<h2>7. Phase 3 conclusion</h2><p>65 parameter-search specs; Phase 3C Tier A = 0; instability was widespread. No selected Phase 3 configuration replaces a canonical baseline.</p><script>document.querySelectorAll('th').forEach(h=>h.onclick=()=>{{let t=h.closest('table'),b=t.tBodies[0],c=[...h.parentNode.children].indexOf(h),r=[...b.rows];r.sort((a,z)=>{{let x=a.cells[c].innerText,y=z.cells[c].innerText,nx=parseFloat(x),ny=parseFloat(y);return Number.isNaN(nx)||Number.isNaN(ny)?x.localeCompare(y):nx-ny}});r.forEach(x=>b.appendChild(x))}});</script></body></html>"""
    temporary = output / "phase4a_boss_strategy_review.html.tmp"; temporary.write_text(document, encoding="utf-8"); os.replace(temporary, output / "phase4a_boss_strategy_review.html")


def package(output: Path, deliverables: Path) -> tuple[Path, str]:
    target = deliverables / "phase4a_baseline_evaluation.zip"; temporary = target.with_suffix(".zip.tmp")
    with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(output.rglob("*")):
            if path.is_file() and not path.name.endswith(".tmp"):
                archive.write(path, Path("phase4a_baseline_evaluation") / path.relative_to(output))
    os.replace(temporary, target)
    with zipfile.ZipFile(target) as archive:
        if archive.testzip(): raise RuntimeError("Phase 4A ZIP integrity failed")
    return target, file_hash(target)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deliverable-root", type=Path, default=DEFAULT_DELIVERABLE)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(); args.output_root.mkdir(parents=True, exist_ok=True)
    before = protected_snapshot(protected_paths(args.deliverable_root)); atomic_json(args.output_root / "phase4a_protected_hashes_before.json", before)
    cached = {
        name: args.output_root / name
        for name in (
            "phase4a_strategy_universe.csv", "phase4a_result_coverage.csv", "phase4a_strategy_master.csv",
            "phase4a_period_robustness.csv", "phase4a_episode_quality.csv",
        )
    }
    if all(path.is_file() for path in cached.values()):
        universe = pd.read_csv(cached["phase4a_strategy_universe.csv"])
        coverage = pd.read_csv(cached["phase4a_result_coverage.csv"])
        master = pd.read_csv(cached["phase4a_strategy_master.csv"])
        periods = pd.read_csv(cached["phase4a_period_robustness.csv"])
        episodes = pd.read_csv(cached["phase4a_episode_quality.csv"])
        if len(universe) != 195 or len(master) != 195 or len(periods) != 1950:
            raise ValueError("cached Phase 4A derived rows are incomplete")
        master = refresh_integrity_and_tiers(master, coverage)
    else:
        universe, coverage, master, periods, episodes = build_outputs(args.deliverable_root)
    phase3c = load_phase3c(args.deliverable_root)
    for index, row in master.iterrows():
        phase = phase3c.get(row.strategy_id, {})
        master.loc[index, "phase3c_tier"] = phase.get("tier", "NOT_SEARCHED")
        master.loc[index, "phase3c_search_return_delta"] = phase.get("return_delta", math.nan)
        master.loc[index, "phase3c_search_be_delta"] = phase.get("be_delta", math.nan)
        master.loc[index, "phase3c_warning_flags"] = ";".join(
            name
            for key, name in (
                ("full_range_drift", "FULL_RANGE_DRIFT"),
                ("single_fold_dominated", "SINGLE_FOLD_DOMINATED"),
                ("isolated_validation_optimum", "ISOLATED_VALIDATION_OPTIMUM"),
            )
            if truthy(phase.get(key, False))
        ) or "NONE"
    research_warnings: list[str] = []
    for row in master.itertuples():
        warnings: list[str] = []
        if numeric(row.episode_be_median) <= TOLERANCE:
            warnings.append("MEDIAN_EPISODE_BE_NONPOSITIVE")
        if numeric(row.episode_be_positive_fraction) <= 0.5:
            warnings.append("MINORITY_POSITIVE_BE_EPISODES")
        if truthy(row.positive_at_lag0_only):
            warnings.append("POSITIVE_AT_LAG0_ONLY")
        if truthy(row.baseline_single_period_dominated):
            warnings.append("BASELINE_SINGLE_PERIOD_DOMINATED")
        if row.phase3c_tier == "F":
            warnings.append("PHASE3C_TIER_F")
        if row.phase3c_warning_flags != "NONE":
            warnings.append("PHASE3C:" + row.phase3c_warning_flags)
        research_warnings.append(";".join(warnings) or "NONE")
    master["research_warnings"] = research_warnings
    if "executable_evidence_group_id" not in universe:
        universe = universe.merge(
            master[["strategy_id", "executable_evidence_group_id", "phase3c_tier"]],
            on="strategy_id", how="left", validate="one_to_one",
        )
        universe["has_phase3_search"] = universe.phase3c_tier != "NOT_SEARCHED"
    else:
        universe["phase3c_tier"] = universe.strategy_id.map(master.set_index("strategy_id").phase3c_tier)
        universe["has_phase3_search"] = universe.phase3c_tier != "NOT_SEARCHED"
    reps = representative_rows(master); semantic = semantic_group_summary(master); shortlist = boss_shortlist(reps)
    source_summary = grouped_summary(reps, master, "source_group")
    provenance_summary = grouped_summary(reps, master, "semantic_provenance")
    family_summary = grouped_summary(reps, master, "strategy_family")
    directional = master[["strategy_id", "source_group", "return_realistic_lag", "strict_reverse_return", "be_realistic_lag", "strict_reverse_be", "mdd_realistic_lag", "strict_reverse_mdd", "directional_diagnostic"]].copy()
    lag = master[["strategy_id", "source_group", "return_lag0", "return_realistic_lag", "return_lag_delta", "be_lag0", "be_realistic_lag", "be_lag_delta", "mdd_lag0", "mdd_realistic_lag", "turnover_lag0", "turnover_realistic_lag", "positive_at_lag0_only", "positive_at_realistic_lag", "be_positive_at_realistic_lag", "sign_flips_under_lag", "be_sign_flips_under_lag"]].copy()
    boss_columns = ["strategy_id", "strategy_name", "strategy_family", "source_group", "executable_evidence_group_id", "baseline_tier", "return_realistic_lag", "be_realistic_lag", "mdd_realistic_lag", "turnover_realistic_lag_display_pct", "positive_return_period_count", "positive_be_period_count", "evaluation_period_count", "episode_be_median", "episode_be_positive_fraction", "holding_duration_median_minutes", "directional_diagnostic", "phase3c_tier", "phase3c_warning_flags", "research_warnings", "baseline_tier_reasons"]
    boss = shortlist[boss_columns].copy()
    followup = boss[boss.baseline_tier.isin(["A", "B"])].copy()
    followup["why_follow_up"] = followup.baseline_tier_reasons
    followup["what_is_strong"] = "positive canonical realistic-lag Return and cost capacity"
    followup["what_is_weak"] = followup.research_warnings
    followup["next_research_question"] = np.where(followup.phase3c_warning_flags.str.contains("DRIFT|DOMINATED|OPTIMUM"), "Can the baseline evidence persist across another symbol/market without parameter reselection?", "Does an explicit execution-cost stress test preserve positive signed cost capacity?")
    low = master[(master.baseline_tier.isin(["E", "F"])) | master.positive_at_lag0_only | master.baseline_single_period_dominated].copy()
    low["low_priority_reason"] = low.baseline_tier_reasons
    atomic_csv(args.output_root / "phase4a_strategy_universe.csv", universe)
    atomic_csv(args.output_root / "phase4a_result_coverage.csv", coverage)
    atomic_csv(args.output_root / "phase4a_strategy_master.csv", master)
    atomic_csv(args.output_root / "phase4a_period_robustness.csv", periods)
    atomic_csv(args.output_root / "phase4a_episode_quality.csv", episodes)
    atomic_csv(args.output_root / "phase4a_semantic_group_summary.csv", semantic)
    atomic_csv(args.output_root / "phase4a_family_summary.csv", family_summary)
    atomic_csv(args.output_root / "phase4a_source_group_summary.csv", source_summary)
    atomic_csv(args.output_root / "phase4a_semantic_provenance_summary.csv", provenance_summary)
    atomic_csv(args.output_root / "phase4a_lag_robustness.csv", lag)
    atomic_csv(args.output_root / "phase4a_directional_diagnostics.csv", directional)
    atomic_csv(args.output_root / "phase4a_boss_shortlist.csv", boss)
    atomic_csv(args.output_root / "phase4a_followup_candidates.csv", followup)
    atomic_csv(args.output_root / "phase4a_low_priority.csv", low)
    make_figures(master, reps, periods, args.output_root); build_html(master, reps, shortlist, source_summary, args.output_root)
    after = protected_snapshot(protected_paths(args.deliverable_root)); atomic_json(args.output_root / "phase4a_protected_hashes_after.json", after)
    protected_changes = sum(before["files"].get(key) != after["files"].get(key) for key in set(before["files"]) | set(after["files"]))
    tier_counts = reps.baseline_tier.value_counts()
    summary = {
        "status": "PASSED", "tolerance": TOLERANCE, "pre_workbook_identities": int((master.source_group == "PRE_WORKBOOK").sum()),
        "workbook_identities": int((master.source_group == "WORKBOOK").sum()), "total_identities": len(master),
        "unique_executable_semantic_groups": len(reps), "raw_return_positive": int(master.abs_return_positive.sum()),
        "group_return_positive": int(reps.abs_return_positive.sum()), "raw_be_positive": int(master.abs_be_positive.sum()),
        "group_be_positive": int(reps.abs_be_positive.sum()), "raw_both_positive": int(master.abs_return_and_be_positive.sum()),
        "group_both_positive": int(reps.abs_return_and_be_positive.sum()), "baseline_tier_counts_groups": {tier: int(tier_counts.get(tier, 0)) for tier in "ABCDEF"},
        "return_positive_majority_groups": int(reps.return_positive_majority_periods.sum()), "be_positive_majority_groups": int(reps.be_positive_majority_periods.sum()),
        "lopo_return_robust_groups": int(reps.baseline_lopo_return_robust.sum()), "single_period_dominated_groups": int(reps.baseline_single_period_dominated.sum()),
        "positive_lag0_groups": int((reps.return_lag0 > TOLERANCE).sum()), "positive_realistic_lag_groups": int(reps.positive_at_realistic_lag.sum()),
        "positive_be_lag0_groups": int((reps.be_lag0 > TOLERANCE).sum()), "positive_be_realistic_lag_groups": int(reps.be_positive_at_realistic_lag.sum()),
        "lag_return_sign_flip_groups": int(reps.sign_flips_under_lag.sum()), "lag_be_sign_flip_groups": int(reps.be_sign_flips_under_lag.sum()),
        "global_be_positive_groups": int(reps.abs_be_positive.sum()), "median_episode_be_positive_groups": int((reps.episode_be_median > TOLERANCE).sum()),
        "majority_positive_be_episode_groups": int((reps.episode_be_positive_fraction > 0.5).sum()),
        "directional_diagnostic_counts": {str(key): int(value) for key, value in reps.directional_diagnostic.value_counts().items()},
        "maximum_be_formula_residual": float(master.be_formula_residual.abs().dropna().max()),
        "maximum_period_return_residual": float(master.period_return_residual.abs().max()),
        "maximum_period_turnover_residual": float(master.period_turnover_residual.abs().max()),
        "complete_result_coverage": int((coverage.coverage_status == "COMPLETE").sum()),
        "phase3_search_specs": 65, "phase3c_tier_a": 0, "new_parameter_search_runs": 0, "new_five_year_backtests": 0,
        "production_configs_generated": 0, "protected_hash_changes": protected_changes,
    }
    if any((len(master) != 195, len(periods) != 1950, not master.result_integrity_passed.all(), protected_changes != 0)):
        summary["status"] = "FAILED"
    atomic_json(args.output_root / "phase4a_validation_summary.json", summary)
    zip_path, sha = package(args.output_root, args.deliverable_root)
    atomic_json(args.output_root / "phase4a_delivery.json", {"zip_path": str(zip_path), "sha256": sha, "zip_integrity": "PASSED"})
    print(json.dumps({**summary, "zip_path": str(zip_path), "zip_sha256": sha}, indent=2), flush=True)
    return 0 if summary["status"] == "PASSED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
