#!/usr/bin/env python3
"""Build the strict absolute-BE AND absolute-Sharpe filtered boss delivery."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path, PurePosixPath
from zipfile import ZIP_DEFLATED, ZipFile

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.internal.build_10m15m_tick_be_sharpe_review import (
    SYMBOLS,
    atomic_csv,
    atomic_json,
    case_path,
    daily_sharpe,
    load_and_validate_series,
    performance_figure,
    sha256,
)

REFERENCE_TIMEFRAMES = ("1m", "10m", "15m")
SELECTION_TIMEFRAMES = ("10m", "15m")


def qualifies(frame: pd.DataFrame) -> pd.Series:
    return frame["Signed_BE_bps"].abs().gt(10) & frame["Sharpe"].abs().gt(1)


def heatmap(path: Path, strategy: str, timeframe: str, frame: pd.DataFrame) -> None:
    frame = frame.set_index("symbol").reindex(SYMBOLS)
    values = np.vstack([
        frame.Return.to_numpy(float) * 100,
        frame.Signed_BE_bps.to_numpy(float),
        frame.Sharpe.to_numpy(float),
        frame.Max_Drawdown.to_numpy(float) * 100,
        frame.Persistent.astype(float).to_numpy(),
    ])
    labels = ["Return (1x, %)", "Signed BE (bps)", "Sharpe", "Max DD (%)", "Persistent"]
    image = np.zeros_like(values)
    for index, row in enumerate(values):
        finite = row[np.isfinite(row)]
        if index == 4:
            image[index] = row
        elif len(finite):
            scale = max(abs(np.quantile(finite, 0.05)), abs(np.quantile(finite, 0.95)), 1e-12)
            image[index] = np.clip(row / scale, -1, 1)
    fig, ax = plt.subplots(figsize=(14, 4.6), constrained_layout=True)
    ax.imshow(image, cmap="RdYlGn", vmin=-1, vmax=1, aspect="auto")
    ax.set_xticks(range(len(SYMBOLS)), SYMBOLS, rotation=30, ha="right")
    ax.set_yticks(range(5), labels)
    for row_index in range(5):
        for symbol_index in range(len(SYMBOLS)):
            value = values[row_index, symbol_index]
            combined = (
                np.isfinite(values[1, symbol_index])
                and np.isfinite(values[2, symbol_index])
                and abs(values[1, symbol_index]) > 10
                and abs(values[2, symbol_index]) > 1
            )
            text = "NaN" if not np.isfinite(value) else (f"{int(value)}" if row_index == 4 else f"{value:.2f}")
            if row_index == 2 and combined:
                text += " Q"
            ax.text(symbol_index, row_index, text, ha="center", va="center", fontsize=8, color="black")
            if row_index == 1 and np.isfinite(value) and abs(value) > 10:
                ax.add_patch(Rectangle((symbol_index - 0.47, row_index - 0.47), 0.94, 0.94, fill=False, edgecolor="black", linewidth=1.5))
            if row_index == 2 and np.isfinite(value) and abs(value) > 1:
                ax.add_patch(Rectangle((symbol_index - 0.43, row_index - 0.43), 0.86, 0.86, fill=False, edgecolor="navy", linewidth=1.5))
            if combined and row_index in {1, 2}:
                ax.add_patch(Rectangle((symbol_index - 0.49, row_index - 0.49), 0.98, 0.98, fill=False, edgecolor="#7e22ce", linewidth=3))
    ax.set_title(f"{strategy} | {timeframe} | 9-symbol frozen review\nbar signal → raw tick execution", fontsize=13)
    ax.set_xticks(np.arange(-0.5, 9, 1), minor=True)
    ax.set_yticks(np.arange(-0.5, 5, 1), minor=True)
    ax.grid(which="minor", color="white", linewidth=1)
    ax.tick_params(which="minor", bottom=False, left=False)
    fig.text(0.99, 0.01, "Q = |BE| > 10 bps AND |Sharpe| > 1.0", ha="right", fontsize=8)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=180)
    plt.close(fig)


def finite_extreme(series: pd.Series, mode: str) -> float:
    finite = pd.to_numeric(series, errors="coerce").dropna()
    if finite.empty:
        return float("nan")
    return float(finite.max() if mode == "max" else finite.min())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    repo = args.repo.resolve()
    output = args.output.resolve()
    source = repo / "outputs/deliverables/boss_multitimeframe_final_delivery/02_full_results"
    result_root = repo / "outputs/baseline_evaluation/boss_multitimeframe_tick_screen"
    prior_root = repo / "outputs/deliverables/10m15m_tick_be_sharpe_review"

    prior = pd.read_csv(prior_root / "all_10m15m_results.csv")
    prior["Signed_BE_bps"] = pd.to_numeric(prior.Signed_BE_bps, errors="coerce")
    prior["Sharpe"] = pd.to_numeric(prior.Sharpe, errors="coerce")
    prior_qualifying = prior[qualifies(prior)].copy()
    qualifying_strategy_ids = set(prior_qualifying.strategy_id.astype(str))
    qualifying_hashes = set(prior_qualifying.semantic_execution_hash.astype(str))
    if len(prior) != 4806 or len(qualifying_strategy_ids) == 0:
        raise ValueError("frozen 10m/15m population or strict selection failed")

    master = pd.read_csv(source / "boss_multitimeframe_tick_master.csv")
    master = master[
        master.strategy_id.astype(str).isin(qualifying_strategy_ids)
        & master.timeframe.isin(REFERENCE_TIMEFRAMES)
    ].copy()
    persistence = pd.read_csv(source / "persistent_position_metrics_v2.csv")
    persistence = persistence[persistence.timeframe.isin(REFERENCE_TIMEFRAMES)][[
        "strategy_id", "symbol", "timeframe", "directionally_persistent", "nonflat_fraction_v2",
        "long_fraction_v2", "short_fraction_v2", "flat_fraction_v2", "median_directional_run_hours",
        "P90_directional_run_hours", "sign_switches_per_day",
    ]]
    master = master.merge(persistence, on=["strategy_id", "symbol", "timeframe"], how="left", validate="one_to_one")
    if len(master) != len(qualifying_strategy_ids) * len(SYMBOLS) * len(REFERENCE_TIMEFRAMES):
        raise ValueError("retained strategy 1m/10m/15m coverage is incomplete")

    prior_sharpe = prior[["semantic_execution_hash", "symbol", "timeframe", "Sharpe"]].drop_duplicates()
    one_minute_rows = []
    one_minute_physical = master[master.timeframe.eq("1m")].drop_duplicates([
        "semantic_execution_hash", "symbol", "timeframe"
    ])
    max_residuals = {"Return": 0.0, "Turnover": 0.0, "MDD": 0.0}
    for row in one_minute_physical.itertuples(index=False):
        path = case_path(result_root, row.semantic_execution_hash, row.symbol, "1m")
        if not path.is_file():
            raise RuntimeError(f"1M_SUMMARY_TIMESERIES_MISSING: {path}")
        _, sharpe, days, residuals = load_and_validate_series(path, pd.Series(row._asdict()))
        for key, value in residuals.items():
            max_residuals[key] = max(max_residuals[key], value)
        one_minute_rows.append({
            "semantic_execution_hash": row.semantic_execution_hash,
            "symbol": row.symbol,
            "timeframe": "1m",
            "Sharpe": sharpe,
            "daily_observation_count": days,
        })
    sharpe_map = pd.concat([prior_sharpe, pd.DataFrame(one_minute_rows)], ignore_index=True)
    sharpe_map = sharpe_map.drop_duplicates(["semantic_execution_hash", "symbol", "timeframe"])
    master = master.merge(sharpe_map, on=["semantic_execution_hash", "symbol", "timeframe"], how="left", validate="many_to_one")
    master["Return"] = master.Return_fee0.astype(float)
    master["Signed_BE_bps"] = master.Signed_BE_bps.fillna(master.BE_bps)
    master["abs_BE_bps"] = master.Signed_BE_bps.abs()
    master["Max_Drawdown"] = master.MDD.astype(float)
    master["Turnover_pct"] = master.Turnover_raw.astype(float) * 100
    master["Persistent"] = master.directionally_persistent.astype(bool)
    master["Nonflat_fraction"] = master.nonflat_fraction_v2
    master["Long_fraction"] = master.long_fraction_v2
    master["Short_fraction"] = master.short_fraction_v2
    master["Flat_fraction"] = master.flat_fraction_v2
    master["Median_directional_run_hours"] = master.median_directional_run_hours
    master["P90_directional_run_hours"] = master.P90_directional_run_hours
    master["Switches_per_day"] = master.sign_switches_per_day
    master["semantic_group_id"] = master.semantic_execution_hash
    master["BE_PASS"] = master.Signed_BE_bps.abs().gt(10)
    master["SHARPE_PASS"] = master.Sharpe.abs().gt(1)
    master["COMBINED_PASS"] = master.BE_PASS & master.SHARPE_PASS

    qualifying = master[master.timeframe.isin(SELECTION_TIMEFRAMES) & master.COMBINED_PASS].copy()
    if len(qualifying) != len(prior_qualifying):
        raise ValueError("strict qualifying population changed during build")
    if set(qualifying.strategy_id.astype(str)) != qualifying_strategy_ids:
        raise ValueError("strict qualifying strategy IDs changed during build")

    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)
    timeframe_order = pd.CategoricalDtype(REFERENCE_TIMEFRAMES, ordered=True)
    symbol_order = pd.CategoricalDtype(SYMBOLS, ordered=True)
    summary_rows = []
    index_rows = []
    qualifying["performance_figure_path"] = [
        str(PurePosixPath("strategies") / row.strategy_id / "performance" / row.timeframe / f"{row.symbol}__performance.png")
        for row in qualifying.itertuples()
    ]
    all_members = pd.read_csv(source / "boss_multitimeframe_tick_master.csv").groupby("semantic_execution_hash").strategy_id.unique().to_dict()
    summary_columns = [
        "strategy_id", "semantic_group_id", "symbol", "timeframe", "Return", "Sharpe", "Signed_BE_bps",
        "abs_BE_bps", "Max_Drawdown", "Turnover_raw", "Turnover_pct", "Persistent", "Nonflat_fraction",
        "Long_fraction", "Short_fraction", "Flat_fraction", "Median_directional_run_hours",
        "P90_directional_run_hours", "Switches_per_day", "BE_PASS", "SHARPE_PASS", "COMBINED_PASS",
    ]
    for strategy in sorted(qualifying_strategy_ids):
        group = master[master.strategy_id.astype(str).eq(strategy)].copy()
        group["timeframe"] = group.timeframe.astype(timeframe_order)
        group["symbol"] = group.symbol.astype(symbol_order)
        group = group.sort_values(["timeframe", "symbol"])
        folder = output / "strategies" / strategy
        atomic_csv(folder / "summary.csv", group[summary_columns])
        for timeframe in REFERENCE_TIMEFRAMES:
            heatmap(folder / f"summary_{timeframe}.png", strategy, timeframe, group[group.timeframe.eq(timeframe)])
        q = qualifying[qualifying.strategy_id.astype(str).eq(strategy)].copy()
        semantic = str(group.semantic_execution_hash.iloc[0])
        positive_be = q.loc[q.Signed_BE_bps.gt(10), "Signed_BE_bps"]
        negative_be = q.loc[q.Signed_BE_bps.lt(-10), "Signed_BE_bps"]
        positive_sharpe = q.loc[q.Sharpe.gt(1), "Sharpe"]
        negative_sharpe = q.loc[q.Sharpe.lt(-1), "Sharpe"]
        qualifying_symbols = ";".join(symbol for symbol in SYMBOLS if symbol in set(q.symbol.astype(str)))
        row = {
            "strategy_id": strategy,
            "semantic_group_id": semantic,
            "equivalent_source_ids": ";".join(sorted(map(str, all_members[semantic]))),
            "qualifying_case_count": len(q),
            "qualifying_10m_count": int(q.timeframe.eq("10m").sum()),
            "qualifying_15m_count": int(q.timeframe.eq("15m").sum()),
            "qualifying_symbols": qualifying_symbols,
            "max_abs_BE": float(q.Signed_BE_bps.abs().max()),
            "max_abs_Sharpe": float(q.Sharpe.abs().max()),
            "max_positive_BE": finite_extreme(positive_be, "max"),
            "min_negative_BE": finite_extreme(negative_be, "min"),
            "max_positive_Sharpe": finite_extreme(positive_sharpe, "max"),
            "min_negative_Sharpe": finite_extreme(negative_sharpe, "min"),
            "summary_1m": str(PurePosixPath("strategies") / strategy / "summary_1m.png"),
            "summary_10m": str(PurePosixPath("strategies") / strategy / "summary_10m.png"),
            "summary_15m": str(PurePosixPath("strategies") / strategy / "summary_15m.png"),
            "strategy_folder": str(PurePosixPath("strategies") / strategy),
        }
        summary_rows.append(row)
        index_rows.append({
            "strategy_id": strategy,
            "qualifying_case_count": len(q),
            "summary_1m": row["summary_1m"],
            "summary_10m": row["summary_10m"],
            "summary_15m": row["summary_15m"],
            "detailed_performance_count": len(q),
            "folder": row["strategy_folder"],
        })

    qualifying_columns = [
        "strategy_id", "semantic_group_id", "symbol", "timeframe", "Return", "Sharpe", "Signed_BE_bps",
        "abs_BE_bps", "Max_Drawdown", "Turnover_pct", "Persistent", "performance_figure_path",
    ]
    atomic_csv(output / "qualifying_cases.csv", qualifying[qualifying_columns])
    atomic_csv(output / "positive_quality_cases.csv", qualifying[
        qualifying.Signed_BE_bps.gt(10) & qualifying.Sharpe.gt(1)
    ][qualifying_columns])
    atomic_csv(output / "qualifying_strategies.csv", pd.DataFrame(summary_rows))
    atomic_csv(output / "strategy_index.csv", pd.DataFrame(index_rows))

    comparison_metrics = ["Return", "Sharpe", "Signed_BE_bps", "Max_Drawdown"]
    comparison = master.pivot(index=["strategy_id", "symbol"], columns="timeframe", values=comparison_metrics)
    comparison.columns = [f"{metric}_{timeframe}" for metric, timeframe in comparison.columns]
    comparison = comparison.reset_index()
    atomic_csv(output / "timeframe_comparison.csv", comparison)

    for row in qualifying.itertuples(index=False):
        source_path = case_path(result_root, row.semantic_execution_hash, row.symbol, row.timeframe)
        series = pd.read_parquet(source_path)
        performance_figure(output / Path(row.performance_figure_path), row.strategy_id, pd.Series(row._asdict()), series)

    validation = {
        "status": "PASSED",
        "selection_rule": "abs(Signed_BE_bps) > 10 AND abs(Sharpe) > 1.0",
        "selection_timeframes": list(SELECTION_TIMEFRAMES),
        "reference_timeframe": "1m",
        "audited_logical_10m15m_cases": len(prior),
        "qualifying_logical_cases": len(qualifying),
        "qualifying_10m_logical_cases": int(qualifying.timeframe.eq("10m").sum()),
        "qualifying_15m_logical_cases": int(qualifying.timeframe.eq("15m").sum()),
        "qualifying_source_strategy_ids": len(qualifying_strategy_ids),
        "qualifying_independent_semantic_groups": len(qualifying_hashes),
        "qualifying_physical_cases": len(qualifying.drop_duplicates(["semantic_execution_hash", "symbol", "timeframe"])),
        "positive_quality_logical_cases": int((qualifying.Signed_BE_bps.gt(10) & qualifying.Sharpe.gt(1)).sum()),
        "summary_figure_count": len(qualifying_strategy_ids) * 3,
        "summary_1m_count": len(qualifying_strategy_ids),
        "summary_10m_count": len(qualifying_strategy_ids),
        "summary_15m_count": len(qualifying_strategy_ids),
        "detailed_performance_figure_count": len(qualifying),
        "nonqualifying_detailed_figure_count": 0,
        "max_1m_aggregate_residuals": max_residuals,
        "five_bp_columns_in_new_csv": 0,
        "full_matrix_backtests_rerun": 0,
        "tick_index_rebuilt": 0,
        "parameter_optimization_runs": 0,
        "strategy_semantic_changes": 0,
        "selective_1m_review_timeseries_rematerialized": len(one_minute_physical),
    }
    atomic_json(output / "validation_summary.json", validation)
    zip_path = output.with_suffix(".zip")
    if zip_path.exists():
        zip_path.unlink()
    with ZipFile(zip_path, "w", ZIP_DEFLATED, allowZip64=True) as archive:
        for file in sorted(output.rglob("*")):
            if file.is_file():
                archive.write(file, file.relative_to(output.parent))
    with ZipFile(zip_path) as archive:
        if archive.testzip() is not None:
            raise RuntimeError("ZIP integrity failed")
    validation["zip_path"] = str(zip_path)
    validation["zip_sha256"] = sha256(zip_path)
    atomic_json(output / "validation_summary.json", validation)
    print(json.dumps(validation, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
