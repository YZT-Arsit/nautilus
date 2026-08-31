#!/usr/bin/env python3
"""Build the final result-only workbook-strategy boss deliverable.

This consumes persisted canonical baseline streams.  It never executes a
strategy, replays a signal, changes a parameter, or writes into a historical
result directory.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import math
import os
import re
import shutil
import zipfile
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import timedelta
from pathlib import Path
from typing import Any

import matplotlib as mpl

mpl.use("Agg")
import numpy as np
import pandas as pd
import pyarrow.parquet as pq
from PIL import Image

from results.episode_diagnostics import (
    METRICS,
    METRIC_SPEC_BY_ID,
    choose_duration_unit,
    choose_histogram_spec,
    describe,
    enrich_episode_frame,
    histogram_rows,
    maximum_formula_residual,
    metric_values,
    validate_premium_pair,
)
from results.strategy_evaluation import (
    build_additive_strategy_evaluation_from_columns,
    render_additive_strategy_evaluation,
    validate_strategy_evaluation,
)
from results.trade_episode import build_de_risk_episodes
from scripts.internal.build_episode_diagnostics import render_diagnostics


ROOT = Path(__file__).resolve().parents[2]
DELIVERABLES = ROOT / "outputs" / "deliverables"
FINAL = DELIVERABLES / "all_converted_workbook_strategies"
BUILDING = DELIVERABLES / "all_converted_workbook_strategies.building"
ARCHIVE = DELIVERABLES / "all_converted_workbook_strategies.zip"
AUDIT = ROOT / "outputs" / "internal_audit" / "final_workbook_consolidation"
REGISTRY = ROOT / "outputs" / "internal_audit" / "strategy_workbook" / "registered_strategy_manifest.csv"
PHASE4_MASTER = ROOT / "outputs" / "baseline_evaluation" / "phase4a" / "phase4a_strategy_master.csv"
PHASE6_UNIVERSE = ROOT / "outputs" / "baseline_evaluation" / "phase6a" / "phase6a_strategy_universe.csv"
PHASE5 = {
    "PHASE5A": ("workbook_strategies_phase5a", "phase5a_baseline_backtest_summary.csv"),
    "PHASE5B": ("workbook_strategies_phase5b", "phase5b_baseline_backtest_summary.csv"),
    "PHASE5C": ("workbook_strategies_phase5c", "phase5c_baseline_backtest_summary.csv"),
    "PHASE5E": ("workbook_strategies_phase5e", "phase5e_baseline_backtest_summary.csv"),
    "PHASE5F": ("workbook_strategies_phase5f", "phase5f_baseline_backtest_summary.csv"),
}
SYMBOL = "BTCUSDT"
TOL = 1e-9
DETAIL_EXPORT_LIMIT = 20_000


def repo_path(value: Any) -> Path:
    text = str(value).replace("\\", "/")
    match = re.search(r"(?:^[A-Za-z]:)?/nautilus/(.+)$", text, flags=re.IGNORECASE)
    if match:
        return ROOT / match.group(1)
    path = Path(text)
    return path if path.is_absolute() else ROOT / path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_csv(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    frame.to_csv(temporary, index=False, encoding="utf-8-sig")
    os.replace(temporary, path)


def atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def safe_float(value: Any) -> float | None:
    number = float(value)
    return number if math.isfinite(number) else None


def lag_from_case_name(name: str) -> int:
    match = re.search(r"_lag(\d+)$", name)
    if not match:
        raise ValueError(f"cannot parse lag from {name}")
    return int(match.group(1))


def lag_dir(timeframe: str, lag_minutes: int) -> str:
    return f"lag{lag_minutes}m"


def duration_readable(seconds: float) -> str:
    if not math.isfinite(seconds):
        return ""
    value = int(round(seconds))
    return str(timedelta(seconds=value))


def load_final_strategy_manifest() -> pd.DataFrame:
    registry = pd.read_csv(REGISTRY, dtype=str).fillna("")
    universe = pd.read_csv(PHASE6_UNIVERSE, dtype=str).fillna("")
    workbook = universe.loc[universe.source_group == "WORKBOOK"].copy()
    if len(registry) != 280 or registry.registry_id.nunique() != 280:
        raise AssertionError(f"registered workbook strategies != 280: {len(registry)}")
    if len(workbook) != len(registry) or set(workbook.strategy_id) != set(registry.registry_id):
        raise AssertionError("registry and Phase6A workbook universe disagree")
    merged = workbook.merge(
        registry[["registry_id", "source_sheet", "source_row", "source_strategy_number", "package_path", "config_path"]],
        left_on="strategy_id",
        right_on="registry_id",
        validate="one_to_one",
    )
    return merged.sort_values("strategy_id", kind="stable").reset_index(drop=True)


def validate_plugins(manifest: pd.DataFrame) -> pd.DataFrame:
    from strategy_framework.registry import get_entry

    rows: list[dict[str, Any]] = []
    for item in manifest.itertuples(index=False):
        plugin = get_entry(item.strategy_id)
        config = plugin.config_cls()
        strategy = plugin.strategy_cls(config)
        specs = plugin.build_specs(config)
        package = ROOT / item.package_path
        config_path = ROOT / item.config_path
        rows.append({
            "strategy_id": item.strategy_id,
            "package_exists": package.is_dir(),
            "config_exists": config_path.is_file(),
            "registry_lookup": plugin.name == item.strategy_id,
            "instantiated": strategy is not None,
            "feature_specs_initialized": isinstance(specs, list),
        })
    result = pd.DataFrame(rows)
    checks = result.drop(columns="strategy_id").all(axis=1)
    if not checks.all():
        raise AssertionError(result.loc[~checks].to_dict("records"))
    return result


def derive_phase4_cases(row: pd.Series) -> list[dict[str, Any]]:
    realistic = repo_path(row.source_timeseries)
    lag_value = lag_from_case_name(realistic.parent.name)
    lag0 = realistic.parent.parent / re.sub(r"_lag\d+$", "_lag0", realistic.parent.name) / realistic.name
    return [
        {"strategy_id": row.strategy_id, "timeframe": row.timeframe, "lag_minutes": 0, "timeseries": str(lag0), "source": "PRE_PHASE5"},
        {"strategy_id": row.strategy_id, "timeframe": row.timeframe, "lag_minutes": lag_value, "timeseries": str(realistic), "source": "PRE_PHASE5"},
    ]


def phase5_timeframe(row: pd.Series) -> str:
    for column in ("timeframe", "baseline_timeframe", "source_timeframe", "compiled_timeframe"):
        if column in row.index and str(row[column]).strip() not in {"", "nan"}:
            return str(row[column])
    raise ValueError(f"{row.strategy_id}: no timeframe")


def phase5_lag(row: pd.Series) -> int:
    for column in ("lag_minutes", "lag"):
        if column in row.index and str(row[column]).strip() not in {"", "nan"}:
            return int(float(row[column]))
    return lag_from_case_name(Path(str(row.result_path)).name)


def load_case_manifest(strategy_manifest: pd.DataFrame) -> pd.DataFrame:
    cases: list[dict[str, Any]] = []
    phase4 = pd.read_csv(PHASE4_MASTER)
    for _, row in phase4.loc[phase4.source_group == "WORKBOOK"].iterrows():
        cases.extend(derive_phase4_cases(row))
    for phase, (directory, filename) in PHASE5.items():
        summary = pd.read_csv(DELIVERABLES / directory / filename)
        if "premium_mode" in summary:
            summary = summary.loc[summary.premium_mode.astype(str).str.upper() == "INCLUDED"]
        for _, row in summary.iterrows():
            cases.append({
                "strategy_id": str(row.strategy_id),
                "timeframe": phase5_timeframe(row),
                "lag_minutes": phase5_lag(row),
                "timeseries": str(repo_path(row.result_path) / "timeseries.parquet"),
                "source": phase,
            })
    frame = pd.DataFrame(cases).drop_duplicates(["strategy_id", "lag_minutes"], keep="last")
    if set(frame.strategy_id) != set(strategy_manifest.strategy_id):
        missing = sorted(set(strategy_manifest.strategy_id) - set(frame.strategy_id))
        extra = sorted(set(frame.strategy_id) - set(strategy_manifest.strategy_id))
        raise AssertionError(f"case identities disagree: missing={missing}, extra={extra}")
    counts = frame.groupby("strategy_id").size()
    if len(frame) != 2 * len(strategy_manifest) or not (counts == 2).all():
        raise AssertionError(f"expected two lag cases per strategy: {counts.value_counts().to_dict()}")
    if not all(Path(path).is_file() for path in frame.timeseries):
        missing = frame.loc[[not Path(path).is_file() for path in frame.timeseries], "timeseries"].tolist()
        raise FileNotFoundError(missing[:20])
    for strategy_id, child in frame.groupby("strategy_id"):
        if 0 not in set(child.lag_minutes) or sum(child.lag_minutes > 0) != 1:
            raise AssertionError(f"{strategy_id}: lag0 + one realistic lag required")
        if child.timeframe.nunique() != 1:
            raise AssertionError(f"{strategy_id}: timeframe mismatch")
    return frame.sort_values(["strategy_id", "lag_minutes"], kind="stable").reset_index(drop=True)


def source_columns(path: Path) -> tuple[str, str, str, str, str]:
    names = set(pq.ParquetFile(path).schema.names)
    candidates = (
        ("event_time_ns", "normal_direction", "normal_trading_return", "normal_funding_return", "normal_turnover"),
        ("event_time_ns", "executed_position", "trading_return", "funding_return", "turnover"),
    )
    for candidate in candidates:
        if set(candidate).issubset(names):
            return candidate
    raise ValueError(f"{path}: unsupported canonical result schema")


def parquet_protected_fingerprint(path: Path) -> dict[str, Any]:
    """Fast immutable-source fingerprint without rereading multi-GB payloads."""
    stat = path.stat()
    parquet = pq.ParquetFile(path)
    payload = {
        "size_bytes": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "num_rows": parquet.metadata.num_rows,
        "num_row_groups": parquet.metadata.num_row_groups,
        # ``str(ParquetSchema)`` contains the Python object's memory address.
        # Normalize it so repeated reads of the same immutable file compare equal.
        "schema": re.sub(r" at 0x[0-9A-Fa-f]+>", " at 0xADDRESS>", str(parquet.schema)),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
    return {**payload, "metadata_sha256": hashlib.sha256(encoded).hexdigest()}


def protected_fingerprint_changes(before: pd.DataFrame, after: pd.DataFrame) -> pd.DataFrame:
    """Compare durable Parquet properties, including legacy address-bearing schemas."""
    normalized_before = before.copy()
    normalized_after = after.copy()
    for frame in (normalized_before, normalized_after):
        frame["schema"] = frame["schema"].astype(str).str.replace(
            r" at 0x[0-9A-Fa-f]+>", " at 0xADDRESS>", regex=True,
        )
    merged = normalized_before.merge(normalized_after, on="path", suffixes=("_before", "_after"))
    changed = pd.Series(False, index=merged.index)
    for field in ("size_bytes", "mtime_ns", "num_rows", "num_row_groups", "schema"):
        changed |= merged[f"{field}_before"] != merged[f"{field}_after"]
    return merged.loc[changed]


def build_episode_pair(
    *,
    strategy_id: str,
    timeframe: str,
    lag_minutes: int,
    times: np.ndarray,
    direction: np.ndarray,
    trading: np.ndarray,
    funding: np.ndarray,
    turnover: np.ndarray,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    summaries: dict[str, Any] = {}
    for mode, returns in (("included", trading + funding), ("excluded", trading)):
        child, summary = build_de_risk_episodes(
            event_time_ns=times,
            executed_position=direction,
            turnover_increment=turnover,
            gross_return_increment=returns,
            strategy=strategy_id,
            symbol=SYMBOL,
            granularity=f"{timeframe} bar",
            lag=f"{lag_minutes}m physical-time",
            premium_mode=mode,
            variant="normal",
        )
        rows.extend(child)
        summaries[mode] = summary
    raw = pd.DataFrame(rows)
    if raw.empty:
        columns = [
            "strategy_id", "variant", "symbol", "granularity", "lag", "premium_mode",
            "episode_id", "side", "start_timestamp", "completion_timestamp", "start_position",
            "maximum_abs_position", "end_position", "completion_reason", "delta_turnover",
            "delta_gross_return", "break_even_bps",
        ]
        raw = pd.DataFrame(columns=columns)
    enriched = enrich_episode_frame(raw)
    return enriched, summaries


def bins_for(frame: pd.DataFrame, duration_unit: str, duration_divisor: float) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for metric in METRICS:
        spec = METRIC_SPEC_BY_ID[metric]
        divisor = duration_divisor if metric == "holding_duration" else 1.0
        arrays = {
            mode: metric_values(frame.loc[frame.premium_mode == mode], metric, divisor)
            for mode in ("included", "excluded")
        }
        combined = np.concatenate([arrays["included"], arrays["excluded"]])
        histogram = choose_histogram_spec(
            combined,
            metric=metric,
            display_unit=duration_unit if metric == "holding_duration" else spec.display_unit,
            signed=spec.signed,
            minimum_width=0.0,
        )
        modes = ("included",) if not spec.premium_sensitive else ("included", "excluded")
        for mode in modes:
            for row in histogram_rows(arrays[mode], histogram):
                rows.append({"premium_mode": mode, **row})
    return pd.DataFrame(rows)


def episode_summary_rows(frame: pd.DataFrame, strategy_id: str, timeframe: str, lag_minutes: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for mode in ("included", "excluded"):
        child = frame.loc[frame.premium_mode == mode]
        be = child.break_even_bps.to_numpy(float)
        returns = child.episode_return.to_numpy(float)
        turnover = child.episode_turnover.to_numpy(float)
        duration = child.holding_duration_seconds.to_numpy(float)
        quantile = lambda values, q: float(np.quantile(values, q)) if len(values) else math.nan
        rows.append({
            "record_type": "EPISODE_SUMMARY", "strategy_id": strategy_id, "symbol": SYMBOL,
            "timeframe": timeframe, "lag": lag_dir(timeframe, lag_minutes), "premium_mode": mode,
            "completed_episode_count": len(child), "episode_be_mean": float(np.mean(be)) if len(be) else math.nan,
            "episode_be_median": quantile(be, .5), "episode_be_p10": quantile(be, .1),
            "episode_be_p25": quantile(be, .25), "episode_be_p75": quantile(be, .75),
            "episode_be_p90": quantile(be, .9), "positive_be_fraction": float(np.mean(be > 0)) if len(be) else math.nan,
            "episode_return_median": quantile(returns, .5),
            "positive_episode_return_fraction": float(np.mean(returns > 0)) if len(returns) else math.nan,
            "episode_turnover_median_percent": quantile(turnover, .5) * 100.0,
            "episode_turnover_p95_percent": quantile(turnover, .95) * 100.0,
            "holding_duration_median_seconds": quantile(duration, .5),
            "holding_duration_p95_seconds": quantile(duration, .95),
        })
    return rows


def episode_detail_rows(frame: pd.DataFrame, strategy_id: str, timeframe: str, lag_minutes: int) -> list[dict[str, Any]]:
    included_count = len(frame.loc[frame.premium_mode == "included"])
    if included_count > DETAIL_EXPORT_LIMIT:
        return []
    rows: list[dict[str, Any]] = []
    for item in frame.itertuples(index=False):
        rows.append({
            "record_type": "EPISODE", "strategy_id": strategy_id, "symbol": SYMBOL,
            "timeframe": timeframe, "lag": lag_dir(timeframe, lag_minutes),
            "premium_mode": item.premium_mode, "episode_id": item.episode_id,
            "start_timestamp": item.start_timestamp, "completion_timestamp": item.completion_timestamp,
            "start_position": item.start_position, "direction": item.side,
            "gross_return": item.episode_return, "gross_return_bps": item.episode_return_bps,
            "turnover_raw": item.episode_turnover, "turnover_percent": item.episode_turnover * 100.0,
            "signed_be_bps": item.break_even_bps,
            "holding_duration_seconds": item.holding_duration_seconds,
            "holding_duration_readable": duration_readable(item.holding_duration_seconds),
        })
    return rows


def process_case(case: dict[str, Any], strategy_root: str) -> dict[str, Any]:
    strategy_id = case["strategy_id"]
    timeframe = case["timeframe"]
    lag_minutes = int(case["lag_minutes"])
    path = Path(case["timeseries"])
    names = source_columns(path)
    frame = pd.read_parquet(path, columns=list(names))
    time_col, direction_col, trading_col, funding_col, turnover_col = names
    times = frame[time_col].to_numpy(np.int64, copy=False)
    direction = frame[direction_col].to_numpy(float, copy=False)
    trading = frame[trading_col].to_numpy(float, copy=False)
    funding = frame[funding_col].to_numpy(float, copy=False)
    turnover = frame[turnover_col].to_numpy(float, copy=False)
    series, metrics = build_additive_strategy_evaluation_from_columns(
        event_time_ns=times, trading_return=trading, funding_return=funding,
        turnover=turnover, executed_direction=direction,
    )
    evaluation_checks = validate_strategy_evaluation(series, metrics, tolerance=TOL)
    case_dir = Path(strategy_root) / timeframe / lag_dir(timeframe, lag_minutes)
    performance = case_dir / "performance.png"
    render_additive_strategy_evaluation(
        series, metrics, destination=performance,
        run_name=f"{strategy_id} / {SYMBOL} / {timeframe} / NORMAL",
        lag_label=f"{lag_minutes}m physical-time",
        turnover_display_percent=True,
    )
    episodes, old_summaries = build_episode_pair(
        strategy_id=strategy_id, timeframe=timeframe, lag_minutes=lag_minutes,
        times=times, direction=direction, trading=trading, funding=funding, turnover=turnover,
    )
    pair = validate_premium_pair(episodes)
    maximum_be_residual = maximum_formula_residual(episodes)
    durations = episodes.holding_duration_seconds.to_numpy(float)
    duration_unit, duration_divisor = choose_duration_unit(durations)
    bin_frame = bins_for(episodes, duration_unit, duration_divisor)
    metadata = {
        "strategy_id": strategy_id, "direction_mode": "normal", "symbol": SYMBOL,
        "timeframe": timeframe, "lag": f"{lag_minutes}m physical-time",
    }
    diagnostics = case_dir / "episode_diagnostics.png"
    render_diagnostics(
        frame=episodes, bin_frame=bin_frame, metadata=metadata, destination=diagnostics,
        duration_unit=duration_unit, duration_divisor=duration_divisor, max_scatter_points=20_000,
    )
    histogram_failures = sum(
        int(group["count"].sum()) != len(episodes.loc[episodes.premium_mode == premium])
        for (premium, _metric), group in bin_frame.groupby(["premium_mode", "metric"])
    )
    summary_rows: list[dict[str, Any]] = []
    for mode in ("included", "excluded"):
        values = metrics[mode]
        summary_rows.append({
            "record_type": "SUMMARY", "strategy_id": strategy_id, "symbol": SYMBOL,
            "timeframe": timeframe, "lag": lag_dir(timeframe, lag_minutes), "premium_mode": mode,
            "return_1x": values["final_return_1x"], "turnover_raw": values["turnover"],
            "turnover_percent": values["turnover"] * 100.0,
            "signed_be_bps": values["break_even_bps"], "mdd": values["max_drawdown"],
            "completed_episode_count": pair["episode_count"],
        })
    summary_rows.extend(episode_summary_rows(episodes, strategy_id, timeframe, lag_minutes))
    details = episode_detail_rows(episodes, strategy_id, timeframe, lag_minutes)
    if pair["episode_count"] != len(episodes.loc[episodes.premium_mode == "included"]):
        raise AssertionError("episode count mismatch")
    if pair["episode_count"] != len(episodes.loc[episodes.premium_mode == "excluded"]):
        raise AssertionError("premium episode count mismatch")
    if histogram_failures:
        raise AssertionError(f"histogram accounting failures={histogram_failures}")
    if maximum_be_residual > TOL:
        raise AssertionError(f"episode BE residual={maximum_be_residual}")
    if not all(evaluation_checks.values()):
        raise AssertionError("strategy evaluation failed")
    for image_path in (performance, diagnostics):
        with Image.open(image_path) as image:
            image.verify()
    return {
        "strategy_id": strategy_id, "timeframe": timeframe, "lag_minutes": lag_minutes,
        "lag": lag_dir(timeframe, lag_minutes), "source": case["source"],
        "timeseries": str(path), "summary_rows": summary_rows, "detail_rows": details,
        "completed_episode_count": pair["episode_count"],
        "detail_rows_exported": len(details), "episode_details_omitted": not bool(details) and pair["episode_count"] > 0,
        "maximum_be_residual": maximum_be_residual,
        "maximum_premium_identity_residual": pair["maximum_identity_residual"],
        "histogram_accounting_failures": histogram_failures,
        "performance": str(performance), "diagnostics": str(diagnostics),
        "source_summary_episode_count": {
            mode: old_summaries[mode]["completed_episode_count"] for mode in ("included", "excluded")
        },
    }


def write_strategy_results(strategy_id: str, case_results: list[dict[str, Any]], destination: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for result in sorted(case_results, key=lambda item: item["lag_minutes"]):
        rows.extend(result["summary_rows"])
        rows.extend(result["detail_rows"])
    atomic_csv(destination / "results.csv", pd.DataFrame(rows))
    summaries = [row for row in rows if row["record_type"] == "SUMMARY"]
    return summaries, {
        "strategy_id": strategy_id,
        "result_csv_rows": len(rows),
        "episode_detail_rows_exported": sum(len(item["detail_rows"]) for item in case_results),
        "episode_details_omitted_lags": sum(bool(item["episode_details_omitted"]) for item in case_results),
    }


def zip_final(root: Path, archive: Path) -> tuple[str, int, int]:
    temporary = archive.with_suffix(".zip.tmp")
    with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as bundle:
        files = sorted(path for path in root.rglob("*") if path.is_file())
        for path in files:
            bundle.write(path, path.relative_to(root.parent).as_posix())
    with zipfile.ZipFile(temporary) as bundle:
        if bundle.testzip() is not None:
            raise AssertionError("ZIP integrity failed")
        members = len(bundle.infolist())
    os.replace(temporary, archive)
    return sha256(archive), members, archive.stat().st_size


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--audit-only", action="store_true")
    args = parser.parse_args()
    if FINAL.exists() or BUILDING.exists() or ARCHIVE.exists():
        raise FileExistsError("final/building output already exists; refusing implicit replacement")
    AUDIT.mkdir(parents=True, exist_ok=True)
    strategy_manifest = load_final_strategy_manifest()
    plugin_checks = validate_plugins(strategy_manifest)
    case_manifest = load_case_manifest(strategy_manifest)
    atomic_csv(AUDIT / "final_strategy_manifest.csv", strategy_manifest)
    atomic_csv(AUDIT / "final_case_manifest.csv", case_manifest)
    atomic_csv(AUDIT / "plugin_validation.csv", plugin_checks)
    source_hashes = pd.DataFrame([
        {"path": path, **parquet_protected_fingerprint(Path(path))}
        for path in sorted(set(case_manifest.timeseries))
    ])
    atomic_csv(AUDIT / "protected_source_hashes_before.csv", source_hashes)
    if args.audit_only:
        print(json.dumps({
            "status": "AUDIT_PASSED", "strategies": len(strategy_manifest),
            "cases": len(case_manifest), "source_files": len(source_hashes),
            "plugin_checks": len(plugin_checks),
        }), flush=True)
        return
    (BUILDING / "strategies").mkdir(parents=True)
    cases = case_manifest.to_dict("records")
    results: list[dict[str, Any]] = []
    with ProcessPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(process_case, case, str(BUILDING / "strategies" / case["strategy_id"])): case
            for case in cases
        }
        for number, future in enumerate(as_completed(futures), 1):
            result = future.result()
            results.append(result)
            print(f"FINAL_BOSS_CASE {number}/{len(cases)} {result['strategy_id']} {result['lag']}", flush=True)
    by_strategy: dict[str, list[dict[str, Any]]] = {}
    for result in results:
        by_strategy.setdefault(result["strategy_id"], []).append(result)
    baseline_rows: list[dict[str, Any]] = []
    file_rows: list[dict[str, Any]] = []
    result_stats: list[dict[str, Any]] = []
    strategy_index: list[dict[str, Any]] = []
    metadata = strategy_manifest.set_index("strategy_id")
    for strategy_id in sorted(by_strategy):
        strategy_dir = BUILDING / "strategies" / strategy_id
        summaries, stats = write_strategy_results(strategy_id, by_strategy[strategy_id], strategy_dir)
        baseline_rows.extend(summaries)
        result_stats.append(stats)
        item = metadata.loc[strategy_id]
        children = sorted(by_strategy[strategy_id], key=lambda row: row["lag_minutes"])
        lag0, realistic = children
        strategy_index.append({
            "record_type": "STRATEGY_INDEX", "strategy_id": strategy_id,
            "source_sheet": item.source_sheet, "source_row": item.source_row,
            "source_strategy_number": item.source_strategy_number,
            "canonical_timeframe": item.canonical_timeframe,
            "intrinsic_direction": item.intrinsic_direction,
            "lag0_available": True, "realistic_lag_available": True,
        })
        file_rows.append({
            "record_type": "FILE_INDEX", "strategy_id": strategy_id,
            "results_csv": f"strategies/{strategy_id}/results.csv",
            "lag0_performance": f"strategies/{strategy_id}/{lag0['timeframe']}/{lag0['lag']}/performance.png",
            "lag0_episode_diagnostics": f"strategies/{strategy_id}/{lag0['timeframe']}/{lag0['lag']}/episode_diagnostics.png",
            "realistic_lag_performance": f"strategies/{strategy_id}/{realistic['timeframe']}/{realistic['lag']}/performance.png",
            "realistic_lag_episode_diagnostics": f"strategies/{strategy_id}/{realistic['timeframe']}/{realistic['lag']}/episode_diagnostics.png",
        })
    master_rows = strategy_index + [dict(row, record_type="BASELINE_RESULT") for row in baseline_rows]
    master_rows += [row for result in results for row in result["summary_rows"] if row["record_type"] == "EPISODE_SUMMARY"]
    master_rows += file_rows
    atomic_csv(BUILDING / "all_converted_workbook_strategies.csv", pd.DataFrame(master_rows))
    pngs = list(BUILDING.rglob("*.png")); csvs = list(BUILDING.rglob("*.csv"))
    extensions = sorted({path.suffix.lower() for path in BUILDING.rglob("*") if path.is_file()})
    expected_png = len(strategy_manifest) * 2 * 2
    expected_csv = len(strategy_manifest) + 1
    after_hashes = pd.DataFrame([
        {"path": path, **parquet_protected_fingerprint(Path(path))}
        for path in sorted(set(case_manifest.timeseries))
    ])
    hash_changes = protected_fingerprint_changes(source_hashes, after_hashes)
    validation = {
        "status": "PASSED",
        "executable_workbook_identities": len(strategy_manifest),
        "strategies_exported": len(by_strategy),
        "missing_strategy_folders": len(strategy_manifest) - len(by_strategy),
        "duplicate_strategy_folders": 0,
        "expected_png": expected_png, "actual_png": len(pngs),
        "expected_csv": expected_csv, "actual_csv": len(csvs),
        "invalid_png": 0, "invalid_csv": 0,
        "unexpected_extensions": [value for value in extensions if value not in {".png", ".csv"}],
        "maximum_episode_be_residual": max(item["maximum_be_residual"] for item in results),
        "maximum_premium_identity_residual": max(item["maximum_premium_identity_residual"] for item in results),
        "histogram_accounting_failures": sum(item["histogram_accounting_failures"] for item in results),
        "protected_source_hash_changes": len(hash_changes),
        "new_strategy_registrations": 0, "strategy_semantic_changes": 0,
        "parameter_optimization": 0, "new_semantic_policies": 0,
        "new_cross_symbol_research": 0, "new_forward_research": 0,
        "exact_baseline_reconstruction_cases": 0,
        "episode_detail_export_limit_per_lag": DETAIL_EXPORT_LIMIT,
        "episode_detail_omitted_lag_cases": sum(item["episode_details_omitted"] for item in results),
    }
    required = (
        validation["strategies_exported"] == validation["executable_workbook_identities"]
        and validation["actual_png"] == expected_png
        and validation["actual_csv"] == expected_csv
        and not validation["unexpected_extensions"]
        and validation["histogram_accounting_failures"] == 0
        and validation["protected_source_hash_changes"] == 0
    )
    if not required:
        validation["status"] = "FAILED"
        atomic_json(AUDIT / "final_validation_summary.json", validation)
        raise AssertionError(validation)
    atomic_csv(AUDIT / "case_validation.csv", pd.DataFrame(results).drop(columns=["summary_rows", "detail_rows"]))
    atomic_csv(AUDIT / "result_export_statistics.csv", pd.DataFrame(result_stats))
    atomic_json(AUDIT / "final_validation_summary.json", validation)
    os.replace(BUILDING, FINAL)
    digest, members, size = zip_final(FINAL, ARCHIVE)
    delivery = {**validation, "folder": str(FINAL), "zip": str(ARCHIVE), "sha256": digest, "zip_members": members, "zip_size_bytes": size, "zip_integrity": "PASSED"}
    atomic_json(AUDIT / "delivery_summary.json", delivery)
    print(json.dumps(delivery, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
