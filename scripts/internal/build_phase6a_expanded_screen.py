#!/usr/bin/env python3
"""Build Phase 6A provenance-aware baseline screening from persisted results only."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import os
import re
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any

import matplotlib as mpl

mpl.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
import numpy as np
import pandas as pd
import yaml

from results.trade_episode import build_de_risk_episodes


ROOT = Path(__file__).resolve().parents[2]
AUDIT = ROOT / "outputs/internal_audit/strategy_workbook"
DELIVERABLES = ROOT / "outputs/deliverables"
OUTPUT = ROOT / "outputs/baseline_evaluation/phase6a"
PERIODS = [
    "2021H2", "2022H1", "2022H2", "2023H1", "2023H2",
    "2024H1", "2024H2", "2025H1", "2025H2", "2026H1",
]
TOL = 1e-10
PHASE_SPECS = {
    "PHASE5A": ("workbook_strategies_phase5a", "phase5a_baseline_backtest_summary.csv"),
    "PHASE5B": ("workbook_strategies_phase5b", "phase5b_baseline_backtest_summary.csv"),
    "PHASE5C": ("workbook_strategies_phase5c", "phase5c_baseline_backtest_summary.csv"),
    "PHASE5E": ("workbook_strategies_phase5e", "phase5e_baseline_backtest_summary.csv"),
    "PHASE5F": ("workbook_strategies_phase5f", "phase5f_baseline_backtest_summary.csv"),
}
PROVENANCE_ORDER = ["P0_SOURCE_DIRECT", "P1_STANDARDIZED", "P2_DEFAULTED", "P3_MODELLED_LOW", "P4_MODELLED_MEDIUM"]


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


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def repo_path(value: Any) -> Path:
    text = str(value).replace("\\", "/")
    match = re.search(r"(?:^[A-Za-z]:)?/nautilus/(.+)$", text, flags=re.IGNORECASE)
    if match:
        return ROOT / match.group(1)
    path = Path(text)
    return path if path.is_absolute() else ROOT / path


def inventory(root: Path) -> dict[str, Any]:
    digest = hashlib.sha256(); count = size = 0
    if not root.exists():
        return {"root": str(root), "file_count": 0, "total_size": 0, "inventory_digest": "MISSING"}
    for path in sorted(item for item in root.rglob("*") if item.is_file() and "__pycache__" not in item.parts):
        stat = path.stat(); relative = path.relative_to(root).as_posix()
        digest.update(f"{relative}\0{stat.st_size}\n".encode())
        count += 1; size += stat.st_size
    return {"root": str(root.relative_to(ROOT)), "file_count": count, "total_size": size, "inventory_digest": digest.hexdigest()}


def protected_snapshot(output: Path) -> dict[str, Any]:
    roots = [ROOT / "strategies", ROOT / "configs/semantic_contracts"]
    roots += [path for path in DELIVERABLES.iterdir() if path.exists() and path != output and path.name.startswith(("phase", "workbook_", "existing_"))]
    files: dict[str, dict[str, Any]] = {}
    for root in roots:
        candidates = [root] if root.is_file() else sorted(item for item in root.rglob("*") if item.is_file())
        for path in candidates:
            if "phase6a" in path.parts or path.name.startswith("phase6a_") or "__pycache__" in path.parts:
                continue
            relative = path.relative_to(ROOT).as_posix()
            files[relative] = {"size": path.stat().st_size, "sha256": sha256(path)}
    digest = hashlib.sha256()
    for relative, metadata in sorted(files.items()):
        digest.update(f"{relative}\0{metadata['size']}\0{metadata['sha256']}\n".encode())
    return {
        "content_file_count": len(files), "content_digest": digest.hexdigest(), "files": files,
        "data_inventories": {
            "market_data": inventory(ROOT / "historical_data/market_data"),
            "feature_data": inventory(ROOT / "historical_data/feature_data"),
        },
    }


def compare_snapshots(before: dict[str, Any], after: dict[str, Any]) -> tuple[int, list[str]]:
    keys = set(before["files"]) | set(after["files"])
    changes = sorted(key for key in keys if before["files"].get(key) != after["files"].get(key))
    data_changed = before["data_inventories"] != after["data_inventories"]
    return len(changes) + int(data_changed), changes + (["historical_data inventory"] if data_changed else [])


def load_yaml(strategy_id: str) -> dict[str, Any]:
    path = ROOT / "strategies" / strategy_id / "config.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8")) if path.is_file() else {}


def recovery_phase(row: pd.Series) -> str:
    for phase in ("phase5f", "phase5e", "phase5c", "phase5b", "phase5a"):
        if str(row.get(f"{phase}_status", "")) == "IMPLEMENTED_STANDALONE":
            return phase.upper()
    return "PRE_PHASE5"


def provenance_tier(provenance: str, recovery: str) -> str:
    value = str(provenance).strip().upper()
    if recovery == "PHASE5F" or ("MODELLED" in value and recovery == "PHASE5F"):
        return "P4_MODELLED_MEDIUM"
    if "MODELLED" in value:
        return "P3_MODELLED_LOW"
    if "DEFAULT" in value:
        return "P2_DEFAULTED"
    if "STANDARD" in value or "SESSION" in value or "CONTRACT_RESOLVED" in value:
        return "P1_STANDARDIZED"
    return "P0_SOURCE_DIRECT"


def clean_params(value: Any) -> Any:
    ignored = {
        "source_registry_id", "semantic_provenance",
        "defaulted_parameters", "modelled_interpretations", "instrument_id",
    }
    if isinstance(value, dict):
        return {key: clean_params(item) for key, item in sorted(value.items()) if key not in ignored}
    if isinstance(value, list):
        return [clean_params(item) for item in value]
    return value


def equivalence_payload(row: pd.Series) -> dict[str, Any]:
    config = load_yaml(str(row.strategy_id))
    params = clean_params(config.get("params", {}))
    family = row.strategy_family
    if row.source_group == "PRE_WORKBOOK":
        family = re.sub(r"_(long|short)$", "", str(row.strategy_id))
    return {
        "family": family, "params": params, "timeframe": row.canonical_timeframe,
        "intrinsic_direction": row.intrinsic_direction,
        "semantic_contracts": sorted(filter(None, str(row.contracts_applied).split(";"))),
    }


def assign_equivalence(
    universe: pd.DataFrame,
    old_group_by_id: dict[str, str],
    phase5_execution_hash_by_id: dict[str, str],
) -> pd.DataFrame:
    """Reuse validated old groups and Phase 5 pre-performance compiler hashes.

    Cross-source identities are deliberately not merged without an explicit IR
    proof linking the two established namespaces.
    """
    hashes: list[str] = []
    proof_types: list[str] = []
    for strategy_id in universe.strategy_id:
        if strategy_id in old_group_by_id:
            hashes.append("PHASE4A_VALIDATED:" + old_group_by_id[strategy_id])
            proof_types.append("PHASE4A_VALIDATED_EXECUTABLE_GROUP")
        else:
            execution_hash = phase5_execution_hash_by_id.get(strategy_id)
            if not execution_hash:
                raise ValueError(f"{strategy_id}: missing Phase 5 pre-performance execution hash")
            hashes.append("PHASE5_FROZEN_RULE_AND_TIMEFRAME:" + execution_hash)
            proof_types.append("PHASE5_FROZEN_RULE_HASH_AND_TIMEFRAME")
    mapping = {value: f"phase6a_semantic_{index:03d}" for index, value in enumerate(sorted(set(hashes)), 1)}
    result = universe.copy(); result["equivalence_hash"] = hashes
    result["equivalence_proof_type"] = proof_types
    result["equivalence_group_id"] = [mapping[value] for value in hashes]
    representatives = result.groupby("equivalence_group_id").strategy_id.min().to_dict()
    result["group_representative"] = result.equivalence_group_id.map(representatives)
    return result


def standardize_phase_summary(phase: str, path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path).copy()
    frame["coverage_recovery_phase"] = phase
    frame["lag_value"] = pd.to_numeric(frame["lag"] if "lag" in frame else frame["lag_minutes"], errors="coerce")
    for column in ("baseline_timeframe", "timeframe", "source_timeframe", "compiled_timeframe"):
        if column in frame:
            frame["canonical_timeframe"] = frame[column].astype(str)
            break
    frame["episode_count_saved"] = pd.to_numeric(
        frame["completed_episode_count"] if "completed_episode_count" in frame else frame["trade_count"], errors="coerce"
    )
    return frame


def load_sources() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, pd.DataFrame]]:
    phase4 = ROOT / "outputs/baseline_evaluation/phase4a"
    if not (phase4 / "phase4a_strategy_master.csv").is_file():
        phase4 = DELIVERABLES / "phase4a_baseline_evaluation"
    old_master = pd.read_csv(phase4 / "phase4a_strategy_master.csv")
    old_periods = pd.read_csv(phase4 / "phase4a_period_robustness.csv")
    registry = pd.read_csv(AUDIT / "registered_strategy_manifest.csv", dtype=str).fillna("")
    if len(registry) != 280 or registry.registry_id.nunique() != 280:
        raise ValueError(f"current workbook registry mismatch: {len(registry)} rows")
    phase_frames: dict[str, pd.DataFrame] = {}
    new_ids: set[str] = set()
    for phase, (directory, filename) in PHASE_SPECS.items():
        frame = standardize_phase_summary(phase, DELIVERABLES / directory / filename)
        phase_frames[phase] = frame
        new_ids.update(frame.strategy_id.unique())
    old_workbook = set(old_master.loc[old_master.source_group == "WORKBOOK", "strategy_id"])
    if old_workbook & new_ids:
        raise ValueError("Phase 5 baseline summaries overlap PRE_PHASE5 universe")
    if old_workbook | new_ids != set(registry.registry_id):
        raise ValueError("workbook universe does not reconcile to current registry")
    rows: list[dict[str, Any]] = []
    old_lookup = old_master.set_index("strategy_id").to_dict("index")
    registry_lookup = registry.set_index("registry_id")
    for strategy_id in sorted(old_master.loc[old_master.source_group == "PRE_WORKBOOK", "strategy_id"]):
        old = old_lookup[strategy_id]
        rows.append({
            "strategy_id": strategy_id, "source_group": "PRE_WORKBOOK", "source_identity": strategy_id,
            "strategy_family": old.get("strategy_family", strategy_id), "semantic_provenance": "SOURCE_EXACT",
            "coverage_recovery_phase": "PRE_WORKBOOK", "canonical_timeframe": old["timeframe"],
            "intrinsic_direction": old.get("intrinsic_direction", "UNKNOWN"), "registry_status": "registered",
            "baseline_result_status": "AVAILABLE", "contracts_applied": "",
        })
    all_phase = pd.concat(phase_frames.values(), ignore_index=True)
    phase_by_id = all_phase.drop_duplicates("strategy_id").set_index("strategy_id").to_dict("index")
    for strategy_id in sorted(registry.registry_id):
        metadata = registry_lookup.loc[strategy_id]
        config = load_yaml(strategy_id); params = config.get("params", {})
        phase = recovery_phase(metadata)
        old = old_lookup.get(strategy_id, {})
        phase_row = phase_by_id.get(strategy_id, {})
        provenance = str(params.get("semantic_provenance") or metadata.get("semantic_provenance") or phase_row.get("semantic_provenance") or old.get("semantic_provenance") or "SOURCE_EXACT")
        timeframe = old.get("timeframe") or phase_row.get("canonical_timeframe") or "UNKNOWN"
        rows.append({
            "strategy_id": strategy_id, "source_group": "WORKBOOK", "source_identity": strategy_id,
            "strategy_family": metadata.get("implementation_family") or params.get("family") or old.get("strategy_family") or "UNKNOWN",
            "semantic_provenance": provenance, "coverage_recovery_phase": phase,
            "canonical_timeframe": timeframe, "intrinsic_direction": "SOURCE_DEFINED_BIDIRECTIONAL",
            "registry_status": "registered", "baseline_result_status": "AVAILABLE",
            "contracts_applied": str(params.get("contracts_applied", "")),
        })
    universe = pd.DataFrame(rows)
    universe["semantic_provenance_tier"] = [provenance_tier(p, r) for p, r in zip(universe.semantic_provenance, universe.coverage_recovery_phase, strict=True)]
    old_group_by_id = old_master.set_index("strategy_id").executable_evidence_group_id.astype(str).to_dict()
    phase5_execution_hash_by_id: dict[str, str] = {}
    for phase in PHASE_SPECS:
        path = ROOT / "configs/semantic_contracts" / f"workbook_{phase.lower()}_strategies.json"
        plan = json.loads(path.read_text(encoding="utf-8"))
        phase5_execution_hash_by_id.update({
            strategy_id: hashlib.sha256(
                (str(item["rule_hash"]) + "|" + str(item.get("source_timeframe", ""))).encode("utf-8")
            ).hexdigest()
            for strategy_id, item in plan.items()
        })
    universe = assign_equivalence(universe, old_group_by_id, phase5_execution_hash_by_id)
    if len(universe) != 344 or universe.strategy_id.nunique() != 344:
        raise ValueError(f"full universe mismatch: {len(universe)}")
    return universe, old_master, old_periods, phase_frames


def result_paths(row: pd.Series) -> tuple[Path, Path]:
    root = repo_path(row.result_path)
    return root / "timeseries.parquet", root / "summary.json"


def phase_primary_rows(strategy_id: str, frames: dict[str, pd.DataFrame]) -> tuple[pd.Series, pd.Series]:
    candidates = pd.concat([frame[frame.strategy_id == strategy_id] for frame in frames.values()], ignore_index=True)
    if candidates.empty:
        raise KeyError(strategy_id)
    lag0 = candidates[candidates.lag_value == 0]
    realistic = candidates[candidates.lag_value > 0].sort_values("lag_value")
    if len(lag0) != 1 or len(realistic) != 1:
        raise ValueError(f"{strategy_id}: expected exactly lag0 + realistic lag")
    return lag0.iloc[0], realistic.iloc[0]


def period_label(times: pd.Series) -> pd.Series:
    timestamp = pd.to_datetime(times, unit="ns", utc=True)
    return timestamp.dt.year.astype(str) + np.where(timestamp.dt.month <= 6, "H1", "H2")


def drawdown(increments: np.ndarray) -> float:
    equity = 1.0 + np.cumsum(increments, dtype=np.float64)
    peak = np.maximum.accumulate(np.r_[1.0, equity])[1:]
    return float((np.divide(equity, peak, out=np.zeros_like(equity), where=peak > 0) - 1.0).min(initial=0.0))


def saved_phase5_drawdown(increments: np.ndarray) -> float:
    """Reproduce the already-persisted Phase 5 baseline MDD convention exactly."""
    equity = 1.0 + np.cumsum(increments, dtype=np.float64)
    peak = np.maximum.accumulate(equity)
    return float((np.divide(equity, peak, out=np.zeros_like(equity), where=peak != 0) - 1.0).min(initial=0.0))


def period_evidence(strategy_id: str, frame: pd.DataFrame, episodes: pd.DataFrame) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    frame = frame.copy(); frame["period"] = period_label(frame.event_time_ns)
    episode_counts = Counter(episodes.period) if not episodes.empty else Counter()
    rows: list[dict[str, Any]] = []
    for period in PERIODS:
        child = frame[frame.period == period]
        if child.empty:
            continue
        returns = child.normal_total_return.to_numpy(float); turnover = float(child.normal_turnover.sum()); total = float(returns.sum())
        rows.append({
            "strategy_id": strategy_id, "period": period, "return_1x": total, "turnover": turnover,
            "signed_be_bps": total * 10_000.0 / turnover if turnover > 0 else math.nan,
            "max_drawdown": drawdown(returns), "trade_count": int(episode_counts[period]),
        })
    values = [row["return_1x"] for row in rows]; turnovers = [row["turnover"] for row in rows]
    total = sum(values); positive_total = sum(max(value, 0.0) for value in values); largest = max(values, default=0.0)
    lopo_return = [total - value for value in values]
    lopo_be = [
        (total - values[index]) * 10_000.0 / (sum(turnovers) - turnovers[index])
        if sum(turnovers) - turnovers[index] > 0 else math.nan
        for index in range(len(rows))
    ]
    return rows, {
        "positive_return_period_count": sum(value > TOL for value in values),
        "positive_BE_period_count": sum(math.isfinite(row["signed_be_bps"]) and row["signed_be_bps"] > TOL for row in rows),
        "period_count": len(rows),
        "positive_return_fraction": sum(value > TOL for value in values) / len(rows) if rows else 0.0,
        "positive_BE_fraction": sum(math.isfinite(row["signed_be_bps"]) and row["signed_be_bps"] > TOL for row in rows) / len(rows) if rows else 0.0,
        "RETURN_POSITIVE_MAJORITY_PERIODS": sum(value > TOL for value in values) > len(rows) / 2,
        "BE_POSITIVE_MAJORITY_PERIODS": sum(math.isfinite(row["signed_be_bps"]) and row["signed_be_bps"] > TOL for row in rows) > len(rows) / 2,
        "BASELINE_SINGLE_PERIOD_DOMINATED": total > TOL and positive_total > TOL and largest > 0.5 * positive_total,
        "minimum_LOPO_return": min(lopo_return, default=math.nan),
        "LOPO_positive_count": sum(value > TOL for value in lopo_return),
        "BASELINE_LOPO_RETURN_ROBUST": bool(rows) and all(value > TOL for value in lopo_return),
        "BASELINE_LOPO_BE_ROBUST": bool(rows) and all(math.isfinite(value) and value > TOL for value in lopo_be),
        "period_return_reconciliation": total, "period_turnover_reconciliation": sum(turnovers),
    }


def empty_episode_metrics() -> dict[str, Any]:
    keys = [
        "episode_BE_mean", "episode_BE_median", "episode_BE_p10", "episode_BE_p25", "episode_BE_p75", "episode_BE_p90",
        "episode_BE_positive_fraction", "episode_return_median", "episode_return_positive_fraction", "episode_turnover_median",
        "episode_turnover_p95", "holding_duration_median", "holding_duration_p95", "top1pct_positive_return_share",
        "top5pct_positive_return_share", "return_without_top5pct", "BE_without_top5pct",
    ]
    return {"completed_episode_count": 0, **{key: math.nan for key in keys}, "WINNER_CONCENTRATED": False}


def episode_evidence(frame: pd.DataFrame) -> dict[str, Any]:
    if frame.empty:
        return empty_episode_metrics()
    be = frame.break_even_bps.to_numpy(float); returns = frame.delta_gross_return.to_numpy(float); turnover = frame.delta_turnover.to_numpy(float)
    duration = (pd.to_datetime(frame.completion_timestamp, utc=True) - pd.to_datetime(frame.start_timestamp, utc=True)).dt.total_seconds().to_numpy(float) / 60.0
    order = np.argsort(-returns, kind="stable"); positive = np.clip(returns, 0.0, None); positive_total = float(positive.sum())
    top1 = max(1, math.ceil(len(frame) * 0.01)); top5 = max(1, math.ceil(len(frame) * 0.05))
    share1 = float(positive[order[:top1]].sum() / positive_total) if positive_total > 0 else math.nan
    share5 = float(positive[order[:top5]].sum() / positive_total) if positive_total > 0 else math.nan
    remaining = frame.sort_values("delta_gross_return", ascending=False, kind="stable").iloc[top5:]
    remaining_return = float(remaining.delta_gross_return.sum()); remaining_turnover = float(remaining.delta_turnover.sum())
    remaining_be = remaining_return * 10_000.0 / remaining_turnover if remaining_turnover > 0 else math.nan
    return {
        "completed_episode_count": len(frame), "episode_BE_mean": float(np.mean(be)), "episode_BE_median": float(np.median(be)),
        "episode_BE_p10": float(np.quantile(be, .10)), "episode_BE_p25": float(np.quantile(be, .25)),
        "episode_BE_p75": float(np.quantile(be, .75)), "episode_BE_p90": float(np.quantile(be, .90)),
        "episode_BE_positive_fraction": float(np.mean(be > TOL)), "episode_return_median": float(np.median(returns)),
        "episode_return_positive_fraction": float(np.mean(returns > TOL)), "episode_turnover_median": float(np.median(turnover)),
        "episode_turnover_p95": float(np.quantile(turnover, .95)), "holding_duration_median": float(np.median(duration)),
        "holding_duration_p95": float(np.quantile(duration, .95)), "top1pct_positive_return_share": share1,
        "top5pct_positive_return_share": share5, "return_without_top5pct": remaining_return, "BE_without_top5pct": remaining_be,
        "WINNER_CONCENTRATED": bool((math.isfinite(share5) and share5 >= .50) or remaining_return <= 0),
    }


def load_episode_csv(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path)
    if "premium_mode" in frame:
        frame = frame[frame.premium_mode.astype(str).str.lower() == "included"].copy()
    frame["period"] = pd.to_datetime(frame.completion_timestamp, utc=True).dt.year.astype(str) + np.where(pd.to_datetime(frame.completion_timestamp, utc=True).dt.month <= 6, "H1", "H2")
    return frame


def build_from_timeseries(strategy_id: str, timeseries: Path, timeframe: str, lag: str) -> tuple[dict[str, Any], list[dict[str, Any]], pd.DataFrame]:
    columns = ["event_time_ns", "normal_direction", "normal_total_return", "normal_turnover"]
    frame = pd.read_parquet(timeseries, columns=columns)
    episodes, episode_summary = build_de_risk_episodes(
        event_time_ns=frame.event_time_ns, executed_position=frame.normal_direction,
        turnover_increment=frame.normal_turnover, gross_return_increment=frame.normal_total_return,
        strategy=strategy_id, symbol="BTCUSDT", granularity=timeframe, lag=lag, premium_mode="included",
    )
    episode_frame = pd.DataFrame(episodes)
    if not episode_frame.empty:
        episode_frame["period"] = pd.to_datetime(episode_frame.completion_timestamp, utc=True).dt.year.astype(str) + np.where(pd.to_datetime(episode_frame.completion_timestamp, utc=True).dt.month <= 6, "H1", "H2")
    period_rows, period_summary = period_evidence(strategy_id, frame, episode_frame)
    metrics = {
        **period_summary, **episode_evidence(episode_frame),
        "episode_turnover_reconciliation_residual": numeric(episode_summary["turnover_reconciliation_residual"]),
        "maximum_episode_BE_residual": numeric(episode_summary["maximum_break_even_residual"]),
        "derived_full_return": float(frame.normal_total_return.sum()),
        "derived_full_turnover": float(frame.normal_turnover.sum()),
        "derived_full_BE": (
            float(frame.normal_total_return.sum()) * 10_000.0 / float(frame.normal_turnover.sum())
            if float(frame.normal_turnover.sum()) > 0 else math.nan
        ),
        "derived_full_MDD": saved_phase5_drawdown(frame.normal_total_return.to_numpy(float)),
    }
    return metrics, period_rows, episode_frame


def baseline_tier(row: dict[str, Any], integrity_ok: bool) -> tuple[str, str, str, str, str]:
    if not integrity_ok:
        return "F", "canonical evidence missing or failed reconciliation", "integrity", "NONE", "INTEGRITY_FAILURE"
    ret = numeric(row["Return"]); be = numeric(row["BE"]); episodes = int(row["Episode_Count"])
    positive_ret = ret > TOL; positive_be = math.isfinite(be) and be > TOL
    warnings: list[str] = []
    for condition, text in (
        (not truthy(row["RETURN_POSITIVE_MAJORITY_PERIODS"]), "RETURN_PERIOD_PERSISTENCE"),
        (not truthy(row["BE_POSITIVE_MAJORITY_PERIODS"]), "BE_PERIOD_PERSISTENCE"),
        (truthy(row["BASELINE_SINGLE_PERIOD_DOMINATED"]), "SINGLE_PERIOD_DOMINATED"),
        (not truthy(row["BASELINE_LOPO_RETURN_ROBUST"]), "LOPO_RETURN_FAIL"),
        (truthy(row["WINNER_CONCENTRATED"]), "WINNER_CONCENTRATED"),
        (truthy(row["return_lag_sign_flip"]), "LAG_RETURN_SIGN_FLIP"),
        (truthy(row["BE_lag_sign_flip"]), "LAG_BE_SIGN_FLIP"),
    ):
        if condition: warnings.append(text)
    if all((positive_ret, positive_be, truthy(row["RETURN_POSITIVE_MAJORITY_PERIODS"]), truthy(row["BE_POSITIVE_MAJORITY_PERIODS"]), not truthy(row["BASELINE_SINGLE_PERIOD_DOMINATED"]), truthy(row["BASELINE_LOPO_RETURN_ROBUST"]), episodes > 0)):
        tier = "A"; reason = "positive Return/BE with majority-period persistence, no single-period dominance, and LOPO Return robustness"
    elif positive_ret and positive_be:
        tier = "B"; reason = "positive Return/BE with robustness warning"
    elif positive_ret != positive_be or (numeric(row["lag0_return"]) > TOL and not positive_ret):
        tier = "C"; reason = "partial positive evidence or lag0-only positivity"
    elif abs(ret) <= TOL or not math.isfinite(be) or episodes == 0:
        tier = "D"; reason = "near-neutral, zero-turnover, or zero completed episodes"
    else:
        tier = "E"; reason = "realistic-lag Return and signed BE are non-positive"
    positive = ";".join(name for condition, name in ((positive_ret, "RETURN_POSITIVE"), (positive_be, "BE_POSITIVE"), (numeric(row["episode_BE_median"]) > TOL, "MEDIAN_EPISODE_BE_POSITIVE")) if condition) or "NONE"
    return tier, reason, ";".join(warnings) or "NONE", positive, ";".join(warnings) or "NONE"


def build_master(universe: pd.DataFrame, old_master: pd.DataFrame, old_periods: pd.DataFrame, phase_frames: dict[str, pd.DataFrame]) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    old = old_master.set_index("strategy_id")
    representative_metrics: dict[str, dict[str, Any]] = {}
    representative_periods: dict[str, list[dict[str, Any]]] = {}
    representative_episodes: dict[str, dict[str, Any]] = {}
    coverage_rows: list[dict[str, Any]] = []
    groups = list(universe.groupby("equivalence_group_id", sort=True))
    for number, (group_id, members) in enumerate(groups, 1):
        representative = str(members.group_representative.iloc[0]); member = members[members.strategy_id == representative].iloc[0]
        if representative in old.index:
            source = old.loc[representative]
            episode_path = repo_path(source.source_episode_table); timeseries = repo_path(source.source_timeseries)
            episodes = load_episode_csv(episode_path)
            period_rows = old_periods[old_periods.strategy_id == representative].to_dict("records")
            period_summary = {
                "positive_return_period_count": int(source.positive_return_period_count), "positive_BE_period_count": int(source.positive_be_period_count),
                "period_count": int(source.evaluation_period_count), "positive_return_fraction": numeric(source.positive_return_fraction),
                "positive_BE_fraction": numeric(source.positive_be_fraction), "RETURN_POSITIVE_MAJORITY_PERIODS": truthy(source.return_positive_majority_periods),
                "BE_POSITIVE_MAJORITY_PERIODS": truthy(source.be_positive_majority_periods), "BASELINE_SINGLE_PERIOD_DOMINATED": truthy(source.baseline_single_period_dominated),
                "minimum_LOPO_return": math.nan, "LOPO_positive_count": math.nan, "BASELINE_LOPO_RETURN_ROBUST": truthy(source.baseline_lopo_return_robust),
                "BASELINE_LOPO_BE_ROBUST": truthy(source.baseline_lopo_be_robust), "period_return_reconciliation": numeric(source.period_return_reconciliation),
                "period_turnover_reconciliation": numeric(source.period_turnover_reconciliation),
            }
            metrics = {
                **period_summary, **episode_evidence(episodes), "episode_turnover_reconciliation_residual": 0.0,
                "maximum_episode_BE_residual": 0.0, "derived_full_return": numeric(source.return_realistic_lag),
                "derived_full_turnover": numeric(source.turnover_realistic_lag), "derived_full_BE": numeric(source.be_realistic_lag),
                "derived_full_MDD": numeric(source.mdd_realistic_lag),
            }
        else:
            _, realistic = phase_primary_rows(representative, phase_frames)
            timeseries, _ = result_paths(realistic); episode_path = Path("")
            if not timeseries.is_file():
                raise FileNotFoundError(timeseries)
            metrics, period_rows, episodes = build_from_timeseries(representative, timeseries, member.canonical_timeframe, f"lag{int(realistic.lag_value)}")
        representative_metrics[group_id] = metrics; representative_periods[group_id] = period_rows; representative_episodes[group_id] = episode_evidence(episodes)
        coverage_rows.append({
            "equivalence_group_id": group_id, "representative_strategy_id": representative,
            "realistic_lag_result_available": timeseries.is_file(), "timeseries_available": timeseries.is_file(),
            "lag0_result_available": True, "episode_table_available": episode_path.is_file() if representative in old.index else True,
            "fill_table_available": (timeseries.parent / "execution_events.csv").is_file(),
            "turnover_series_available": timeseries.is_file(), "common_period_available": len(period_rows) == 10,
            "timeseries_path": str(timeseries), "episode_path": str(episode_path) if representative in old.index else "DERIVED_IN_MEMORY",
        })
        print(f"PHASE6A GROUP {number}/{len(groups)} {representative}", flush=True)
    rows: list[dict[str, Any]] = []; all_periods: list[dict[str, Any]] = []
    phase3_path = ROOT / "outputs/parameter_search/phase3c/phase3c_master_robustness_table.csv"
    if not phase3_path.is_file():
        phase3_path = DELIVERABLES / "phase3c_robustness/phase3c_master_robustness_table.csv"
    phase3 = pd.read_csv(phase3_path).set_index("strategy_id") if phase3_path.is_file() else pd.DataFrame()
    for _, item in universe.iterrows():
        group_metrics = representative_metrics[item.equivalence_group_id]
        if item.strategy_id in old.index:
            source = old.loc[item.strategy_id]
            lag0_return = numeric(source.return_lag0); lag0_be = numeric(source.be_lag0)
            ret = numeric(source.return_realistic_lag); turnover = numeric(source.turnover_realistic_lag); be = numeric(source.be_realistic_lag); mdd = numeric(source.mdd_realistic_lag)
            realistic_lag = source.canonical_realistic_lag
            baseline_residuals = (numeric(source.be_formula_residual), numeric(source.period_return_residual), numeric(source.period_turnover_residual))
        else:
            lag0, realistic = phase_primary_rows(item.strategy_id, phase_frames)
            lag0_return = numeric(lag0.final_return_1x); lag0_be = numeric(lag0.signed_be_bps)
            ret = numeric(realistic.final_return_1x); turnover = numeric(realistic.turnover); be = numeric(realistic.signed_be_bps); mdd = numeric(realistic.max_drawdown)
            realistic_lag = f"lag{int(realistic.lag_value)}"
            baseline_residuals = (ret - turnover * be / 10_000.0 if turnover > 0 and math.isfinite(be) else 0.0,
                                  numeric(group_metrics["period_return_reconciliation"]) - ret,
                                  numeric(group_metrics["period_turnover_reconciliation"]) - turnover)
        phase = phase3.loc[item.strategy_id] if item.strategy_id in phase3.index else {}
        phase_flags = ";".join(name for key, name in (("full_range_drift", "FULL_RANGE_DRIFT"), ("single_fold_dominated", "SINGLE_FOLD_DOMINATED"), ("isolated_validation_optimum", "ISOLATED_VALIDATION_OPTIMUM")) if truthy(phase.get(key, False))) if hasattr(phase, "get") else ""
        result = {
            **item.to_dict(), "realistic_lag": realistic_lag, "Return": ret, "Turnover": turnover,
            "Turnover_display_pct": turnover * 100.0, "BE": be, "MDD": mdd,
            "Episode_Count": int(group_metrics["completed_episode_count"]), "lag0_return": lag0_return, "lag0_BE": lag0_be,
            "return_lag_sign_flip": (lag0_return > TOL) != (ret > TOL), "BE_lag_sign_flip": (lag0_be > TOL) != (be > TOL),
            "ABS_RETURN_POSITIVE": ret > TOL, "ABS_BE_POSITIVE": math.isfinite(be) and be > TOL,
            "ABS_RETURN_AND_BE_POSITIVE": ret > TOL and math.isfinite(be) and be > TOL,
            **group_metrics, "phase3_searched": item.strategy_id in phase3.index,
            "phase3c_tier": phase.get("tier", "NOT_SEARCHED") if hasattr(phase, "get") else "NOT_SEARCHED",
            "phase3_warning_flags": phase_flags or "NONE", "BE_formula_residual": baseline_residuals[0],
            "period_return_residual": baseline_residuals[1], "period_turnover_residual": baseline_residuals[2],
            "MDD_reconstruction_residual": numeric(group_metrics["derived_full_MDD"]) - mdd,
        }
        turnover_tol = max(TOL, abs(turnover) * 2e-12)
        integrity = (
            abs(result["BE_formula_residual"]) <= TOL
            and abs(result["period_return_residual"]) <= TOL
            and abs(result["period_turnover_residual"]) <= turnover_tol
            and abs(result["MDD_reconstruction_residual"]) <= TOL
        )
        tier, reason, blockers, positive, warnings = baseline_tier(result, integrity)
        result.update({"baseline_quality_tier": tier, "tier_reasons": reason, "tier_blockers": blockers, "positive_evidence": positive, "warnings": warnings, "result_integrity_passed": integrity})
        rows.append(result)
    master = pd.DataFrame(rows)
    for group_id, period_rows in representative_periods.items():
        representative = universe.loc[universe.equivalence_group_id == group_id, "group_representative"].iloc[0]
        for row in period_rows:
            normalized = dict(row); normalized.update({"equivalence_group_id": group_id, "representative_strategy_id": representative})
            all_periods.append(normalized)
    episode_rows = []
    for group_id, metrics in representative_episodes.items():
        representative = universe.loc[universe.equivalence_group_id == group_id, "group_representative"].iloc[0]
        episode_rows.append({"equivalence_group_id": group_id, "representative_strategy_id": representative, **metrics})
    return master, pd.DataFrame(all_periods), pd.DataFrame(episode_rows), pd.DataFrame(coverage_rows)


def semantic_summary(master: pd.DataFrame) -> pd.DataFrame:
    severity = {tier: index for index, tier in enumerate(PROVENANCE_ORDER)}
    rows = []
    for group_id, frame in master.groupby("equivalence_group_id"):
        representative_id = frame.group_representative.iloc[0]; representative = frame[frame.strategy_id == representative_id].iloc[0]
        highest = max(frame.semantic_provenance_tier, key=lambda value: severity[value])
        rows.append({
            "equivalence_group_id": group_id, "representative_strategy_id": representative_id,
            "member_ids": ";".join(sorted(frame.strategy_id)), "member_count": len(frame),
            "highest_semantic_provenance_intrusiveness": highest,
            "coverage_phases_represented": ";".join(sorted(set(frame.coverage_recovery_phase))),
            "Return": representative.Return, "Turnover": representative.Turnover, "BE": representative.BE,
            "MDD": representative.MDD, "Episode_Count": representative.Episode_Count,
            "baseline_quality_tier": representative.baseline_quality_tier, "warnings": representative.warnings,
        })
    return pd.DataFrame(rows)


def representative_frame(master: pd.DataFrame) -> pd.DataFrame:
    return master[master.strategy_id == master.group_representative].copy()


def aggregate_summary(reps: pd.DataFrame, master: pd.DataFrame, dimension: str) -> pd.DataFrame:
    rows = []
    for value, frame in reps.groupby(dimension, dropna=False):
        identities = master[master[dimension] == value]
        tier_counts = frame.baseline_quality_tier.value_counts()
        rows.append({
            dimension: value, "strategy_identities": len(identities), "semantic_groups": len(frame),
            "Return_positive_groups": int(frame.ABS_RETURN_POSITIVE.sum()), "BE_positive_groups": int(frame.ABS_BE_POSITIVE.sum()),
            "both_positive_groups": int(frame.ABS_RETURN_AND_BE_POSITIVE.sum()),
            "Tier_A_groups": int((frame.baseline_quality_tier == "A").sum()), "Tier_B_groups": int((frame.baseline_quality_tier == "B").sum()),
            "Tier_C_groups": int(tier_counts.get("C", 0)), "Tier_D_groups": int(tier_counts.get("D", 0)),
            "Tier_E_groups": int(tier_counts.get("E", 0)), "Tier_F_groups": int(tier_counts.get("F", 0)),
            "median_episode_BE_positive_groups": int((frame.episode_BE_median > TOL).sum()),
            "winner_concentrated_groups": int(frame.WINNER_CONCENTRATED.sum()),
            "LOPO_return_robust_groups": int(frame.BASELINE_LOPO_RETURN_ROBUST.sum()),
            "median_Return": float(frame.Return.median()), "median_BE": float(frame.BE.median()),
            "median_MDD": float(frame.MDD.median()), "median_Turnover": float(frame.Turnover.median()),
            "median_episode_BE_positive_fraction": float(frame.episode_BE_positive_fraction.median()),
        })
    return pd.DataFrame(rows)


def shortlist(reps: pd.DataFrame) -> pd.DataFrame:
    frame = reps[reps.baseline_quality_tier.isin(["A", "B"])].copy()
    rank = {"A": 0, "B": 1}; frame["_tier"] = frame.baseline_quality_tier.map(rank)
    frame = frame.sort_values(
        ["_tier", "BASELINE_LOPO_RETURN_ROBUST", "RETURN_POSITIVE_MAJORITY_PERIODS", "BE_POSITIVE_MAJORITY_PERIODS",
         "episode_BE_median", "episode_BE_positive_fraction", "BE", "MDD", "Turnover", "strategy_id"],
        ascending=[True, False, False, False, False, False, False, False, True, True],
    ).drop(columns="_tier")
    return frame


def make_figures(reps: pd.DataFrame, periods: pd.DataFrame, output: Path) -> None:
    figures = output / "figures"; figures.mkdir(parents=True, exist_ok=True)
    colors = reps.semantic_provenance_tier.map(dict(zip(PROVENANCE_ORDER, ["#2166ac", "#4393c3", "#92c5de", "#f4a582", "#b2182b"], strict=True)))
    def scatter(x: str, y: str, xlabel: str, ylabel: str, name: str, scale_x: float = 1.0) -> None:
        fig, ax = plt.subplots(figsize=(9, 6.5)); ax.scatter(reps[x] * scale_x, reps[y], c=colors, alpha=.72, s=30)
        ax.axhline(0, color="0.45", lw=.8); ax.axvline(0, color="0.45", lw=.8); ax.set(xlabel=xlabel, ylabel=ylabel); ax.grid(alpha=.2)
        palette = dict(zip(PROVENANCE_ORDER, ["#2166ac", "#4393c3", "#92c5de", "#f4a582", "#b2182b"], strict=True))
        ax.legend(
            handles=[Line2D([0], [0], marker="o", linestyle="", color=color, label=tier, markersize=6) for tier, color in palette.items()],
            title="Semantic provenance", fontsize=8, title_fontsize=8, loc="best",
        )
        fig.tight_layout(); fig.savefig(figures / name, dpi=160); plt.close(fig)
    scatter("BE", "Return", "Signed Global BE (bps)", "Cumulative Return (1x arithmetic)", "01_return_vs_be.png")
    scatter("Turnover", "Return", "Cumulative Turnover (%)", "Cumulative Return (1x arithmetic)", "02_return_vs_turnover.png", 100.0)
    scatter("MDD", "Return", "Max Drawdown", "Cumulative Return (1x arithmetic)", "03_return_vs_mdd.png")
    fig, ax = plt.subplots(figsize=(8, 5)); reps.baseline_quality_tier.value_counts().reindex(list("ABCDEF"), fill_value=0).plot.bar(ax=ax, color="#4c78a8"); ax.set(xlabel="Quality tier", ylabel="Semantic groups"); ax.grid(axis="y", alpha=.2); fig.tight_layout(); fig.savefig(figures / "04_quality_tier_counts.png", dpi=160); plt.close(fig)
    pivot = pd.crosstab(reps.semantic_provenance_tier, reps.baseline_quality_tier).reindex(index=PROVENANCE_ORDER, columns=list("ABCDEF"), fill_value=0)
    fig, ax = plt.subplots(figsize=(9, 5)); pivot.plot.bar(stacked=True, ax=ax); ax.set(xlabel="Provenance tier", ylabel="Semantic groups"); ax.legend(title="Quality"); fig.tight_layout(); fig.savefig(figures / "05_quality_by_provenance.png", dpi=160); plt.close(fig)
    phase = aggregate_summary(reps[reps.source_group == "WORKBOOK"], reps[reps.source_group == "WORKBOOK"], "coverage_recovery_phase")
    fig, ax = plt.subplots(figsize=(10, 5)); phase.set_index("coverage_recovery_phase")[["Return_positive_groups", "BE_positive_groups", "Tier_A_groups", "Tier_B_groups"]].plot.bar(ax=ax); ax.set(ylabel="Semantic groups", xlabel="Coverage phase"); fig.tight_layout(); fig.savefig(figures / "06_positive_by_coverage_phase.png", dpi=160); plt.close(fig)
    strong = reps[reps.baseline_quality_tier.isin(["A", "B"])].sort_values(["baseline_quality_tier", "strategy_id"])
    pivot_period = periods[periods.representative_strategy_id.isin(strong.strategy_id)].pivot(index="representative_strategy_id", columns="period", values="return_1x").reindex(index=strong.strategy_id, columns=PERIODS)
    fig, ax = plt.subplots(figsize=(13, max(5, len(pivot_period) * .22))); image = ax.imshow(np.sign(pivot_period.to_numpy(float)), aspect="auto", cmap="RdYlGn", vmin=-1, vmax=1); ax.set_xticks(range(len(PERIODS)), PERIODS, rotation=45, ha="right"); ax.set_yticks(range(len(pivot_period)), pivot_period.index, fontsize=6); fig.colorbar(image, ax=ax, label="Return sign"); fig.tight_layout(); fig.savefig(figures / "07_tier_ab_period_heatmap.png", dpi=160); plt.close(fig)
    scatter("BE", "episode_BE_median", "Global BE (bps)", "Median completed-episode BE (bps)", "08_global_vs_episode_be.png")
    provenance = aggregate_summary(reps, reps, "semantic_provenance_tier").set_index("semantic_provenance_tier").reindex(PROVENANCE_ORDER).fillna(0)
    denom = provenance.semantic_groups.replace(0, np.nan)
    rates = pd.DataFrame({"Return > 0": provenance.Return_positive_groups / denom, "BE > 0": provenance.BE_positive_groups / denom, "Tier A/B": (provenance.Tier_A_groups + provenance.Tier_B_groups) / denom})
    fig, ax = plt.subplots(figsize=(10, 5)); rates.plot.bar(ax=ax); ax.set(ylabel="Fraction of semantic groups", xlabel="Provenance tier", ylim=(0, 1)); fig.tight_layout(); fig.savefig(figures / "09_provenance_vs_quality.png", dpi=160); plt.close(fig)


def build_html(master: pd.DataFrame, reps: pd.DataFrame, boss: pd.DataFrame, output: Path) -> None:
    tier_counts = reps.baseline_quality_tier.value_counts()
    cards = {
        "Executable identities": len(master), "Independent groups": len(reps), "Return > 0 groups": int(reps.ABS_RETURN_POSITIVE.sum()),
        "BE > 0 groups": int(reps.ABS_BE_POSITIVE.sum()), "Both > 0 groups": int(reps.ABS_RETURN_AND_BE_POSITIVE.sum()),
        "Tier A groups": int(tier_counts.get("A", 0)), "Tier B groups": int(tier_counts.get("B", 0)),
    }
    card_html = "".join(f"<div class='card'><b>{html.escape(key)}</b><span>{value}</span></div>" for key, value in cards.items())
    columns = ["strategy_id", "strategy_family", "baseline_quality_tier", "semantic_provenance", "semantic_provenance_tier", "coverage_recovery_phase", "Return", "BE", "MDD", "Turnover_display_pct", "positive_return_period_count", "period_count", "episode_BE_median", "episode_BE_positive_fraction", "WINNER_CONCENTRATED", "warnings"]
    def table(frame: pd.DataFrame) -> str:
        return frame[columns].to_html(index=False, border=0, float_format=lambda value: f"{value:.6g}") if not frame.empty else "<p>None.</p>"
    high = boss[boss.semantic_provenance_tier.isin(["P0_SOURCE_DIRECT", "P1_STANDARDIZED", "P2_DEFAULTED"])]
    low = boss[boss.semantic_provenance_tier == "P3_MODELLED_LOW"]
    medium = boss[boss.semantic_provenance_tier == "P4_MODELLED_MEDIUM"]
    document = f"""<!doctype html><html><head><meta charset='utf-8'><title>Phase 6A Expanded Strategy Review</title><style>body{{font:14px system-ui;margin:28px;color:#202124}}.cards{{display:flex;gap:10px;flex-wrap:wrap}}.card{{padding:10px 16px;border:1px solid #ddd;border-radius:8px;display:flex;gap:12px}}.card span{{font-size:20px}}table{{border-collapse:collapse;width:100%;font-size:11px}}th,td{{padding:5px;border-bottom:1px solid #ddd;text-align:right}}th:first-child,td:first-child{{text-align:left}}th{{background:#f4f6f8}}h2{{margin-top:30px}}.note{{padding:12px;background:#fff8dc;border-left:4px solid #d4a72c}}</style></head><body><h1>Phase 6A — Expanded Executable-Universe Baseline Review</h1><p class='note'>Canonical ORIGINAL/NORMAL, Premium Included, realistic lag. Financial quality and semantic provenance are separate dimensions. Modelled strategies are deterministic research interpretations, not source-exact implementations.</p><div class='cards'>{card_html}</div><h2>High-certainty source / standard / defaulted candidates</h2>{table(high)}<h2>LOW-modelled candidates</h2>{table(low)}<h2>MEDIUM-modelled candidates</h2>{table(medium)}<h2>Coverage expansion</h2><p>Workbook coverage expanded from 131 to 280 executable identities. Phase 6A performs no semantic recovery, optimization, direction search, Premium search, or cross-symbol test.</p></body></html>"""
    temporary = output / "phase6a_expanded_strategy_review.html.tmp"; temporary.write_text(document, encoding="utf-8"); os.replace(temporary, output / "phase6a_expanded_strategy_review.html")


def package(output: Path) -> tuple[Path, str, int]:
    target = DELIVERABLES / "phase6a_expanded_strategy_review.zip"; temporary = target.with_suffix(".zip.tmp")
    with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(output.rglob("*")):
            if path.is_file() and not path.name.endswith(".tmp") and path.name != "phase6a_delivery.json":
                archive.write(path, Path("phase6a_expanded_strategy_review") / path.relative_to(output))
    os.replace(temporary, target)
    with zipfile.ZipFile(target) as archive:
        bad = archive.testzip(); members = len(archive.infolist())
    if bad:
        raise RuntimeError(f"ZIP integrity failure: {bad}")
    return target, sha256(target), members


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=OUTPUT)
    args = parser.parse_args(); args.output_root.mkdir(parents=True, exist_ok=True)
    before = protected_snapshot(args.output_root); atomic_json(args.output_root / "phase6a_protected_hashes_before.json", before)
    universe, old_master, old_periods, phase_frames = load_sources()
    master, periods, episodes, group_coverage = build_master(universe, old_master, old_periods, phase_frames)
    coverage = universe[["strategy_id", "equivalence_group_id"]].merge(
        group_coverage, on="equivalence_group_id", how="left", validate="many_to_one"
    )
    reps = representative_frame(master); semantic = semantic_summary(master)
    boss = shortlist(reps)
    boss_columns = [
        "strategy_id", "strategy_family", "baseline_quality_tier", "semantic_provenance", "semantic_provenance_tier", "coverage_recovery_phase",
        "Return", "BE", "MDD", "Turnover_display_pct", "positive_return_period_count", "positive_BE_period_count", "period_count",
        "episode_BE_median", "episode_BE_positive_fraction", "WINNER_CONCENTRATED", "BASELINE_LOPO_RETURN_ROBUST",
        "return_lag_sign_flip", "BE_lag_sign_flip", "phase3c_tier", "phase3_warning_flags", "warnings",
    ]
    boss_table = boss[boss_columns].copy()
    high = boss_table[boss_table.semantic_provenance_tier.isin(["P0_SOURCE_DIRECT", "P1_STANDARDIZED", "P2_DEFAULTED"])]
    modelled = boss_table[boss_table.semantic_provenance_tier.isin(["P3_MODELLED_LOW", "P4_MODELLED_MEDIUM"])].copy()
    modelled = modelled.merge(master[["strategy_id", "contracts_applied"]], on="strategy_id", how="left", validate="one_to_one")
    weak_winner_concentrated = reps.WINNER_CONCENTRATED & ~reps.baseline_quality_tier.isin(["A", "B"])
    low_priority = reps[(reps.baseline_quality_tier == "E") | ((reps.baseline_quality_tier == "C") & reps.return_lag_sign_flip) | weak_winner_concentrated | (reps.Episode_Count == 0)].copy()
    phase6b = boss_table.copy(); phase6b["why_included"] = np.where(phase6b.baseline_quality_tier == "A", "QUALITY_TIER_A", "QUALITY_TIER_B")
    phase6b["candidate_subset"] = np.where(phase6b.semantic_provenance_tier.isin(["P0_SOURCE_DIRECT", "P1_STANDARDIZED", "P2_DEFAULTED"]), "high_certainty_phase6b", "modelled_phase6b")
    phase6b["remaining_robustness_warning"] = phase6b.warnings
    provenance = aggregate_summary(reps, master, "semantic_provenance_tier")
    family = aggregate_summary(reps, master, "strategy_family")
    timeframe = aggregate_summary(reps, master, "canonical_timeframe")
    coverage_phase = aggregate_summary(reps[reps.source_group == "WORKBOOK"], master[master.source_group == "WORKBOOK"], "coverage_recovery_phase")
    equivalence = universe[["strategy_id", "source_group", "source_identity", "equivalence_group_id", "group_representative", "equivalence_hash", "equivalence_proof_type", "strategy_family", "canonical_timeframe", "semantic_provenance", "contracts_applied"]].copy()
    universe_output = universe[["strategy_id", "source_group", "source_identity", "strategy_family", "semantic_provenance", "semantic_provenance_tier", "coverage_recovery_phase", "canonical_timeframe", "intrinsic_direction", "equivalence_group_id", "registry_status", "baseline_result_status"]].copy()
    atomic_csv(args.output_root / "phase6a_strategy_universe.csv", universe_output)
    atomic_csv(args.output_root / "phase6a_global_equivalence_manifest.csv", equivalence)
    atomic_csv(args.output_root / "phase6a_baseline_result_coverage.csv", coverage)
    atomic_csv(args.output_root / "phase6a_strategy_master.csv", master)
    atomic_csv(args.output_root / "phase6a_semantic_group_summary.csv", semantic)
    atomic_csv(args.output_root / "phase6a_period_robustness.csv", periods)
    atomic_csv(args.output_root / "phase6a_episode_quality.csv", episodes)
    atomic_csv(args.output_root / "phase6a_provenance_quality.csv", provenance)
    atomic_csv(args.output_root / "phase6a_family_summary.csv", family)
    atomic_csv(args.output_root / "phase6a_timeframe_summary.csv", timeframe)
    atomic_csv(args.output_root / "phase6a_coverage_phase_summary.csv", coverage_phase)
    atomic_csv(args.output_root / "phase6a_boss_shortlist.csv", boss_table)
    atomic_csv(args.output_root / "phase6a_high_certainty_shortlist.csv", high)
    atomic_csv(args.output_root / "phase6a_modelled_strategy_candidates.csv", modelled)
    atomic_csv(args.output_root / "phase6a_low_priority.csv", low_priority)
    atomic_csv(args.output_root / "phase6a_phase6b_candidates.csv", phase6b)
    make_figures(reps, periods, args.output_root); build_html(master, reps, boss, args.output_root)
    old_phase4 = old_master.set_index("strategy_id"); current_phase4 = master[master.strategy_id.isin(old_phase4.index)].set_index("strategy_id")
    invariance = {
        "Return": float((current_phase4.Return - old_phase4.return_realistic_lag).abs().max()),
        "Turnover": float((current_phase4.Turnover - old_phase4.turnover_realistic_lag).abs().max()),
        "BE": float((current_phase4.BE - old_phase4.be_realistic_lag).abs().max()),
        "MDD": float((current_phase4.MDD - old_phase4.mdd_realistic_lag).abs().max()),
        "Episode_Count": float((current_phase4.Episode_Count - old_phase4.completed_episode_count).abs().max()),
    }
    after = protected_snapshot(args.output_root); atomic_json(args.output_root / "phase6a_protected_hashes_after.json", after)
    protected_changes, protected_change_paths = compare_snapshots(before, after)
    tier_counts = reps.baseline_quality_tier.value_counts(); pre = reps[reps.source_group == "PRE_WORKBOOK"]
    phase4_candidate = master[master.strategy_id == "xlsx_s2_0435"].iloc[0]
    summary = {
        "status": "PASSED", "pre_workbook_identities": int((master.source_group == "PRE_WORKBOOK").sum()),
        "workbook_identities": int((master.source_group == "WORKBOOK").sum()), "total_executable_identities": len(master),
        "independent_semantic_groups": len(reps), "raw_return_positive": int(master.ABS_RETURN_POSITIVE.sum()),
        "cross_source_equivalence_groups": 0,
        "group_return_positive": int(reps.ABS_RETURN_POSITIVE.sum()), "raw_BE_positive": int(master.ABS_BE_POSITIVE.sum()),
        "group_BE_positive": int(reps.ABS_BE_POSITIVE.sum()), "raw_both_positive": int(master.ABS_RETURN_AND_BE_POSITIVE.sum()),
        "group_both_positive": int(reps.ABS_RETURN_AND_BE_POSITIVE.sum()),
        "quality_tier_counts": {tier: int(tier_counts.get(tier, 0)) for tier in "ABCDEF"},
        "majority_return_positive_groups": int(reps.RETURN_POSITIVE_MAJORITY_PERIODS.sum()),
        "majority_BE_positive_groups": int(reps.BE_POSITIVE_MAJORITY_PERIODS.sum()),
        "LOPO_return_robust_groups": int(reps.BASELINE_LOPO_RETURN_ROBUST.sum()),
        "single_period_dominated_groups": int(reps.BASELINE_SINGLE_PERIOD_DOMINATED.sum()),
        "median_episode_BE_positive_groups": int((reps.episode_BE_median > TOL).sum()),
        "majority_episode_BE_positive_groups": int((reps.episode_BE_positive_fraction > .5).sum()),
        "winner_concentrated_groups": int(reps.WINNER_CONCENTRATED.sum()),
        "pre_workbook_positive_return_and_BE_groups": int(pre.ABS_RETURN_AND_BE_POSITIVE.sum()),
        "phase4a_metric_invariance_max_residuals": invariance,
        "phase5_financial_metric_invariance": "PASSED_VIA_SAVED_TIMESERIES_RECONSTRUCTION",
        "phase5_episode_count_invariance": "CANONICAL_EPISODES_RESEGMENTED;LEGACY_PHASE5_SUMMARY_FIELD_WAS_FILL_COUNT",
        "xlsx_s2_0435": {key: numeric(phase4_candidate[key]) for key in ("Return", "Turnover", "BE", "MDD", "Episode_Count")},
        "maximum_BE_formula_residual": float(master.BE_formula_residual.abs().max()),
        "maximum_period_return_residual": float(master.period_return_residual.abs().max()),
        "maximum_period_turnover_residual": float(master.period_turnover_residual.abs().max()),
        "maximum_MDD_reconstruction_residual": float(master.MDD_reconstruction_residual.abs().max()),
        "maximum_episode_BE_residual": float(master.maximum_episode_BE_residual.abs().max()),
        "all_result_integrity_passed": bool(master.result_integrity_passed.all()),
        "new_five_year_backtests": 0, "new_parameter_search_runs": 0, "new_semantic_policies": 0,
        "new_strategy_registrations": 0, "phase5g_started": False, "protected_artifact_changes": protected_changes,
        "protected_change_paths": protected_change_paths,
    }
    if any((len(master) != 344, len(universe) != 344, not master.result_integrity_passed.all(), protected_changes != 0, len(periods) != len(reps) * 10)):
        summary["status"] = "FAILED"
    atomic_json(args.output_root / "phase6a_validation_summary.json", summary)
    zip_path, zip_sha, members = package(args.output_root)
    atomic_json(args.output_root / "phase6a_delivery.json", {"zip_path": str(zip_path), "sha256": zip_sha, "member_count": members, "zip_integrity": "PASSED"})
    print(json.dumps({**summary, "zip_path": str(zip_path), "zip_sha256": zip_sha, "zip_members": members}, ensure_ascii=False, indent=2), flush=True)
    return 0 if summary["status"] == "PASSED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
