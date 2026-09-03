#!/usr/bin/env python3
"""Build the frozen 10m/15m tick-execution BE/Sharpe boss review."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import sys
from pathlib import Path, PurePosixPath
from zipfile import ZIP_DEFLATED, ZipFile

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
SYMBOLS = ["XRPUSDT", "DOGEUSDT", "SUIUSDT", "BNBUSDT", "ETHUSDT", "BTCUSDT", "1000PEPEUSDT", "SOLUSDT", "ADAUSDT"]
TIMEFRAMES = ["10m", "15m"]
TOLERANCE = {"Return": 1e-10, "Turnover": 1e-6, "MDD": 1e-10}


def atomic_csv(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    frame.to_csv(tmp, index=False)
    os.replace(tmp, path)


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, default=str, allow_nan=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def case_path(root: Path, semantic: str, symbol: str, timeframe: str) -> Path:
    return root / "matrix_cases" / f"symbol={symbol}" / f"timeframe={timeframe}" / f"semantic={semantic}" / "review_timeseries.parquet"


def daily_sharpe(frame: pd.DataFrame) -> tuple[float, int]:
    ts = pd.to_datetime(frame.event_time_ns, unit="ns", utc=True)
    cumulative = frame.cumulative_return_with_premium.astype(float)
    # Review files retain exact UTC 00:00 anchors plus the final observation
    # and all position transitions.  Consecutive anchor differences recover
    # complete UTC-day arithmetic increments without treating the final
    # intraday transition as the end of day.
    anchor_mask = ts.dt.hour.eq(0) & ts.dt.minute.eq(0) & ts.dt.second.eq(0)
    anchors = cumulative.loc[anchor_mask].to_numpy(float)
    endpoint = float(cumulative.iloc[-1])
    values = np.r_[anchors, endpoint]
    daily = np.diff(values)
    daily = daily[np.isfinite(daily)]
    if len(daily) < 2:
        return float("nan"), len(daily)
    std = float(daily.std(ddof=1))
    if not np.isfinite(std) or std == 0.0:
        return float("nan"), len(daily)
    return float(daily.mean() / std * math.sqrt(365.0)), len(daily)


def load_and_validate_series(path: Path, expected: pd.Series) -> tuple[pd.DataFrame, float, int, dict[str, float]]:
    frame = pd.read_parquet(path)
    required = {"event_time_ns", "cumulative_return_with_premium", "cumulative_turnover", "executed_position", "drawdown"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"{path}: missing {sorted(missing)}")
    residuals = {
        "Return": abs(float(frame.cumulative_return_with_premium.iloc[-1]) - float(expected.Return_fee0)),
        "Turnover": abs(float(frame.cumulative_turnover.iloc[-1]) - float(expected.Turnover_raw)),
        "MDD": abs(float(frame.drawdown.min()) - float(expected.MDD)),
    }
    if any(residuals[key] > TOLERANCE[key] for key in residuals):
        raise ValueError(f"{path}: aggregate residual {residuals}")
    sharpe, days = daily_sharpe(frame)
    return frame, sharpe, days, residuals


def selection_class(be: bool, sharpe: bool, previous: bool) -> str:
    if previous:
        return "PREVIOUS_PLUS_BE_AND_SHARPE" if be and sharpe else "PREVIOUS_PLUS_BE" if be else "PREVIOUS_PLUS_SHARPE" if sharpe else "PREVIOUS_SELECTED_ONLY"
    return "BE_AND_SHARPE_SELECTED" if be and sharpe else "BE_SELECTED_ONLY" if be else "SHARPE_SELECTED_ONLY"


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
    for i, row in enumerate(values):
        finite = row[np.isfinite(row)]
        if i == 4:
            image[i] = row
        elif len(finite):
            scale = max(abs(np.quantile(finite, .05)), abs(np.quantile(finite, .95)), 1e-12)
            image[i] = np.clip(row / scale, -1, 1)
    fig, ax = plt.subplots(figsize=(14, 4.6), constrained_layout=True)
    ax.imshow(image, cmap="RdYlGn", vmin=-1, vmax=1, aspect="auto")
    ax.set_xticks(range(len(SYMBOLS)), SYMBOLS, rotation=30, ha="right")
    ax.set_yticks(range(5), labels)
    for i in range(5):
        for j in range(len(SYMBOLS)):
            value = values[i, j]
            text = "NaN" if not np.isfinite(value) else (f"{int(value)}" if i == 4 else f"{value:.2f}")
            if i == 2 and np.isfinite(value):
                text += "**" if value >= 1.5 else "*" if value >= 1 else ""
            ax.text(j, i, text, ha="center", va="center", fontsize=8, color="black")
            if i == 1 and np.isfinite(value) and abs(value) >= 10:
                ax.add_patch(Rectangle((j-.48, i-.48), .96, .96, fill=False, edgecolor="black", linewidth=2))
            if i == 2 and np.isfinite(value) and value >= 1:
                ax.add_patch(Rectangle((j-.44, i-.44), .88, .88, fill=False, edgecolor="navy", linewidth=2 if value >= 1.5 else 1))
    ax.set_title(f"{strategy} | {timeframe} | 9-symbol frozen review\nbar signal → raw tick execution", fontsize=13)
    ax.set_xticks(np.arange(-.5, 9, 1), minor=True)
    ax.set_yticks(np.arange(-.5, 5, 1), minor=True)
    ax.grid(which="minor", color="white", linewidth=1)
    ax.tick_params(which="minor", bottom=False, left=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=180)
    plt.close(fig)


def performance_figure(path: Path, strategy: str, row: pd.Series, series: pd.DataFrame) -> None:
    ts = pd.to_datetime(series.event_time_ns, unit="ns", utc=True)
    ret = series.cumulative_return_with_premium.astype(float)
    turnover = series.cumulative_turnover.astype(float) * 100
    position = np.sign(series.executed_position.astype(float).to_numpy())
    dd = series.drawdown.astype(float)
    fig, axes = plt.subplots(3, 1, figsize=(14, 9), sharex=True, gridspec_kw={"height_ratios": [2.1, 1, 1.25]}, constrained_layout=True)
    ax = axes[0]
    ax.plot(ts, ret, color="#1f77b4", linewidth=1.1, label="Cumulative Return (1x)")
    ax.set_ylabel("Cumulative Return (1x)")
    ax.grid(alpha=.2)
    right = ax.twinx()
    right.plot(ts, turnover, color="#d97706", linewidth=.8, alpha=.75, label="Cumulative Turnover")
    right.set_ylabel("Cumulative Turnover (%)")
    ax.legend(loc="upper left", fontsize=8); right.legend(loc="upper right", fontsize=8)
    middle = axes[1]
    colors = np.where(position > 0, "#2ca02c", np.where(position < 0, "#d62728", "#9ca3af"))
    middle.scatter(ts, position, c=colors, s=1.0, marker="s", linewidths=0, rasterized=True)
    middle.set_yticks([-1, 0, 1], ["SHORT", "FLAT", "LONG"])
    middle.set_ylim(-1.45, 1.45); middle.grid(axis="y", alpha=.2)
    annotation = (
        f"Long {row.Long_fraction:.1%} | Short {row.Short_fraction:.1%} | Flat {row.Flat_fraction:.1%} | Nonflat {row.Nonflat_fraction:.1%}\n"
        f"Median run {row.Median_directional_run_hours:.1f}h | P90 run {row.P90_directional_run_hours:.1f}h | Switches/day {row.Switches_per_day:.3f}"
    )
    middle.text(.01, .96, annotation, transform=middle.transAxes, va="top", fontsize=8, bbox={"facecolor": "white", "alpha": .8, "edgecolor": "none"})
    bottom = axes[2]
    bottom.fill_between(ts, dd, 0, color="#c2410c", alpha=.3)
    bottom.plot(ts, dd, color="#c2410c", linewidth=.8)
    trough = int(np.nanargmin(dd.to_numpy()))
    bottom.scatter([ts.iloc[trough]], [dd.iloc[trough]], color="black", s=32, zorder=5)
    bottom.annotate(f"Max DD {dd.iloc[trough]:.2%}\n{ts.iloc[trough]:%Y-%m-%d}", (ts.iloc[trough], dd.iloc[trough]), xytext=(8, 10), textcoords="offset points", fontsize=8)
    bottom.set_ylabel("Drawdown"); bottom.grid(alpha=.2)
    bottom.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=5, maxticks=9)); bottom.xaxis.set_major_formatter(mdates.ConciseDateFormatter(bottom.xaxis.get_major_locator()))
    fig.suptitle(
        f"{strategy} | {row.symbol} | {row.timeframe} | raw tick execution\n"
        f"Return={row.Return:.2%} | Sharpe={row.Sharpe:.2f} | BE={row.Signed_BE_bps:.2f} bps | "
        f"MaxDD={row.Max_Drawdown:.2%} | Turnover={row.Turnover_pct:.2f}%",
        fontsize=12,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=160)
    plt.close(fig)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    repo = args.repo.resolve()
    source = repo / "outputs/deliverables/boss_multitimeframe_final_delivery/02_full_results"
    result_root = repo / "outputs/baseline_evaluation/boss_multitimeframe_tick_screen"
    output = args.output or repo / "outputs/deliverables/10m15m_tick_be_sharpe_review"
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)
    master = pd.read_csv(source / "boss_multitimeframe_tick_master.csv")
    master = master[master.timeframe.isin(TIMEFRAMES)].copy()
    if len(master) != 4806 or master.strategy_id.nunique() != 267 or master.symbol.nunique() != 9:
        raise ValueError("frozen 4,806-row scope failed")
    persistence = pd.read_csv(source / "persistent_position_metrics_v2.csv")
    persistence = persistence[persistence.timeframe.isin(TIMEFRAMES)][[
        "strategy_id", "symbol", "timeframe", "directionally_persistent", "nonflat_fraction_v2",
        "long_fraction_v2", "short_fraction_v2", "flat_fraction_v2", "median_directional_run_hours",
        "P90_directional_run_hours", "sign_switches_per_day",
    ]]
    master = master.merge(persistence, on=["strategy_id", "symbol", "timeframe"], how="left", validate="one_to_one")
    physical = master.drop_duplicates(["semantic_execution_hash", "symbol", "timeframe"]).copy()
    sharpe_rows = []
    max_residuals = {"Return": 0.0, "Turnover": 0.0, "MDD": 0.0}
    missing_series = []
    for row in physical.itertuples(index=False):
        path = case_path(result_root, row.semantic_execution_hash, row.symbol, row.timeframe)
        if not path.is_file():
            missing_series.append(str(path)); continue
        _, sharpe, days, residuals = load_and_validate_series(path, pd.Series(row._asdict()))
        for key, value in residuals.items():
            max_residuals[key] = max(max_residuals[key], value)
        sharpe_rows.append({"semantic_execution_hash": row.semantic_execution_hash, "symbol": row.symbol, "timeframe": row.timeframe, "Sharpe": sharpe, "daily_observation_count": days, "review_timeseries_path": str(path)})
    if missing_series:
        raise RuntimeError(f"TIMESERIES_REQUIRED_FOR_RENDER_MISSING: {len(missing_series)}")
    sharpes = pd.DataFrame(sharpe_rows)
    master = master.merge(sharpes, on=["semantic_execution_hash", "symbol", "timeframe"], how="left", validate="many_to_one")
    previous = pd.read_csv(source / "boss_10m15m_final_shortlist.csv")
    previous_keys = set(zip(previous.semantic_execution_hash, previous.timeframe))
    master["Return"] = master.Return_fee0.astype(float)
    master["Signed_BE_bps"] = master.Signed_BE_bps.fillna(master.BE_bps)
    master["abs_BE_bps"] = master.Signed_BE_bps.abs()
    master["Max_Drawdown"] = master.MDD.astype(float)
    master["Nonflat_fraction"] = master.nonflat_fraction_v2
    master["Long_fraction"] = master.long_fraction_v2
    master["Short_fraction"] = master.short_fraction_v2
    master["Flat_fraction"] = master.flat_fraction_v2
    master["Median_directional_run_hours"] = master.median_directional_run_hours
    master["P90_directional_run_hours"] = master.P90_directional_run_hours
    master["Switches_per_day"] = master.sign_switches_per_day
    master["Persistent"] = master.directionally_persistent.astype(bool)
    master["BE10_CASE"] = master.abs_BE_bps.ge(10)
    master["GOOD_SHARPE_CASE"] = master.Sharpe.ge(1.0)
    master["STRONG_SHARPE_CASE"] = master.Sharpe.ge(1.5)
    master["PREVIOUS_SELECTED_CASE"] = [(h, tf) in previous_keys for h, tf in zip(master.semantic_execution_hash, master.timeframe)]
    master["BE10_OR_GOOD_SHARPE"] = master.BE10_CASE | master.GOOD_SHARPE_CASE
    physical = master.drop_duplicates(["semantic_execution_hash", "symbol", "timeframe"]).copy()
    triggered = physical.BE10_CASE | physical.GOOD_SHARPE_CASE | physical.PREVIOUS_SELECTED_CASE
    pair_keys = set(zip(physical.loc[triggered, "semantic_execution_hash"], physical.loc[triggered, "symbol"]))
    physical["DETAILED_SELECTED"] = [(h, s) in pair_keys for h, s in zip(physical.semantic_execution_hash, physical.symbol)]
    selected_hashes = set(physical.loc[physical.DETAILED_SELECTED, "semantic_execution_hash"])
    id_members = master.groupby("semantic_execution_hash").strategy_id.unique().to_dict()
    rep_map = master.groupby("semantic_execution_hash").representative_strategy_id.first().to_dict()
    physical["strategy_id"] = physical.semantic_execution_hash.map(rep_map)
    detail = physical[physical.DETAILED_SELECTED].copy()
    detail["performance_figure_path"] = [str(PurePosixPath("strategies") / r.strategy_id / "performance" / r.timeframe / f"{r.symbol}__performance.png") for r in detail.itertuples()]
    detail["detailed_figure_generated"] = True
    summary_rows = []
    index_rows = []
    for semantic in sorted(selected_hashes):
        strategy = rep_map[semantic]
        group = physical[physical.semantic_execution_hash.eq(semantic)].copy()
        be = bool(group.BE10_CASE.any()); sh = bool(group.GOOD_SHARPE_CASE.any()); prev = bool(group.PREVIOUS_SELECTED_CASE.any())
        folder = output / "strategies" / strategy
        detail_keys = set(zip(detail[detail.semantic_execution_hash.eq(semantic)].symbol, detail[detail.semantic_execution_hash.eq(semantic)].timeframe))
        group["detailed_figure_generated"] = [(s, t) in detail_keys for s, t in zip(group.symbol, group.timeframe)]
        group["equivalent_source_ids"] = ";".join(sorted(id_members[semantic]))
        group["semantic_group_id"] = semantic
        summary_cols = ["strategy_id", "semantic_group_id", "equivalent_source_ids", "semantic_execution_hash", "symbol", "timeframe", "Return", "Sharpe", "Signed_BE_bps", "abs_BE_bps", "Max_Drawdown", "Turnover_raw", "Turnover_pct", "Nonflat_fraction", "Long_fraction", "Short_fraction", "Flat_fraction", "Median_directional_run_hours", "P90_directional_run_hours", "Switches_per_day", "Persistent", "BE10_CASE", "GOOD_SHARPE_CASE", "STRONG_SHARPE_CASE", "PREVIOUS_SELECTED_CASE", "BE10_OR_GOOD_SHARPE", "detailed_figure_generated"]
        atomic_csv(folder / "summary.csv", group[summary_cols])
        for tf in TIMEFRAMES:
            heatmap(folder / f"summary_{tf}.png", strategy, tf, group[group.timeframe.eq(tf)])
        pos = group.loc[group.Signed_BE_bps.ge(10), "Signed_BE_bps"]
        neg = group.loc[group.Signed_BE_bps.le(-10), "Signed_BE_bps"]
        tf10 = group[group.timeframe.eq("10m")].Sharpe; tf15 = group[group.timeframe.eq("15m")].Sharpe
        row = {
            "strategy_id": strategy, "semantic_group_id": semantic, "equivalent_source_ids": ";".join(sorted(id_members[semantic])),
            "selected_by_BE": be, "selected_by_Sharpe": sh, "previous_selected": prev,
            "selection_class": selection_class(be, sh, prev), "BE10_case_count": int(group.BE10_CASE.sum()),
            "positive_BE10_case_count": int(group.Signed_BE_bps.ge(10).sum()), "negative_BE10_case_count": int(group.Signed_BE_bps.le(-10).sum()),
            "POSITIVE_BE10_EXISTS": bool(group.Signed_BE_bps.ge(10).any()),
            "NEGATIVE_BE10_EXISTS": bool(group.Signed_BE_bps.le(-10).any()),
            "BOTH_SIGNS_BE10": bool(group.Signed_BE_bps.ge(10).any() and group.Signed_BE_bps.le(-10).any()),
            "good_Sharpe_case_count": int(group.GOOD_SHARPE_CASE.sum()), "strong_Sharpe_case_count": int(group.STRONG_SHARPE_CASE.sum()),
            "max_abs_BE": group.abs_BE_bps.max(), "max_positive_BE": pos.max() if len(pos) else np.nan, "min_negative_BE": neg.min() if len(neg) else np.nan,
            "max_Sharpe": group.Sharpe.max(), "best_10m_Sharpe": tf10.max(), "best_15m_Sharpe": tf15.max(),
            "median_10m_Sharpe": tf10.median(), "median_15m_Sharpe": tf15.median(),
            "median_10m_MDD": group.loc[group.timeframe.eq("10m"), "Max_Drawdown"].median(), "median_15m_MDD": group.loc[group.timeframe.eq("15m"), "Max_Drawdown"].median(),
            "symbols_with_Sharpe_ge_1": ";".join(sorted(group.loc[group.GOOD_SHARPE_CASE, "symbol"].unique())),
            "symbols_with_Sharpe_ge_1_5": ";".join(sorted(group.loc[group.STRONG_SHARPE_CASE, "symbol"].unique())),
            "strategy_folder": str(PurePosixPath("strategies") / strategy),
        }
        summary_rows.append(row)
        dg = detail[detail.semantic_execution_hash.eq(semantic)]
        index_rows.append({**{k: row[k] for k in ["strategy_id", "selected_by_BE", "selected_by_Sharpe", "previous_selected", "max_abs_BE", "max_Sharpe"]}, "summary_10m": str(PurePosixPath("strategies") / strategy / "summary_10m.png"), "summary_15m": str(PurePosixPath("strategies") / strategy / "summary_15m.png"), "detailed_figure_count": len(dg), "10m_detailed_count": int(dg.timeframe.eq("10m").sum()), "15m_detailed_count": int(dg.timeframe.eq("15m").sum())})
    selected_strategies = pd.DataFrame(summary_rows).sort_values(["selection_class", "max_abs_BE"], ascending=[True, False])
    atomic_csv(output / "selected_strategies.csv", selected_strategies)
    atomic_csv(output / "strategy_index.csv", pd.DataFrame(index_rows))
    common_cols = ["strategy_id", "semantic_execution_hash", "symbol", "timeframe", "Return", "Sharpe", "Signed_BE_bps", "Max_Drawdown", "Turnover_raw", "Turnover_pct", "Persistent", "Nonflat_fraction", "Long_fraction", "Short_fraction", "Flat_fraction", "Median_directional_run_hours", "P90_directional_run_hours", "Switches_per_day", "BE10_CASE", "GOOD_SHARPE_CASE", "STRONG_SHARPE_CASE", "PREVIOUS_SELECTED_CASE", "BE10_OR_GOOD_SHARPE"]
    atomic_csv(output / "all_10m15m_results.csv", master[common_cols])
    atomic_csv(output / "be10_cases.csv", master.loc[master.BE10_CASE, common_cols])
    atomic_csv(output / "positive_be10_cases.csv", master.loc[master.Signed_BE_bps.ge(10), common_cols])
    atomic_csv(output / "good_sharpe_cases.csv", master.loc[master.GOOD_SHARPE_CASE, common_cols])
    atomic_csv(output / "strong_sharpe_cases.csv", master.loc[master.STRONG_SHARPE_CASE, common_cols])
    selected_case_cols = common_cols + ["performance_figure_path"]
    atomic_csv(output / "selected_cases.csv", detail[selected_case_cols])
    for row in detail.itertuples(index=False):
        source_path = case_path(result_root, row.semantic_execution_hash, row.symbol, row.timeframe)
        series = pd.read_parquet(source_path)
        performance_figure(output / row.performance_figure_path, row.strategy_id, pd.Series(row._asdict()), series)
    validation = {
        "status": "PASSED", "logical_rows": len(master), "physical_timeseries": len(sharpes),
        "strategies": int(master.strategy_id.nunique()), "semantic_groups": int(master.semantic_execution_hash.nunique()),
        "selected_strategy_count": len(selected_strategies), "BE10_case_count_physical": int(physical.BE10_CASE.sum()),
        "good_Sharpe_case_count_physical": int(physical.GOOD_SHARPE_CASE.sum()), "strong_Sharpe_case_count_physical": int(physical.STRONG_SHARPE_CASE.sum()),
        "previous_selected_case_count_physical": int(physical.PREVIOUS_SELECTED_CASE.sum()), "detailed_logical_figure_count": len(detail),
        "BE10_case_count_logical": int(master.BE10_CASE.sum()), "good_Sharpe_case_count_logical": int(master.GOOD_SHARPE_CASE.sum()),
        "strong_Sharpe_case_count_logical": int(master.STRONG_SHARPE_CASE.sum()),
        "strategies_selected_by_BE": int(selected_strategies.selected_by_BE.sum()),
        "strategies_selected_by_Sharpe": int(selected_strategies.selected_by_Sharpe.sum()),
        "BE_only_strategies": int((selected_strategies.selected_by_BE & ~selected_strategies.selected_by_Sharpe).sum()),
        "Sharpe_only_strategies": int((~selected_strategies.selected_by_BE & selected_strategies.selected_by_Sharpe).sum()),
        "selected_by_both": int((selected_strategies.selected_by_BE & selected_strategies.selected_by_Sharpe).sum()),
        "previous_selected_independent_groups": int(selected_strategies.previous_selected.sum()),
        "previous_selected_strategy_timeframe_rows": int(len(previous_keys)),
        "max_aggregate_residuals": max_residuals, "sharpe_definition": "UTC daily arithmetic increments; mean/sample_std(ddof=1)*sqrt(365); rf=0",
        "summary_figure_count": len(selected_strategies) * 2, "detailed_figure_count": len(detail),
        "five_bp_columns_in_new_csv": 0, "backtests_rerun_full_matrix": 0, "rematerialized_physical_10m15m_timeseries": len(sharpes),
    }
    atomic_json(output / "validation_summary.json", validation)
    zip_path = output.with_suffix(".zip")
    if zip_path.exists(): zip_path.unlink()
    with ZipFile(zip_path, "w", ZIP_DEFLATED, allowZip64=True) as archive:
        for file in sorted(output.rglob("*")):
            if file.is_file(): archive.write(file, file.relative_to(output.parent))
    with ZipFile(zip_path) as archive:
        if archive.testzip() is not None: raise RuntimeError("ZIP integrity failed")
    validation["zip_path"] = str(zip_path); validation["zip_sha256"] = sha256(zip_path)
    atomic_json(output / "validation_summary.json", validation)
    print(json.dumps(validation, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
