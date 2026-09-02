#!/usr/bin/env python3
"""Recompute directional persistence from saved executed-position review paths.

This is a post-processing task only.  It reads the completed matrix master and
the saved review_timeseries parquet files.  It never invokes a strategy,
feature builder, execution model, backtest, or tick-index builder.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
RESULT_ROOT = ROOT / "outputs/baseline_evaluation/boss_multitimeframe_tick_screen"
NS_PER_SECOND = 1_000_000_000
BAR_SECONDS = 60.0
DAY_SECONDS = 86_400.0
PREVIOUS_TOP = (
    "xlsx_s1_0020",
    "xlsx_s2_0434",
    "xlsx_s2_0157",
    "xlsx_s2_0396",
    "xlsx_s2_0593",
    "xlsx_s2_0770",
    "xlsx_s1_0013",
    "xlsx_s2_0280",
    "xlsx_s2_0366",
    "xlsx_s2_0563",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_csv(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    frame.to_csv(temporary, index=False, encoding="utf-8-sig")
    os.replace(temporary, path)


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def compress_state_runs(timeseries: pd.DataFrame) -> pd.DataFrame:
    """Return exact state intervals from transition-preserving review points."""
    required = {"event_time_ns", "executed_position"}
    missing = required - set(timeseries.columns)
    if missing:
        raise ValueError(f"review timeseries missing columns: {sorted(missing)}")
    frame = (
        timeseries[["event_time_ns", "executed_position"]]
        .sort_values("event_time_ns")
        .drop_duplicates("event_time_ns", keep="last")
        .reset_index(drop=True)
    )
    if frame.empty:
        raise ValueError("empty review timeseries")
    event_time = frame.event_time_ns.to_numpy(np.int64)
    if np.any(np.diff(event_time) <= 0):
        raise ValueError("review timeseries is not strictly chronological")
    signs = np.sign(frame.executed_position.to_numpy(float)).astype(np.int8)
    starts = np.flatnonzero(np.r_[True, signs[1:] != signs[:-1]])
    start_ns = event_time[starts]
    end_ns = np.r_[start_ns[1:], event_time[-1] + int(BAR_SECONDS * NS_PER_SECOND)]
    runs = pd.DataFrame(
        {
            "state": signs[starts],
            "start_time_ns": start_ns,
            "end_time_ns": end_ns,
        }
    )
    runs["duration_seconds"] = (
        runs.end_time_ns.to_numpy(np.int64) - runs.start_time_ns.to_numpy(np.int64)
    ) / NS_PER_SECOND
    if (runs.duration_seconds <= 0).any():
        raise ValueError("non-positive state-run duration")
    return runs


def directional_persistence_metrics(timeseries: pd.DataFrame) -> dict[str, Any]:
    runs = compress_state_runs(timeseries)
    directional = runs[runs.state != 0].reset_index(drop=True)
    durations = directional.duration_seconds.to_numpy(float)
    total_seconds = float(runs.duration_seconds.sum())
    observation_count = int(round(total_seconds / BAR_SECONDS))
    elapsed_days = total_seconds / DAY_SECONDS
    nonzero_states = directional.state.to_numpy(np.int8)
    sign_switch_count = int((nonzero_states[1:] != nonzero_states[:-1]).sum())
    run_states = runs.state.to_numpy(np.int8)
    direct_reversal_count = int(((run_states[1:] * run_states[:-1]) < 0).sum())

    def quantile(q: float) -> float:
        return float(np.quantile(durations, q)) if len(durations) else 0.0

    long_seconds = float(runs.loc[runs.state > 0, "duration_seconds"].sum())
    short_seconds = float(runs.loc[runs.state < 0, "duration_seconds"].sum())
    flat_seconds = float(runs.loc[runs.state == 0, "duration_seconds"].sum())
    nonflat_fraction = (long_seconds + short_seconds) / total_seconds
    median_run = quantile(0.50)
    switches_per_day = sign_switch_count / elapsed_days if elapsed_days else 0.0
    always_in_market = nonflat_fraction >= 0.90
    directionally_persistent = (
        always_in_market
        and median_run >= DAY_SECONDS
        and switches_per_day <= 1.0
    )
    if directionally_persistent:
        classification = "DIRECTIONALLY_PERSISTENT"
    elif always_in_market:
        classification = "ALWAYS_IN_MARKET_HIGH_SWITCHING_OR_SHORT_RUNS"
    elif median_run >= DAY_SECONDS and switches_per_day <= 1.0:
        classification = "LONG_DIRECTIONAL_RUNS_BUT_NOT_HIGH_NONFLAT"
    else:
        classification = "INTERMITTENT_OR_HIGH_SWITCHING"
    return {
        "observation_count": observation_count,
        "elapsed_days": elapsed_days,
        "nonflat_fraction_v2": nonflat_fraction,
        "long_fraction_v2": long_seconds / total_seconds,
        "short_fraction_v2": short_seconds / total_seconds,
        "flat_fraction_v2": flat_seconds / total_seconds,
        "directional_run_count": int(len(directional)),
        "long_run_count": int((directional.state > 0).sum()),
        "short_run_count": int((directional.state < 0).sum()),
        "median_directional_run_duration": median_run,
        "P75_directional_run_duration": quantile(0.75),
        "P90_directional_run_duration": quantile(0.90),
        "max_directional_run_duration": float(durations.max()) if len(durations) else 0.0,
        "median_directional_run_hours": median_run / 3600.0,
        "P90_directional_run_hours": quantile(0.90) / 3600.0,
        "sign_switch_count_v2": sign_switch_count,
        "sign_switches_per_day": switches_per_day,
        "sign_switches_per_1000_bars": (
            sign_switch_count * 1000.0 / observation_count if observation_count else 0.0
        ),
        "direct_reversal_count_v2": direct_reversal_count,
        "position_change_count_v2": max(len(runs) - 1, 0),
        "always_in_market": always_in_market,
        "directionally_persistent": directionally_persistent,
        "persistence_class_v2": classification,
        "persistence_rule_v2": (
            "DIRECTIONALLY_PERSISTENT iff nonflat_fraction>=0.90 AND "
            "median_directional_run_duration>=86400 seconds AND sign_switches_per_day<=1.0"
        ),
        "directional_run_duration_unit": "seconds",
        "sign_switch_definition_v2": (
            "change of sign between consecutive non-flat runs; flat intervals do not hide a later side change"
        ),
        "direct_reversal_definition_v2": "adjacent LONG-to-SHORT or SHORT-to-LONG transition with no flat run",
    }


def case_root(root: Path, row: pd.Series | Any) -> Path:
    return (
        root
        / "matrix_cases"
        / f"symbol={row.symbol}"
        / f"timeframe={row.timeframe}"
        / f"semantic={row.semantic_execution_hash}"
    )


def build_metrics(root: Path, master: pd.DataFrame) -> pd.DataFrame:
    physical_keys = ["semantic_execution_hash", "symbol", "timeframe"]
    physical = master.drop_duplicates(physical_keys).sort_values(physical_keys)
    metric_rows: list[dict[str, Any]] = []
    for row in physical.itertuples(index=False):
        source = case_root(root, row) / "review_timeseries.parquet"
        if not source.is_file():
            raise FileNotFoundError(source)
        metrics = directional_persistence_metrics(pd.read_parquet(source))
        metric_rows.append(
            {
                "semantic_execution_hash": row.semantic_execution_hash,
                "symbol": row.symbol,
                "timeframe": row.timeframe,
                **metrics,
                "review_timeseries_path": str(source),
                "review_timeseries_sha256": sha256_file(source),
            }
        )
    metric_frame = pd.DataFrame(metric_rows)
    merged = master.merge(metric_frame, on=physical_keys, how="left", validate="many_to_one")
    if merged.persistence_class_v2.isna().any():
        raise ValueError("missing v2 metrics after merge")
    fraction_residual = (
        merged.long_fraction_v2 + merged.short_fraction_v2 + merged.flat_fraction_v2 - 1.0
    ).abs().max()
    if fraction_residual > 1e-12:
        raise ValueError(f"v2 position fraction residual: {fraction_residual}")
    # Saved paths preserve every transition and predecessor; the time-weighted
    # reconstructed fractions must reconcile to the original full-clock metrics.
    original_residual = np.max(
        np.abs(
            merged[["long_fraction", "short_fraction", "flat_fraction"]].to_numpy(float)
            - merged[["long_fraction_v2", "short_fraction_v2", "flat_fraction_v2"]].to_numpy(float)
        )
    )
    if original_residual > 1.1 / max(int(merged.observation_count.min()), 1):
        raise ValueError(f"executed-position fraction reconciliation failed: {original_residual}")
    merged["turnover_raw"] = merged.Turnover_raw
    merged["turnover_percent"] = merged.Turnover_pct
    merged["Return"] = merged.Return_fee0
    merged["BE"] = merged.BE_bps
    merged["metric_contract_version"] = "PERSISTENCE_V2_DIRECTIONAL_RUNS_1"
    return merged


def candidate_table(metrics: pd.DataFrame) -> pd.DataFrame:
    candidates = metrics[metrics.nonflat_fraction_v2 >= 0.90].copy()
    candidates["ranking_basis"] = (
        "eligibility nonflat_fraction>=0.90; then lexicographic: directionally_persistent DESC, "
        "median_directional_run_duration DESC, sign_switches_per_day ASC, turnover_raw ASC, "
        "nonflat_fraction DESC as final tie-break"
    )
    candidates = candidates.sort_values(
        [
            "directionally_persistent",
            "median_directional_run_duration",
            "sign_switches_per_day",
            "turnover_raw",
            "nonflat_fraction_v2",
            "strategy_id",
            "symbol",
            "timeframe",
        ],
        ascending=[False, False, True, True, False, True, True, True],
    ).reset_index(drop=True)
    candidates.insert(0, "descriptive_rank", np.arange(1, len(candidates) + 1))
    return candidates


def previous_top_reassessment(metrics: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for previous_rank, strategy_id in enumerate(PREVIOUS_TOP, start=1):
        group = metrics[metrics.strategy_id == strategy_id].sort_values(
            ["nonflat_fraction", "BE_bps", "symbol", "timeframe"],
            ascending=[False, False, True, True],
        )
        if group.empty:
            raise ValueError(f"previous top strategy missing: {strategy_id}")
        row = group.iloc[0].copy()
        row["previous_rank"] = previous_rank
        rows.append(row)
    result = pd.DataFrame(rows).sort_values("previous_rank")
    first = [
        "previous_rank",
        "strategy_id",
        "symbol",
        "timeframe",
        "persistence_class_v2",
        "directionally_persistent",
        "nonflat_fraction_v2",
        "median_directional_run_duration",
        "P90_directional_run_duration",
        "sign_switches_per_day",
        "turnover_raw",
        "turnover_percent",
        "Return",
        "BE",
        "Return_5bp",
    ]
    return result[first + [column for column in result.columns if column not in first]]


def regime_figure(summary: pd.Series, timeseries: pd.DataFrame, output: Path) -> None:
    runs = compress_state_runs(timeseries)
    timestamps = pd.to_datetime(timeseries.event_time_ns, unit="ns", utc=True)
    fig, axes = plt.subplots(
        3,
        1,
        figsize=(15, 10),
        sharex=True,
        gridspec_kw={"height_ratios": [2.2, 1.25, 1.0]},
        constrained_layout=True,
    )
    upper = axes[0]
    upper.plot(
        timestamps,
        timeseries.cumulative_return_with_premium * 100,
        label="Return — Premium Included",
        color="#1565C0",
        linewidth=1.0,
    )
    upper.plot(
        timestamps,
        timeseries.cumulative_return_without_premium * 100,
        label="Return — Premium Excluded",
        color="#00897B",
        linewidth=0.9,
        alpha=0.8,
    )
    upper.axhline(0, color="#555555", linewidth=0.7)
    upper.set_ylabel("Cumulative Return (1x, %)")
    turnover_axis = upper.twinx()
    turnover_axis.plot(
        timestamps,
        timeseries.cumulative_turnover * 100,
        label="Cumulative Turnover",
        color="#EF6C00",
        linestyle="--",
        linewidth=1.0,
    )
    turnover_axis.set_ylabel("Cumulative Turnover (% of capital)")
    lines = upper.lines[:2] + turnover_axis.lines
    upper.legend(lines, [line.get_label() for line in lines], loc="best", fontsize=8)

    regime = axes[1]
    colors = {1: "#1976D2", 0: "#BDBDBD", -1: "#D32F2F"}
    labels = {1: "LONG", 0: "FLAT", -1: "SHORT"}
    for state in (1, 0, -1):
        selected = runs[runs.state == state]
        intervals = [
            (
                mdates.date2num(pd.Timestamp(row.start_time_ns, unit="ns", tz="UTC")),
                (row.end_time_ns - row.start_time_ns) / NS_PER_SECOND / DAY_SECONDS,
            )
            for row in selected.itertuples()
        ]
        if intervals:
            regime.broken_barh(
                intervals,
                (state - 0.32, 0.64),
                facecolors=colors[state],
                edgecolors="none",
                alpha=0.85,
                label=labels[state],
            )
    regime.step(
        timestamps,
        np.sign(timeseries.executed_position),
        where="post",
        color="#212121",
        linewidth=0.35,
        alpha=0.55,
    )
    regime.set_yticks([-1, 0, 1], ["SHORT", "FLAT", "LONG"])
    regime.set_ylim(-1.45, 1.45)
    regime.set_ylabel("Executed regime")
    regime.legend(loc="upper right", ncols=3, fontsize=8)
    regime.text(
        0.01,
        0.98,
        (
            f"Long {summary.long_fraction_v2:.1%} | Short {summary.short_fraction_v2:.1%} | "
            f"Flat {summary.flat_fraction_v2:.1%} | Nonflat {summary.nonflat_fraction_v2:.1%}\n"
            f"Median run {summary.median_directional_run_hours:.1f} h | "
            f"P90 run {summary.P90_directional_run_hours:.1f} h | "
            f"Sign switches {int(summary.sign_switch_count_v2):,} | "
            f"Switches/day {summary.sign_switches_per_day:.3f}"
        ),
        transform=regime.transAxes,
        va="top",
        ha="left",
        fontsize=8,
        bbox={"boxstyle": "round,pad=0.35", "facecolor": "white", "alpha": 0.88, "edgecolor": "#888888"},
    )

    axes[2].plot(timestamps, timeseries.drawdown * 100, color="#C62828", linewidth=1.0)
    axes[2].fill_between(timestamps, timeseries.drawdown * 100, 0, color="#EF9A9A", alpha=0.35)
    axes[2].set_ylabel("Drawdown (%)")
    axes[2].set_xlabel("UTC time")
    fig.suptitle(
        f"{summary.strategy_id} | {summary.symbol} | {summary.timeframe} signal → raw tick execution\n"
        f"Persistence v2: {summary.persistence_class_v2} | Return={summary.Return:.2%} | "
        f"5bp={summary.Return_5bp:.2%} | BE={summary.BE:.2f} bps | "
        f"Turnover={summary.turnover_percent:,.2f}%",
        fontsize=12,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=200)
    plt.close(fig)


def render_figures(
    root: Path,
    candidates: pd.DataFrame,
    previous: pd.DataFrame,
    figure_limit: int,
) -> list[Path]:
    example = candidates[
        (candidates.strategy_id == "xlsx_s1_0020")
        & (candidates.symbol == "BTCUSDT")
        & (candidates.timeframe == "15m")
    ]
    selected = pd.concat(
        [candidates.head(figure_limit), previous, example], ignore_index=True
    ).drop_duplicates(["strategy_id", "symbol", "timeframe"])
    output_root = root / "figures" / "persistent_v2"
    rendered = []
    for row in selected.itertuples(index=False):
        source = case_root(root, row) / "review_timeseries.parquet"
        target = output_root / f"{row.strategy_id}_{row.symbol}_{row.timeframe}_persistence_v2.png"
        regime_figure(pd.Series(row._asdict()), pd.read_parquet(source), target)
        rendered.append(target)
    expected = set(rendered)
    for stale in output_root.glob("*_persistence_v2.png"):
        if stale not in expected:
            stale.unlink()
    return rendered


def build(root: Path, figure_limit: int) -> dict[str, Any]:
    master_path = root / "boss_multitimeframe_tick_master.csv"
    if not master_path.is_file():
        raise FileNotFoundError(master_path)
    master_hash_before = sha256_file(master_path)
    master = pd.read_csv(master_path)
    if len(master) != 9_612:
        raise ValueError(f"expected 9,612 master rows, found {len(master)}")
    metrics = build_metrics(root, master)
    candidates = candidate_table(metrics)
    previous = previous_top_reassessment(metrics)
    atomic_csv(root / "persistent_position_metrics_v2.csv", metrics)
    atomic_csv(root / "persistent_position_candidates_v2.csv", candidates)
    atomic_csv(root / "previous_top_persistent_reassessment.csv", previous)
    rendered = render_figures(root, candidates, previous, figure_limit)
    master_hash_after = sha256_file(master_path)
    if master_hash_after != master_hash_before:
        raise ValueError("master result changed during v2 post-processing")

    xlsx_0020 = metrics[
        (metrics.strategy_id == "xlsx_s1_0020")
        & (metrics.symbol == "BTCUSDT")
        & (metrics.timeframe == "15m")
    ]
    if len(xlsx_0020) != 1:
        raise ValueError("xlsx_s1_0020/BTCUSDT/15m reconciliation failed")
    sample = xlsx_0020.iloc[0]
    if sample.directionally_persistent and sample.sign_switches_per_day > 1.0:
        raise ValueError("high-switch sample was incorrectly classified as persistent")
    result = {
        "status": "PASSED",
        "contract_version": "PERSISTENCE_V2_DIRECTIONAL_RUNS_1",
        "metrics_rows": len(metrics),
        "candidate_rows": len(candidates),
        "directionally_persistent_cases": int(metrics.directionally_persistent.sum()),
        "always_in_market_cases": int(metrics.always_in_market.sum()),
        "always_in_market_not_directionally_persistent_cases": int(
            (metrics.always_in_market & ~metrics.directionally_persistent).sum()
        ),
        "previous_top_rows": len(previous),
        "figures_rendered": len(rendered),
        "xlsx_s1_0020_btcusdt_15m": {
            "persistence_class_v2": sample.persistence_class_v2,
            "nonflat_fraction": sample.nonflat_fraction_v2,
            "median_directional_run_hours": sample.median_directional_run_hours,
            "P90_directional_run_hours": sample.P90_directional_run_hours,
            "sign_switches_per_day": sample.sign_switches_per_day,
            "turnover_raw": sample.turnover_raw,
        },
        "ranking": (
            "transparent ordering; no weighted score: require nonflat>=0.90, then "
            "directionally_persistent DESC, median run DESC, switches/day ASC, turnover ASC, "
            "nonflat DESC tie-break"
        ),
        "master_hash_unchanged": True,
        "backtests_rerun": 0,
        "tick_index_rebuilt": 0,
        "strategy_semantic_changes": 0,
        "parameter_changes": 0,
        "figure_paths": [str(path) for path in rendered],
    }
    atomic_json(root / "persistent_position_v2_validation_summary.json", result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=RESULT_ROOT)
    parser.add_argument("--figure-limit", type=int, default=40)
    args = parser.parse_args()
    print(json.dumps(build(args.root, args.figure_limit), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
