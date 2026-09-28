#!/usr/bin/env python3
"""
Post-process the completed 9-symbol Stage-A matrix with UTC daily turnover.

This script never runs a strategy.  It reads the existing compact review
timeseries, reconstructs daily cumulative-turnover increments, reconciles them
to the authoritative total raw turnover, and writes classification summaries.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.internal.build_stagea_9symbol_expanded_tick_review import add_selection  # noqa: E402
from scripts.internal.build_stagea_9symbol_expanded_tick_review import (  # noqa: E402
    load_preworkbook,
)
from scripts.internal.build_stagea_9symbol_expanded_tick_review import load_workbook  # noqa: E402


def atomic_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    frame.to_csv(temporary, index=False)
    os.replace(temporary, path)


def atomic_json(value: object, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, default=str) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def daily_turnover(path: Path, expected_total: float) -> tuple[dict[str, float | int], pd.DataFrame]:
    frame = pd.read_parquet(path, columns=["event_time_ns", "cumulative_turnover"])
    frame = frame.sort_values("event_time_ns").drop_duplicates("event_time_ns", keep="last")
    ts = pd.to_datetime(frame.event_time_ns, unit="ns", utc=True)
    cumulative = frame.cumulative_turnover.astype(float)
    midnight = ts.dt.hour.eq(0) & ts.dt.minute.eq(0) & ts.dt.second.eq(0)
    anchors = frame.loc[midnight, ["event_time_ns", "cumulative_turnover"]].copy()
    if anchors.empty:
        raise ValueError(f"no UTC midnight anchors in {path}")
    first_ts = pd.Timestamp(int(anchors.event_time_ns.iloc[0]), unit="ns", tz="UTC")
    last_ts = pd.Timestamp(int(frame.event_time_ns.iloc[-1]), unit="ns", tz="UTC")
    if first_ts.time().isoformat() != "00:00:00":
        raise ValueError(f"incomplete leading boundary in {path}: {first_ts}")
    # Each midnight cumulative value is the start of its UTC day.  The final
    # retained observation closes the final complete day.
    boundaries = np.r_[anchors.cumulative_turnover.to_numpy(float), float(cumulative.iloc[-1])]
    increments = np.diff(boundaries)
    dates = pd.to_datetime(anchors.event_time_ns, unit="ns", utc=True).dt.date.astype(str)
    series = pd.DataFrame({"date_utc": dates, "turnover_raw": increments})
    series["turnover_pct"] = series.turnover_raw * 100.0
    boundary_excluded = float(anchors.cumulative_turnover.iloc[0])
    reconciled = float(series.turnover_raw.sum() + boundary_excluded)
    mismatch = reconciled - float(expected_total)
    if abs(mismatch) > 1e-6:
        raise ValueError(f"daily turnover does not reconcile for {path}: {mismatch}")
    values = series.turnover_raw.to_numpy(float)
    active = values[values > 0]
    metrics: dict[str, float | int] = {
        "mean_daily_turnover_raw": float(values.mean()),
        "mean_daily_turnover_pct": float(values.mean() * 100.0),
        "median_daily_turnover_raw": float(np.median(values)),
        "median_daily_turnover_pct": float(np.median(values) * 100.0),
        "P90_daily_turnover_pct": float(np.quantile(values, 0.90) * 100.0),
        "P95_daily_turnover_pct": float(np.quantile(values, 0.95) * 100.0),
        "max_daily_turnover_pct": float(values.max() * 100.0),
        "active_day_mean_turnover_pct": float(active.mean() * 100.0) if len(active) else 0.0,
        "total_turnover_raw": float(expected_total),
        "total_turnover_pct": float(expected_total * 100.0),
        "complete_turnover_days": len(values),
        "excluded_partial_boundary_turnover_raw": boundary_excluded,
        "daily_turnover_reconciliation_mismatch": float(mismatch),
        "turnover_window_start": first_ts.date().isoformat(),
        "turnover_window_end": last_ts.date().isoformat(),
        "turnover_window_end_exclusive": (last_ts.normalize() + pd.Timedelta(days=1)).date().isoformat(),
        "effective_years": len(values) / 365.25,
    }
    return metrics, series


def classify(frame: pd.DataFrame) -> pd.DataFrame:
    result = add_selection(frame)
    result["positive_case"] = (
        (result.timeframe.eq("1m") & result.Sharpe.gt(1.5))
        | (result.timeframe.isin(["10m", "15m"])
           & result.Signed_BE_bps.gt(10.0) & result.Sharpe.gt(1.0))
    )
    result["negative_reverse_candidate"] = (
        (result.timeframe.eq("1m") & result.Return.lt(0) & result.Sharpe.lt(-1.5))
        | (result.timeframe.isin(["10m", "15m"])
           & result.Return.lt(0) & result.Sharpe.lt(-1.0)
           & result.Signed_BE_bps.lt(-10.0))
    )
    result["classification"] = np.select(
        [result.positive_case, result.negative_reverse_candidate, result.CASE_QUALIFIES],
        ["POSITIVE_SELECTED", "NEGATIVE_REVERSE_CANDIDATE", "ABSOLUTE_THRESHOLD_SELECTED"],
        default="NOT_SELECTED",
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=ROOT)
    parser.add_argument("--research-output", type=Path)
    parser.add_argument("--delivery-output", type=Path)
    args = parser.parse_args()
    repo = args.repo.resolve()
    research = (args.research_output or repo / "outputs/baseline_evaluation/multi_symbol_classification").resolve()
    delivery = (args.delivery_output or repo / "outputs/deliverables/multi_symbol_classification").resolve()

    workbook, workbook_paths, workbook_residuals = load_workbook(repo)
    preworkbook, preworkbook_paths, preworkbook_residuals = load_preworkbook(repo)
    master = pd.concat([workbook, preworkbook], ignore_index=True)
    paths = {**workbook_paths, **preworkbook_paths}
    physical = master.drop_duplicates(["semantic_group_id", "symbol", "timeframe"]).copy()
    metric_rows: list[dict[str, object]] = []
    daily_cache: dict[tuple[str, str, str], pd.DataFrame] = {}
    for row in physical.itertuples(index=False):
        key = (str(row.semantic_group_id), str(row.symbol), str(row.timeframe))
        metrics, series = daily_turnover(paths[key], float(row.Turnover_raw))
        metric_rows.append({"semantic_group_id": key[0], "symbol": key[1], "timeframe": key[2], **metrics})
        daily_cache[key] = series
    metrics = pd.DataFrame(metric_rows)
    master = master.merge(metrics, on=["semantic_group_id", "symbol", "timeframe"], validate="many_to_one")
    master = classify(master)
    if len(master) != 331 * 9 * 3 or master.strategy_id.nunique() != 331:
        raise ValueError(f"classification population changed: rows={len(master)} strategies={master.strategy_id.nunique()}")
    if master.daily_turnover_reconciliation_mismatch.abs().max() > 1e-6:
        raise ValueError("daily turnover reconciliation failed")
    master["requested_start"] = "2021-07-01"
    master["effective_start"] = master.turnover_window_start
    master["end"] = master.turnover_window_end_exclusive
    master["daily_observations"] = master.daily_observation_count.astype(int)
    master["Signed_BE"] = master.Signed_BE_bps
    master["MaxDD"] = master.Max_Drawdown
    master["Avg_Daily_Turnover_pct"] = master.mean_daily_turnover_pct
    master["Median_Daily_Turnover_pct"] = master.median_daily_turnover_pct
    master["P90_Daily_Turnover_pct"] = master.P90_daily_turnover_pct
    master["P95_Daily_Turnover_pct"] = master.P95_daily_turnover_pct
    master["Max_Daily_Turnover_pct"] = master.max_daily_turnover_pct
    master["Total_Turnover_raw"] = master.total_turnover_raw
    master["Total_Turnover_pct"] = master.total_turnover_pct
    master["selected"] = master.CASE_QUALIFIES.astype(bool)
    master["reverse_candidate"] = master.negative_reverse_candidate.astype(bool)

    if research.exists():
        shutil.rmtree(research)
    if delivery.exists():
        shutil.rmtree(delivery)
    research.mkdir(parents=True)
    delivery.mkdir(parents=True)

    detail_columns = [
        "strategy_id", "semantic_group_id", "source_origin", "representative_strategy_id",
        "symbol", "timeframe", "requested_start", "effective_start", "end",
        "daily_observations", "effective_years", "Return", "Sharpe", "Signed_BE", "MaxDD",
        "Avg_Daily_Turnover_pct", "Median_Daily_Turnover_pct",
        "P90_Daily_Turnover_pct", "P95_Daily_Turnover_pct", "Max_Daily_Turnover_pct",
        "Total_Turnover_raw", "Total_Turnover_pct", "Persistent", "classification",
        "selected", "reverse_candidate", "positive_case", "negative_reverse_candidate",
        "total_turnover_raw", "total_turnover_pct", "mean_daily_turnover_raw",
        "mean_daily_turnover_pct", "median_daily_turnover_raw", "median_daily_turnover_pct",
        "P90_daily_turnover_pct", "P95_daily_turnover_pct", "max_daily_turnover_pct",
        "active_day_mean_turnover_pct", "complete_turnover_days",
        "excluded_partial_boundary_turnover_raw", "daily_turnover_reconciliation_mismatch",
        "turnover_window_start", "turnover_window_end",
    ]
    atomic_csv(master[detail_columns], research / "multi_symbol_classification_master.csv")

    symbol_summary = master.groupby("symbol", as_index=False).agg(
        effective_start=("effective_start", "min"), end=("end", "max"),
        min_daily_observations=("daily_observations", "min"),
        max_effective_years=("effective_years", "max"),
        eligible_strategies=("strategy_id", "nunique"), logical_cases=("strategy_id", "size"),
        selected_cases=("selected", "sum"),
        reverse_candidate_cases=("reverse_candidate", "sum"),
        median_Return=("Return", "median"), median_Sharpe=("Sharpe", "median"),
        median_Signed_BE=("Signed_BE", "median"),
        median_Avg_Daily_Turnover_pct=("Avg_Daily_Turnover_pct", "median"),
        median_MaxDD=("MaxDD", "median"),
    )
    selected_counts = (
        master.loc[master.selected].groupby("symbol").strategy_id.nunique()
        .rename("selected_strategy_ids")
    )
    symbol_summary = symbol_summary.merge(selected_counts, on="symbol", how="left")
    symbol_summary["selected_strategy_ids"] = symbol_summary.selected_strategy_ids.fillna(0).astype(int)
    atomic_csv(symbol_summary, research / "symbol_summary.csv")

    cross = master.groupby(["strategy_id", "semantic_group_id", "source_origin", "timeframe"], as_index=False).agg(
        symbols_tested=("symbol", "nunique"), positive_symbol_count=("positive_case", "sum"),
        negative_reverse_symbol_count=("negative_reverse_candidate", "sum"),
        median_Return=("Return", "median"), median_Sharpe=("Sharpe", "median"),
        median_Signed_BE_bps=("Signed_BE_bps", "median"),
        mean_Avg_Daily_Turnover_pct=("mean_daily_turnover_pct", "mean"),
        median_Avg_Daily_Turnover_pct=("mean_daily_turnover_pct", "median"),
        median_Total_Turnover_pct=("total_turnover_pct", "median"),
    )
    atomic_csv(cross, research / "cross_symbol_strategy_summary.csv")

    repeatability = master.groupby(
        ["strategy_id", "semantic_group_id", "source_origin", "timeframe"], as_index=False,
    ).agg(
        available_symbols=("symbol", "nunique"), selected_symbols=("selected", "sum"),
        positive_Return_symbols=("Return", lambda value: int(value.gt(0).sum())),
        positive_Sharpe_symbols=("Sharpe", lambda value: int(value.gt(0).sum())),
        positive_BE_symbols=("Signed_BE", lambda value: int(value.gt(0).sum())),
        reverse_candidate_symbols=("reverse_candidate", "sum"),
        median_Avg_Daily_Turnover_pct=("Avg_Daily_Turnover_pct", "median"),
    )
    atomic_csv(repeatability, research / "multi_symbol_repeatability.csv")

    positives = master[master.positive_case].sort_values(
        ["Sharpe", "Signed_BE_bps", "mean_daily_turnover_pct"], ascending=[False, False, True]
    ).copy()
    negatives = master[master.negative_reverse_candidate].sort_values(
        ["Sharpe", "Signed_BE_bps", "mean_daily_turnover_pct"], ascending=[True, True, True]
    ).copy()
    boss_columns = [
        "strategy_id", "source_origin", "semantic_group_id", "symbol", "timeframe",
        "Return", "Sharpe", "Signed_BE_bps", "Max_Drawdown",
        "mean_daily_turnover_pct", "median_daily_turnover_pct", "P95_daily_turnover_pct",
        "total_turnover_raw", "total_turnover_pct", "complete_turnover_days",
    ]
    atomic_csv(positives[boss_columns], research / "top_positive_cases.csv")
    atomic_csv(negatives[boss_columns], research / "strongest_negative_reverse_candidates.csv")

    selected_keys: list[tuple[str, str, str]] = []
    for row in pd.concat([positives.head(20), negatives.head(20)]).itertuples(index=False):
        selected_keys.append((str(row.semantic_group_id), str(row.symbol), str(row.timeframe)))
    for key in dict.fromkeys(selected_keys):
        safe_group = hashlib.sha256(key[0].encode()).hexdigest()[:16]
        path = research / "daily_turnover" / f"group={safe_group}" / f"symbol={key[1]}" / f"timeframe={key[2]}" / "daily_turnover.csv"
        atomic_csv(daily_cache[key], path)

    key_results = pd.DataFrame([
        {"metric": "symbols_audited", "value": 9, "denominator": "frozen symbol universe"},
        {"metric": "strategy_ids_audited", "value": int(master.strategy_id.nunique()), "denominator": "WORKBOOK + PRE_WORKBOOK IDs"},
        {"metric": "logical_cases_audited", "value": len(master), "denominator": "strategy x symbol x timeframe"},
        {"metric": "positive_cases", "value": int(master.positive_case.sum()), "denominator": "logical cases"},
        {"metric": "negative_reverse_candidates", "value": int(master.negative_reverse_candidate.sum()), "denominator": "logical cases"},
        {"metric": "primary_turnover_metric", "value": "Avg Daily Turnover (%)", "denominator": "complete UTC days incl. zero-turnover"},
        {"metric": "BE_denominator", "value": "TOTAL RAW TURNOVER", "denominator": "unchanged"},
    ])
    atomic_csv(key_results, research / "key_results.csv")
    validation = {
        "status": "PASSED", "backtests_rerun": 0,
        "symbols": sorted(master.symbol.unique().tolist()),
        "strategy_ids": int(master.strategy_id.nunique()), "logical_cases": len(master),
        "max_daily_turnover_reconciliation_mismatch": float(master.daily_turnover_reconciliation_mismatch.abs().max()),
        "BE_denominator": "total_turnover_raw", "funding_label": "Funding Included",
        "workbook_source_residuals": workbook_residuals,
        "preworkbook_source_residuals": preworkbook_residuals,
    }
    atomic_json(validation, research / "validation_summary.json")

    for name in [
        "multi_symbol_classification_master.csv", "symbol_summary.csv",
        "cross_symbol_strategy_summary.csv", "top_positive_cases.csv",
        "multi_symbol_repeatability.csv", "strongest_negative_reverse_candidates.csv",
        "key_results.csv", "validation_summary.json",
    ]:
        shutil.copy2(research / name, delivery / name)
    if (research / "daily_turnover").exists():
        shutil.copytree(research / "daily_turnover", delivery / "daily_turnover")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
