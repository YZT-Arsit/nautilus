#!/usr/bin/env python3
"""Build the read-only Phase 3C robustness triage from frozen Phase 3B results."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import os
import shutil
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
DEFAULT_OUTPUT = ROOT / "outputs/parameter_search/phase3c"
TOLERANCE = 1e-12
FOLD_LABELS = ["2023H1", "2023H2", "2024H1", "2024H2", "2025H1", "2025H2", "2026H1"]


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def atomic_csv(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    frame.to_csv(temporary, index=False, encoding="utf-8-sig")
    os.replace(temporary, path)


def numeric(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return math.nan


def truthy(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes"}


def compare(left: float, right: float, tolerance: float = TOLERANCE) -> str:
    if not math.isfinite(left) or not math.isfinite(right):
        return "NOT_COMPARABLE"
    delta = left - right
    if delta > tolerance:
        return "BETTER"
    if delta < -tolerance:
        return "WORSE"
    return "EQUAL"


def lofo(values: list[float], tolerance: float = TOLERANCE) -> dict[str, Any]:
    finite = [value for value in values if math.isfinite(value)]
    if len(finite) != len(values) or not values:
        return {"positive": 0, "nonpositive": 0, "minimum": math.nan, "median": math.nan, "robust": False}
    totals = [sum(values[:index] + values[index + 1 :]) for index in range(len(values))]
    return {
        "positive": sum(value > tolerance for value in totals),
        "nonpositive": sum(value <= tolerance for value in totals),
        "minimum": min(totals),
        "median": float(np.median(totals)),
        "robust": all(value > tolerance for value in totals),
    }


def dominant_fold(values: list[float], fold_ids: list[str], tolerance: float = TOLERANCE) -> dict[str, Any]:
    total = sum(values)
    positive_total = sum(max(0.0, value) for value in values)
    index = max(range(len(values)), key=values.__getitem__)
    largest = values[index]
    share = largest / positive_total if positive_total > tolerance and largest > 0 else math.nan
    return {
        "largest_fold_return_delta": largest,
        "total_oos_return_delta_from_folds": total,
        "largest_fold_share_of_positive_delta": share,
        "dominant_fold": fold_ids[index],
        "single_fold_dominated": positive_total > tolerance and largest > 0.5 * positive_total,
        "improvement_disappears_without_best_fold": total > tolerance and total - largest <= tolerance,
    }


def tree_hash(paths: list[Path]) -> dict[str, Any]:
    files: dict[str, str] = {}
    for source in paths:
        candidates = [source] if source.is_file() else sorted(path for path in source.rglob("*") if path.is_file())
        for path in candidates:
            relative = path.relative_to(ROOT).as_posix()
            files[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    payload = json.dumps(files, sort_keys=True, separators=(",", ":"))
    return {"aggregate_sha256": hashlib.sha256(payload.encode()).hexdigest(), "file_count": len(files), "files": files}


def protected_paths(wave_roots: dict[int, Path]) -> list[Path]:
    values = list(wave_roots.values())
    for relative in (
        "phase3a_search_protocol.json",
        "phase3a_walk_forward_protocol.json",
        "parameter_search_manifest.csv",
        "phase3b_wave3_parameter_search_manifest.csv",
        "phase3b_wave5_parameter_search_manifest.csv",
        "strategy_workbook_conversion_manifest.csv",
        "registered_module_manifest.csv",
    ):
        path = AUDIT / relative
        if path.exists():
            values.append(path)
    for path in (ROOT / "strategies", AUDIT / "semantic_contracts"):
        if path.exists():
            values.append(path)
    return values


def load_frame(root: Path, wave: int, suffix: str) -> pd.DataFrame:
    path = root / f"phase3b_wave{wave}_{suffix}"
    if not path.is_file():
        raise FileNotFoundError(path)
    return pd.read_csv(path)


def load_specs() -> dict[str, dict[str, str]]:
    frame = pd.read_csv(AUDIT / "parameter_search_manifest.csv", dtype=str).fillna("")
    return {row["search_id"]: row for row in frame.to_dict("records") if row["status"] == "READY"}


def load_families() -> dict[str, str]:
    path = AUDIT / "registered_strategy_manifest.csv"
    if not path.is_file():
        return {}
    frame = pd.read_csv(path, dtype=str).fillna("")
    return dict(zip(frame.registry_id, frame.implementation_family, strict=False))


def wave_data(wave: int, root: Path) -> dict[str, pd.DataFrame]:
    return {
        "oos": load_frame(root, wave, "oos_summary.csv"),
        "tests": load_frame(root, wave, "fold_test_metrics.csv"),
        "selections": load_frame(root, wave, "fold_selections.csv"),
        "candidates": load_frame(root, wave, "candidate_metrics.csv"),
        "stability": load_frame(root, wave, "parameter_stability.csv"),
    }


def neighborhood_flags(spec: dict[str, str], selections: pd.DataFrame, candidates: pd.DataFrame) -> dict[str, Any]:
    names = sorted(json.loads(spec["searchable_parameters"]))
    space = json.loads(spec["candidate_space"])
    def grid_index(name: str, value: Any) -> int:
        values = space[name]
        if isinstance(value, (int, float)):
            return min(range(len(values)), key=lambda index: abs(float(values[index]) - float(value)))
        return values.index(value)
    isolated_folds = 0
    total_folds = 0
    for selection in selections.to_dict("records"):
        rows = candidates[(candidates.fold_id == selection["fold_id"]) & (candidates.split == "VALIDATION")]
        selected = rows[rows.candidate_id == selection["selected_candidate_id"]]
        if selected.empty:
            continue
        selected_row = selected.iloc[0]
        selected_params = json.loads(selected_row.parameters)
        neighbor_returns: list[float] = []
        for candidate in rows.to_dict("records"):
            if not truthy(candidate.get("eligible")):
                continue
            params = json.loads(candidate["parameters"])
            differences = [name for name in names if params[name] != selected_params[name]]
            if len(differences) != 1:
                continue
            name = differences[0]
            if abs(grid_index(name, params[name]) - grid_index(name, selected_params[name])) == 1:
                neighbor_returns.append(numeric(candidate["return_1x"]))
        isolated = len(neighbor_returns) >= 2 and numeric(selected_row.return_1x) > max(neighbor_returns) + TOLERANCE
        isolated_folds += int(isolated)
        total_folds += 1
    return {
        "isolated_validation_optimum": isolated_folds > 0,
        "isolated_optimum_fold_count": isolated_folds,
        "isolated_optimum_fold_fraction": isolated_folds / total_folds if total_folds else 0.0,
    }


def boundary_flags(spec: dict[str, str], selections: pd.DataFrame) -> dict[str, Any]:
    names = json.loads(spec["searchable_parameters"])
    space = json.loads(spec["candidate_space"])
    paths = [json.loads(value) for value in selections.selected_parameters]
    low = high = both = False
    drifts: list[float] = []
    for name in names:
        values = [row[name] for row in paths]
        authorized = space[name]
        low_hit = min(authorized) in values
        high_hit = max(authorized) in values
        low |= low_hit
        high |= high_hit
        both |= low_hit and high_hit
        if all(isinstance(value, (int, float)) for value in values) and max(authorized) != min(authorized):
            drifts.append((float(max(values)) - float(min(values))) / (float(max(authorized)) - float(min(authorized))))
    vectors = [json.dumps(value, sort_keys=True) for value in paths]
    transitions = sum(left != right for left, right in zip(vectors, vectors[1:], strict=False))
    return {
        "low_boundary_selection": low,
        "high_boundary_selection": high,
        "both_boundaries_selected": both,
        "full_range_drift": both,
        "normalized_parameter_drift": max(drifts, default=0.0),
        "joint_config_transition_rate": transitions / max(1, len(vectors) - 1),
        "unique_selected_config_count": len(set(vectors)),
    }


def train_validation_diagnostics(selections: pd.DataFrame, candidates: pd.DataFrame) -> dict[str, Any]:
    train_returns: list[float] = []
    validation_returns: list[float] = []
    train_be: list[float] = []
    validation_be: list[float] = []
    below = 0
    for selection in selections.to_dict("records"):
        rows = candidates[(candidates.fold_id == selection["fold_id"]) & (candidates.candidate_id == selection["selected_candidate_id"])]
        train = rows[rows.split == "TRAIN"]
        validation = rows[rows.split == "VALIDATION"]
        if train.empty or validation.empty:
            continue
        tr = numeric(train.iloc[0].return_1x)
        vr = numeric(validation.iloc[0].return_1x)
        train_returns.append(tr)
        validation_returns.append(vr)
        train_be.append(numeric(train.iloc[0].signed_global_be_bps))
        validation_be.append(numeric(validation.iloc[0].signed_global_be_bps))
        below += int(vr < tr - TOLERANCE)
    finite = lambda values: [value for value in values if math.isfinite(value)]
    trf, vrf, tbf, vbf = map(finite, (train_returns, validation_returns, train_be, validation_be))
    deltas = [validation - train for train, validation in zip(train_returns, validation_returns, strict=False)]
    return {
        "median_train_return": float(np.median(trf)) if trf else math.nan,
        "median_validation_return": float(np.median(vrf)) if vrf else math.nan,
        "median_train_to_validation_delta": float(np.median(deltas)) if deltas else math.nan,
        "median_train_be": float(np.median(tbf)) if tbf else math.nan,
        "median_validation_be": float(np.median(vbf)) if vbf else math.nan,
        "validation_return_below_train_fold_count": below,
        "systematic_train_validation_degradation": below >= 4,
    }


def stability_severity(flags: dict[str, Any]) -> str:
    if flags["full_range_drift"] and (flags["isolated_validation_optimum"] or flags["joint_config_transition_rate"] >= 0.8):
        return "HIGHLY_UNSTABLE"
    if flags["full_range_drift"] or flags["isolated_validation_optimum"]:
        return "UNSTABLE"
    if flags["unique_selected_config_count"] >= 4 or flags["joint_config_transition_rate"] >= 0.5:
        return "MODERATE_DRIFT"
    return "STABLE"


def classify_tier(row: dict[str, Any]) -> tuple[str, str, str, str]:
    positive: list[str] = []
    negative: list[str] = []
    if row["absolute_return_positive"]:
        positive.append("selected OOS Return > 0")
    else:
        negative.append("selected OOS Return <= 0")
    if row["absolute_be_positive"]:
        positive.append("selected signed OOS BE > 0")
    else:
        negative.append("selected signed OOS BE <= 0 or unavailable")
    if row["return_beats_baseline"]:
        positive.append("Return beats baseline")
    if row["be_beats_baseline"]:
        positive.append("BE beats baseline")
    for flag, label in (
        (row["full_range_drift"], "FULL_RANGE_DRIFT"),
        (row["single_fold_dominated"], "SINGLE_FOLD_DOMINATED"),
        (row["isolated_validation_optimum"], "ISOLATED_VALIDATION_OPTIMUM"),
    ):
        if flag:
            negative.append(label)
    severe = sum((row["full_range_drift"], row["single_fold_dominated"], row["isolated_validation_optimum"])) >= 2
    tier_a = all(
        (
            row["absolute_return_positive"], row["absolute_be_positive"], row["return_beats_baseline"],
            row["return_improves_majority_folds"], not row["full_range_drift"],
            not row["single_fold_dominated"], not row["isolated_validation_optimum"], row["lofo_robust_return"],
        )
    )
    if severe:
        tier = "F"
        reasons = "severe compound instability takes precedence"
    elif tier_a:
        tier = "A"
        reasons = "positive absolute Return/BE, persistent relative gain, and no major instability"
    elif (
        row["absolute_return_positive"]
        and row["absolute_be_positive"]
        and (row["return_beats_baseline"] or row["return_improves_majority_folds"] or row["lofo_robust_return"])
    ) or (
        row["return_beats_baseline"] and row["be_beats_baseline"] and (row["absolute_return_positive"] or row["absolute_be_positive"])
    ):
        tier = "B"
        reasons = "positive/promising evidence remains, but at least one robustness condition for Tier A is absent"
    elif row["return_beats_baseline"] and (not row["absolute_return_positive"] or not row["absolute_be_positive"]):
        tier = "C"
        reasons = "relative Return improvement with non-positive absolute Return or BE"
    elif row["return_equals_baseline"]:
        tier = "D"
        reasons = "selected OOS Return is baseline-equivalent"
    else:
        tier = "E"
        reasons = "selected OOS Return is worse than baseline"
    blockers = "; ".join(negative) or "none"
    return tier, reasons, "; ".join(positive) or "none", blockers


def semantic_groups(specs: dict[str, dict[str, str]], families: dict[str, str]) -> dict[str, str]:
    payloads: dict[str, str] = {}
    for search_id, spec in specs.items():
        strategy_id = spec["strategy_id"]
        config = ROOT / "strategies" / strategy_id / "config.yaml"
        params: dict[str, Any] = {}
        if config.is_file():
            source = yaml.safe_load(config.read_text(encoding="utf-8")) or {}
            params = dict(source.get("params", {}))
            for name in ("source_registry_id", "semantic_provenance", "contracts_applied", "defaulted_parameters"):
                params.pop(name, None)
        payload = {
            "family": families.get(strategy_id, params.get("family", "")),
            "runtime": params,
            "candidate_space": json.loads(spec["candidate_space"]),
            "fixed_parameters": json.loads(spec["fixed_parameters"]),
            "timeframe": spec["target_timeframe"],
            "lag": "lag1m",
            "premium": "INCLUDED",
            "direction": "ORIGINAL",
        }
        payloads[search_id] = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    unique = {value: f"semantic_group_{index:03d}" for index, value in enumerate(sorted(set(payloads.values())), 1)}
    return {search_id: unique[value] for search_id, value in payloads.items()}


def build_master(wave_roots: dict[int, Path]) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    specs = load_specs()
    families = load_families()
    groups = semantic_groups(specs, families)
    rows: list[dict[str, Any]] = []
    fold_rows: list[dict[str, Any]] = []
    wave_counts: Counter[int] = Counter()
    for wave, root in wave_roots.items():
        data = wave_data(wave, root)
        for summary in data["oos"].to_dict("records"):
            search_id = summary["search_id"]
            spec = specs[search_id]
            tests = data["tests"][data["tests"].search_id == search_id].sort_values("fold_id")
            selections = data["selections"][data["selections"].search_id == search_id].sort_values("fold_id")
            candidates = data["candidates"][data["candidates"].search_id == search_id]
            if len(tests) != 7 or len(selections) != 7:
                raise ValueError(f"{search_id}: expected seven TEST folds and selections")
            fold_ids = tests.fold_id.astype(str).tolist()
            return_deltas = [numeric(value) for value in tests.return_delta]
            be_deltas = [numeric(value) for value in tests.be_delta]
            dominance = dominant_fold(return_deltas, fold_ids)
            return_lofo = lofo(return_deltas)
            be_lofo = lofo(be_deltas)
            boundary = boundary_flags(spec, selections)
            stored_stability = data["stability"][data["stability"].search_id == search_id]
            if len(stored_stability) != 1:
                raise ValueError(f"{search_id}: expected one stored parameter-stability row")
            stored_stability = stored_stability.iloc[0]
            boundary["full_range_drift"] = str(stored_stability.stability_flag) == "FULL_RANGE_DRIFT"
            boundary["normalized_parameter_drift"] = numeric(stored_stability.normalized_parameter_drift)
            boundary["unique_selected_config_count"] = int(float(stored_stability.unique_selected_configs))
            if boundary["full_range_drift"]:
                boundary["both_boundaries_selected"] = True
            neighborhood = neighborhood_flags(spec, selections, candidates)
            train_validation = train_validation_diagnostics(selections, candidates)
            flags = {**boundary, **neighborhood}
            selected_return = numeric(summary.get("selected_oos_return", summary.get("selected_oos_return_1x")))
            baseline_return = numeric(summary.get("baseline_oos_return", summary.get("baseline_oos_return_1x")))
            selected_be = numeric(summary.get("selected_oos_be_bps"))
            baseline_be = numeric(summary.get("baseline_oos_be_bps"))
            selected_mdd = numeric(summary.get("selected_oos_mdd"))
            baseline_mdd = numeric(summary.get("baseline_oos_mdd"))
            selected_turnover = numeric(summary.get("selected_oos_turnover"))
            baseline_turnover = numeric(summary.get("baseline_oos_turnover"))
            return_cmp = compare(selected_return, baseline_return)
            be_cmp = compare(selected_be, baseline_be)
            mdd_cmp = compare(selected_mdd, baseline_mdd)
            turnover_cmp = compare(-selected_turnover, -baseline_turnover)
            trade_counts = [int(float(value)) for value in tests.selected_trade_count]
            result: dict[str, Any] = {
                "wave": wave,
                "search_id": search_id,
                "strategy_id": summary["strategy_id"],
                "strategy_family": families.get(summary["strategy_id"], spec.get("search_group_id", "UNKNOWN")),
                "semantic_group_id": groups[search_id],
                "searched_parameters": spec["searchable_parameters"],
                "parameter_types": ";".join(json.loads(spec["searchable_parameters"])),
                "candidate_count": int(spec["estimated_candidate_count"]),
                "number_of_search_parameters": len(json.loads(spec["searchable_parameters"])),
                "search_group_dimension": len(json.loads(spec["searchable_parameters"])),
                "search_breadth": "LOW" if int(spec["estimated_candidate_count"]) <= 5 else ("MEDIUM" if int(spec["estimated_candidate_count"]) <= 15 else "HIGH"),
                "selected_oos_return": selected_return,
                "baseline_oos_return": baseline_return,
                "return_delta": selected_return - baseline_return,
                "selected_oos_be": selected_be,
                "baseline_oos_be": baseline_be,
                "be_delta": selected_be - baseline_be,
                "selected_oos_mdd": selected_mdd,
                "baseline_oos_mdd": baseline_mdd,
                "mdd_delta": selected_mdd - baseline_mdd,
                "selected_oos_turnover": selected_turnover,
                "baseline_oos_turnover": baseline_turnover,
                "turnover_delta": selected_turnover - baseline_turnover,
                "turnover_ratio": selected_turnover / baseline_turnover if baseline_turnover > TOLERANCE else math.nan,
                "selected_trade_count": int(float(summary["selected_trade_count"])),
                "trade_count_by_fold": json.dumps(dict(zip(fold_ids, trade_counts, strict=True))),
                "max_fold_trade_fraction": max(trade_counts) / sum(trade_counts) if sum(trade_counts) else math.nan,
                "return_improved_fold_count": sum(value > TOLERANCE for value in return_deltas),
                "return_worse_fold_count": sum(value < -TOLERANCE for value in return_deltas),
                "be_improved_fold_count": sum(value > TOLERANCE for value in be_deltas if math.isfinite(value)),
                "be_worse_fold_count": sum(value < -TOLERANCE for value in be_deltas if math.isfinite(value)),
                "mdd_improved_fold_count": sum(numeric(row.selected_max_drawdown) > numeric(row.baseline_max_drawdown) + TOLERANCE for row in tests.itertuples()),
                "positive_absolute_return_fold_count": sum(numeric(value) > TOLERANCE for value in tests.selected_return_1x),
                "positive_absolute_be_fold_count": sum(numeric(value) > TOLERANCE for value in tests.selected_signed_global_be_bps),
                "return_improvement_fraction": sum(value > TOLERANCE for value in return_deltas) / 7,
                "be_improvement_fraction": sum(value > TOLERANCE for value in be_deltas if math.isfinite(value)) / 7,
                "return_improves_majority_folds": sum(value > TOLERANCE for value in return_deltas) >= 4,
                "be_improves_majority_folds": sum(value > TOLERANCE for value in be_deltas if math.isfinite(value)) >= 4,
                **dominance,
                "lofo_positive_return_delta_count": return_lofo["positive"],
                "lofo_nonpositive_return_delta_count": return_lofo["nonpositive"],
                "minimum_lofo_return_delta": return_lofo["minimum"],
                "median_lofo_return_delta": return_lofo["median"],
                "lofo_robust_return": return_lofo["robust"],
                "lofo_positive_be_delta_count": be_lofo["positive"],
                "lofo_robust_be": be_lofo["robust"],
                **flags,
                "baseline_selected_folds": sum(truthy(value) for value in selections.baseline_won),
                "absolute_return_positive": selected_return > TOLERANCE,
                "absolute_be_positive": math.isfinite(selected_be) and selected_be > TOLERANCE,
                "absolute_return_nonpositive": selected_return <= TOLERANCE,
                "absolute_be_nonpositive": not math.isfinite(selected_be) or selected_be <= TOLERANCE,
                "return_beats_baseline": return_cmp == "BETTER",
                "return_equals_baseline": return_cmp == "EQUAL",
                "return_worse_than_baseline": return_cmp == "WORSE",
                "be_beats_baseline": be_cmp == "BETTER",
                "be_equals_baseline": be_cmp == "EQUAL",
                "be_worse_than_baseline": be_cmp == "WORSE",
                "mdd_beats_baseline": mdd_cmp == "BETTER",
                "mdd_equals_baseline": mdd_cmp == "EQUAL",
                "mdd_worse_than_baseline": mdd_cmp == "WORSE",
                "turnover_lower_than_baseline": turnover_cmp == "BETTER",
                "turnover_higher_than_baseline": turnover_cmp == "WORSE",
                "return_up_mdd_worse": return_cmp == "BETTER" and mdd_cmp == "WORSE",
                "cost_capacity_quadrant": f"RETURN_{'UP' if return_cmp == 'BETTER' else 'DOWN' if return_cmp == 'WORSE' else 'EQUAL'}_BE_{'UP' if be_cmp == 'BETTER' else 'DOWN' if be_cmp == 'WORSE' else 'EQUAL'}",
                **train_validation,
            }
            result["stability_severity"] = stability_severity(result)
            tier, tier_reasons, positive, blockers = classify_tier(result)
            result.update({"tier": tier, "tier_reasons": tier_reasons, "positive_evidence": positive, "tier_blockers": blockers})
            rows.append(result)
            wave_counts[wave] += 1
            for label, test in zip(FOLD_LABELS, tests.itertuples(), strict=True):
                fold_rows.append({
                    "wave": wave, "search_id": search_id, "strategy_id": summary["strategy_id"],
                    "fold_id": test.fold_id, "period": label,
                    "selected_return": numeric(test.selected_return_1x), "baseline_return": numeric(test.baseline_return_1x),
                    "return_delta": numeric(test.return_delta), "selected_be": numeric(test.selected_signed_global_be_bps),
                    "baseline_be": numeric(test.baseline_signed_global_be_bps), "be_delta": numeric(test.be_delta),
                    "selected_mdd": numeric(test.selected_max_drawdown), "baseline_mdd": numeric(test.baseline_max_drawdown),
                    "mdd_delta": numeric(test.mdd_delta), "selected_trade_count": int(float(test.selected_trade_count)),
                    "baseline_trade_count": int(float(test.baseline_trade_count)),
                })
    master = pd.DataFrame(rows).sort_values(["tier", "search_id"]).reset_index(drop=True)
    if wave_counts != Counter({1: 23, 3: 35, 5: 7}) or len(master) != 65 or master.search_id.nunique() != 65:
        raise ValueError(f"65-spec reconciliation failed: {wave_counts}, rows={len(master)}")
    rank = master.apply(lambda row: (
        not bool(row.absolute_return_positive), not bool(row.absolute_be_positive),
        not bool(row.return_improves_majority_folds), not bool(row.be_improves_majority_folds),
        not bool(row.lofo_robust_return), {"STABLE": 0, "MODERATE_DRIFT": 1, "UNSTABLE": 2, "HIGHLY_UNSTABLE": 3}[row.stability_severity],
        -numeric(row.selected_oos_mdd), numeric(row.selected_oos_turnover), row.strategy_id,
    ), axis=1)
    master["followup_priority"] = 0
    for tier in ("A", "B"):
        indexes = master[master.tier == tier].index.tolist()
        indexes.sort(key=lambda index: rank[index])
        for priority, index in enumerate(indexes, 1):
            master.loc[index, "followup_priority"] = priority
    metadata = {"wave_counts": dict(wave_counts), "raw_spec_count": 65, "unique_semantic_group_count": master.semantic_group_id.nunique()}
    return master, pd.DataFrame(fold_rows), metadata


def aggregate_report(master: pd.DataFrame, group: str) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    expanded = master.assign(_group=master[group]).copy()
    if group == "parameter_types":
        expanded = expanded.assign(_group=expanded._group.str.split(";")).explode("_group")
    for value, frame in expanded.groupby("_group", dropna=False):
        rows.append({
            group[:-1] if group.endswith("s") else group: value,
            "spec_count": len(frame),
            "unique_semantic_group_count": frame.semantic_group_id.nunique(),
            "return_improved_count": int(frame.return_beats_baseline.sum()),
            "return_improved_fraction": float(frame.return_beats_baseline.mean()),
            "be_improved_count": int(frame.be_beats_baseline.sum()),
            "be_improved_fraction": float(frame.be_beats_baseline.mean()),
            "return_be_both_improved_count": int((frame.return_beats_baseline & frame.be_beats_baseline).sum()),
            "absolute_positive_return_count": int(frame.absolute_return_positive.sum()),
            "absolute_positive_be_count": int(frame.absolute_be_positive.sum()),
            "full_range_drift_count": int(frame.full_range_drift.sum()),
            "single_fold_dominated_count": int(frame.single_fold_dominated.sum()),
            "isolated_optimum_count": int(frame.isolated_validation_optimum.sum()),
            "tier_a_count": int((frame.tier == "A").sum()),
            "tier_b_count": int((frame.tier == "B").sum()),
        })
    return pd.DataFrame(rows).sort_values(["spec_count", rows[0].keys().__iter__().__next__()], ascending=[False, True])


def tier_summary(master: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for dimension, values in (("ALL", [("ALL", master)]), ("WAVE", master.groupby("wave")), ("FAMILY", master.groupby("strategy_family")), ("PARAMETER_TYPE", master.assign(parameter_type=master.parameter_types.str.split(";")).explode("parameter_type").groupby("parameter_type"))):
        for value, frame in values:
            counts = frame.tier.value_counts()
            rows.append({"breakdown": dimension, "value": value, "spec_count": len(frame), **{f"tier_{tier}_count": int(counts.get(tier, 0)) for tier in "ABCDEF"}})
    return pd.DataFrame(rows)


def make_figures(master: pd.DataFrame, parameter: pd.DataFrame, output: Path) -> None:
    figures = output / "figures"
    figures.mkdir(parents=True, exist_ok=True)
    colors = master.tier.map(dict(A="#198754", B="#4c9f70", C="#d4a72c", D="#6c757d", E="#dc3545", F="#6f42c1"))
    def scatter(x: str, y: str, title: str, xlabel: str, ylabel: str, name: str, diagonal: bool = False) -> None:
        fig, axis = plt.subplots(figsize=(9, 7))
        axis.scatter(master[x], master[y], c=colors, alpha=0.8)
        if diagonal:
            finite = np.r_[master[x].replace([np.inf, -np.inf], np.nan).dropna(), master[y].replace([np.inf, -np.inf], np.nan).dropna()]
            if len(finite):
                low, high = float(np.min(finite)), float(np.max(finite)); axis.plot([low, high], [low, high], "k--", linewidth=1)
        axis.axhline(0, color="0.6", linewidth=0.8); axis.axvline(0, color="0.6", linewidth=0.8)
        axis.set(title=title, xlabel=xlabel, ylabel=ylabel); axis.grid(alpha=0.2)
        fig.tight_layout(); fig.savefig(figures / name, dpi=160); plt.close(fig)
    scatter("baseline_oos_return", "selected_oos_return", "Phase 3C — Selected vs Baseline OOS Return", "Baseline OOS Return (1x)", "Selected OOS Return (1x)", "01_selected_vs_baseline_return.png", True)
    scatter("baseline_oos_be", "selected_oos_be", "Phase 3C — Selected vs Baseline Signed BE", "Baseline BE (bps)", "Selected BE (bps)", "02_selected_vs_baseline_be.png", True)
    scatter("return_delta", "be_delta", "Phase 3C — Return Delta vs BE Delta", "OOS Return delta", "Signed BE delta (bps)", "03_return_delta_vs_be_delta.png")
    scatter("normalized_parameter_drift", "return_delta", "Phase 3C — Parameter Drift vs OOS Return Delta", "Normalized parameter drift", "OOS Return delta", "04_drift_vs_return_delta.png")
    fig, axis = plt.subplots(figsize=(8, 5)); counts = master.tier.value_counts().reindex(list("ABCDEF"), fill_value=0); counts.plot.bar(ax=axis, color=["#198754", "#4c9f70", "#d4a72c", "#6c757d", "#dc3545", "#6f42c1"]); axis.set(title="Phase 3C — Robustness Tier Counts", xlabel="Tier", ylabel="Search specs"); axis.grid(axis="y", alpha=0.2); fig.tight_layout(); fig.savefig(figures / "05_tier_counts.png", dpi=160); plt.close(fig)
    top = parameter.head(12).sort_values("return_improved_fraction")
    fig, axis = plt.subplots(figsize=(11, 7)); axis.barh(top.parameter_type, top.return_improved_fraction, label="Return improved"); axis.barh(top.parameter_type, top.be_improved_fraction, alpha=0.65, label="BE improved"); axis.set(xlabel="Fraction of specs", title="Phase 3C — Parameter-Type Robustness"); axis.legend(); axis.grid(axis="x", alpha=0.2); fig.tight_layout(); fig.savefig(figures / "06_parameter_type_robustness.png", dpi=160); plt.close(fig)


def build_html(master: pd.DataFrame, tier: pd.DataFrame, summary: dict[str, Any], output: Path) -> None:
    def table(frame: pd.DataFrame, columns: list[str]) -> str:
        return frame[columns].to_html(index=False, classes="sortable", border=0, float_format=lambda value: f"{value:.6g}")
    cards = "".join(f"<div class='card'><b>Tier {name}</b><span>{summary['tier_counts'].get(name, 0)}</span></div>" for name in "ABCDEF")
    common = ["strategy_id", "tier", "selected_oos_return", "selected_oos_be", "return_delta", "be_delta", "stability_severity", "tier_blockers"]
    sections = [
        ("Highest-priority research candidates", master[master.tier.isin(["A", "B"])].sort_values(["tier", "followup_priority"])),
        ("Relative improvement but negative absolute result", master[(master.return_beats_baseline) & (~master.absolute_return_positive)]),
        ("Boundary-drifting searches", master[master.full_range_drift]),
        ("Single-fold dominated searches", master[master.single_fold_dominated]),
        ("Baseline-equivalent searches", master[master.tier == "D"]),
        ("OOS deteriorated searches", master[master.tier == "E"]),
    ]
    body = "".join(f"<h2>{html.escape(title)}</h2>{table(frame, common)}" for title, frame in sections)
    document = f"""<!doctype html><html><head><meta charset='utf-8'><title>Phase 3C Robustness Review</title><style>body{{font:14px system-ui;margin:28px;color:#202124}}h1,h2{{margin-top:28px}}.cards{{display:flex;gap:12px;flex-wrap:wrap}}.card{{padding:12px 20px;border:1px solid #ddd;border-radius:8px;display:flex;gap:16px}}.card span{{font-size:22px}}table{{border-collapse:collapse;width:100%;font-size:12px}}th,td{{padding:6px;border-bottom:1px solid #ddd;text-align:right}}th:first-child,td:first-child{{text-align:left}}th{{cursor:pointer;background:#f4f6f8;position:sticky;top:0}}.note{{background:#fff8dc;padding:12px;border-left:4px solid #d4a72c}}</style></head><body><h1>Phase 3C — Robustness Review (not “best strategies”)</h1><p class='note'>Read-only triage of 65 frozen walk-forward searches. No optimization, reselection, or production parameters.</p><div class='cards'>{cards}</div><p>Unique executable semantic groups: {summary['unique_semantic_group_count']} / raw specs: 65.</p>{body}<script>document.querySelectorAll('th').forEach((h,i)=>h.onclick=()=>{{let t=h.closest('table'),b=t.tBodies[0],r=[...b.rows],c=[...h.parentNode.children].indexOf(h);r.sort((a,z)=>{{let x=a.cells[c].innerText,y=z.cells[c].innerText,nx=parseFloat(x),ny=parseFloat(y);return Number.isNaN(nx)||Number.isNaN(ny)?x.localeCompare(y):nx-ny}});r.forEach(x=>b.appendChild(x))}});</script></body></html>"""
    temporary = (output / "phase3c_robustness_review.html.tmp")
    temporary.write_text(document, encoding="utf-8")
    os.replace(temporary, output / "phase3c_robustness_review.html")


def package(output: Path, deliverable: Path) -> tuple[Path, str]:
    deliverable.mkdir(parents=True, exist_ok=True)
    target = deliverable / "phase3c_robustness.zip"
    temporary = target.with_suffix(".zip.tmp")
    with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(output.rglob("*")):
            if path.is_file() and not path.name.endswith(".tmp"):
                archive.write(path, Path("phase3c_robustness") / path.relative_to(output))
    os.replace(temporary, target)
    with zipfile.ZipFile(target) as archive:
        bad = archive.testzip()
        if bad:
            raise RuntimeError(f"corrupt ZIP member: {bad}")
    return target, hashlib.sha256(target.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wave1-root", type=Path, default=ROOT / "outputs/parameter_search/phase3b_wave1")
    parser.add_argument("--wave3-root", type=Path, default=ROOT / "outputs/parameter_search/phase3b_wave3")
    parser.add_argument("--wave5-root", type=Path, default=ROOT / "outputs/parameter_search/phase3b_wave5")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--deliverable-root", type=Path, default=ROOT / "outputs/deliverables")
    args = parser.parse_args()
    wave_roots = {1: args.wave1_root.resolve(), 3: args.wave3_root.resolve(), 5: args.wave5_root.resolve()}
    args.output_root.mkdir(parents=True, exist_ok=True)
    protected = protected_paths(wave_roots)
    before = tree_hash(protected)
    atomic_json(args.output_root / "phase3c_protected_hashes_before.json", before)
    master, folds, metadata = build_master(wave_roots)
    parameter = aggregate_report(master, "parameter_types").rename(columns={"parameter_type": "parameter_type"})
    family = aggregate_report(master, "strategy_family")
    tiers = tier_summary(master)
    followup = master[master.tier.isin(["A", "B"])].copy().sort_values(["tier", "followup_priority"])
    followup["why_included"] = followup.tier_reasons
    low = master[(master.tier.isin(["E", "F"])) | ((master.tier == "C") & (~master.absolute_return_positive))].copy()
    low["low_priority_reason"] = low.tier_reasons + "; " + low.tier_blockers
    atomic_csv(args.output_root / "phase3c_master_robustness_table.csv", master)
    atomic_csv(args.output_root / "phase3c_fold_consistency.csv", folds)
    atomic_csv(args.output_root / "phase3c_tier_summary.csv", tiers)
    atomic_csv(args.output_root / "phase3c_parameter_type_robustness.csv", parameter)
    atomic_csv(args.output_root / "phase3c_family_robustness.csv", family)
    atomic_csv(args.output_root / "phase3c_followup_candidates.csv", followup)
    atomic_csv(args.output_root / "phase3c_low_priority_searches.csv", low)
    make_figures(master, parameter, args.output_root)
    summary = {
        **metadata,
        "status": "PASSED",
        "tolerance": TOLERANCE,
        "tier_precedence": ["F severe compound instability", "A exact robust-positive conditions", "B promising", "C relative-only", "D baseline-equivalent", "E deterioration"],
        "stability_rules": {"HIGHLY_UNSTABLE": "FULL_RANGE_DRIFT and (isolated optimum or transition rate >= 0.8)", "UNSTABLE": "FULL_RANGE_DRIFT or isolated optimum", "MODERATE_DRIFT": "unique configs >= 4 or transition rate >= 0.5", "STABLE": "otherwise"},
        "search_breadth_bands": {"LOW": "candidate_count <= 5", "MEDIUM": "6 <= candidate_count <= 15", "HIGH": "candidate_count > 15"},
        "tier_counts": {tier: int((master.tier == tier).sum()) for tier in "ABCDEF"},
        "absolute_return_positive": int(master.absolute_return_positive.sum()),
        "absolute_be_positive": int(master.absolute_be_positive.sum()),
        "both_absolute_positive": int((master.absolute_return_positive & master.absolute_be_positive).sum()),
        "return_improved": int(master.return_beats_baseline.sum()),
        "return_equal": int(master.return_equals_baseline.sum()),
        "return_worse": int(master.return_worse_than_baseline.sum()),
        "be_improved": int(master.be_beats_baseline.sum()),
        "return_and_be_improved": int((master.return_beats_baseline & master.be_beats_baseline).sum()),
        "return_improved_majority_folds": int(master.return_improves_majority_folds.sum()),
        "be_improved_majority_folds": int(master.be_improves_majority_folds.sum()),
        "lofo_robust_return_improvement": int(master.lofo_robust_return.sum()),
        "full_range_drift": int(master.full_range_drift.sum()),
        "single_fold_dominated": int(master.single_fold_dominated.sum()),
        "isolated_validation_optimum": int(master.isolated_validation_optimum.sum()),
        "highly_unstable": int((master.stability_severity == "HIGHLY_UNSTABLE").sum()),
        "new_parameter_search_backtests": 0,
        "test_informed_reselection": 0,
        "production_configs_generated": 0,
    }
    build_html(master, tiers, summary, args.output_root)
    after = tree_hash(protected)
    atomic_json(args.output_root / "phase3c_protected_hashes_after.json", after)
    summary["protected_hash_changes"] = 0 if before["files"] == after["files"] else len(set(before["files"]) ^ set(after["files"])) + sum(before["files"].get(key) != after["files"].get(key) for key in set(before["files"]) & set(after["files"]))
    summary["phase3b_hash_invariance_passed"] = summary["protected_hash_changes"] == 0
    if not summary["phase3b_hash_invariance_passed"]:
        summary["status"] = "FAILED"
    atomic_json(args.output_root / "phase3c_validation_summary.json", summary)
    zip_path, sha = package(args.output_root, args.deliverable_root)
    atomic_json(args.output_root / "phase3c_delivery.json", {"zip_path": str(zip_path), "sha256": sha, "zip_integrity": "PASSED"})
    print(json.dumps({**summary, "zip_path": str(zip_path), "zip_sha256": sha}, indent=2))
    return 0 if summary["status"] == "PASSED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
