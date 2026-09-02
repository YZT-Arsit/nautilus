#!/usr/bin/env python3
"""Render the terminal boss review HTML and bounded standard figures."""

from __future__ import annotations

import argparse
import html
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]


def metric_label(value: object, format_spec: str, suffix: str = "") -> str:
    """Format an optional finite metric without manufacturing a numeric value."""
    if value is None:
        return "N/A"
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return "N/A"
    if not np.isfinite(numeric):
        return "N/A"
    return f"{numeric:{format_spec}}{suffix}"


def performance_figure(summary: dict, timeseries: pd.DataFrame, output: Path) -> None:
    timestamps = pd.to_datetime(timeseries.event_time_ns, unit="ns", utc=True)
    figure, axes = plt.subplots(
        3, 1, figsize=(14, 9), sharex=True,
        gridspec_kw={"height_ratios": [2.2, 1.0, 1.0]}, constrained_layout=True,
    )
    upper = axes[0]
    upper.plot(timestamps, timeseries.cumulative_return_with_premium * 100, label="Return — Premium Included", color="#1565C0")
    upper.plot(timestamps, timeseries.cumulative_return_without_premium * 100, label="Return — Premium Excluded", color="#00897B", alpha=0.85)
    upper.axhline(0, color="#555555", linewidth=0.7)
    upper.set_ylabel("Cumulative Return (1x, %)")
    turnover = upper.twinx()
    turnover.plot(timestamps, timeseries.cumulative_turnover * 100, label="Cumulative Turnover", color="#EF6C00", linestyle="--", alpha=0.75)
    turnover.set_ylabel("Cumulative Turnover (% of capital)")
    lines = upper.lines[:2] + turnover.lines
    upper.legend(lines, [line.get_label() for line in lines], loc="best", fontsize=8)
    axes[1].step(timestamps, timeseries.executed_position, where="post", color="#3949AB")
    axes[1].axhline(0, color="#777777", linewidth=0.6)
    axes[1].set_ylabel("Executed Position (x leverage)")
    axes[2].plot(timestamps, timeseries.drawdown * 100, color="#C62828")
    axes[2].fill_between(timestamps, timeseries.drawdown * 100, 0, color="#EF9A9A", alpha=0.35)
    axes[2].set_ylabel("Drawdown (%)")
    axes[2].set_xlabel("UTC time")
    figure.suptitle(
        f"{summary['representative_strategy_id']} | {summary['symbol']} | {summary['timeframe']} signal → raw tick execution\n"
        f"FEE0 Return={metric_label(summary['Return_fee0'], '.2%')} | "
        f"5bp={metric_label(summary['Return_5bp'], '.2%')} | "
        f"BE={metric_label(summary['BE_bps'], '.2f', ' bps')} | "
        f"MDD={metric_label(summary['MDD'], '.2%')} | "
        f"Nonflat={metric_label(summary['nonflat_fraction'], '.1%')} | "
        f"Wait P95={metric_label(summary['first_tick_wait_p95_ms'], '.0f', ' ms')}",
        fontsize=12,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output, dpi=150)
    plt.close(figure)


def render(root: Path) -> dict:
    master = pd.read_csv(root / "boss_multitimeframe_tick_master.csv")
    aggregate = pd.read_csv(root / "boss_multitimeframe_strategy_summary.csv")
    figures = root / "figures"
    figures.mkdir(parents=True, exist_ok=True)
    multi = aggregate.loc[aggregate.positive_Return_BE_symbols >= 2, ["strategy_id", "timeframe"]]
    keys = set(map(tuple, multi.to_records(index=False)))
    selected = master[
        master.apply(lambda row: (row.strategy_id, row.timeframe) in keys, axis=1)
        | (master.Return_5bp > 0)
    ].copy()
    top_persistent = master.sort_values(
        ["nonflat_fraction", "BE_bps"], ascending=[False, False]
    ).head(20)
    selected = pd.concat([selected, top_persistent], ignore_index=True).drop_duplicates(
        ["semantic_execution_hash", "symbol", "timeframe"]
    )
    rendered = []
    for row in selected.itertuples():
        case_root = (
            root / "matrix_cases" / f"symbol={row.symbol}" / f"timeframe={row.timeframe}"
            / f"semantic={row.semantic_execution_hash}"
        )
        source = case_root / "review_timeseries.parquet"
        summary_path = case_root / "summary.json"
        if not source.is_file() or not summary_path.is_file():
            continue
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        target = figures / f"{summary['representative_strategy_id']}_{row.symbol}_{row.timeframe}_performance.png"
        if not target.is_file():
            performance_figure(summary, pd.read_parquet(source), target)
        rendered.append(str(target))

    # One compact reference-vs-project position-shape comparison.
    reference = pd.read_csv(root / "reference_position_behavior.csv")
    ours = master.sort_values(["nonflat_fraction", "BE_bps"], ascending=[False, False]).head(5)
    labels = [
        f"External {row.reference_strategy}/{row.symbol}"
        for row in reference.head(4).itertuples()
    ]
    values = [
        (row.long_fraction, row.short_fraction, row.flat_fraction)
        for row in reference.head(4).itertuples()
    ]
    labels += [f"{row.strategy_id}/{row.symbol}/{row.timeframe}" for row in ours.itertuples()]
    values += [(row.long_fraction, row.short_fraction, row.flat_fraction) for row in ours.itertuples()]
    array = np.asarray(values)
    fig, ax = plt.subplots(figsize=(13, max(5, len(labels) * 0.48)), constrained_layout=True)
    y = np.arange(len(labels))
    ax.barh(y, array[:, 0] * 100, label="Long", color="#1976D2")
    ax.barh(y, array[:, 1] * 100, left=array[:, 0] * 100, label="Short", color="#D32F2F")
    ax.barh(y, array[:, 2] * 100, left=(array[:, 0] + array[:, 1]) * 100, label="Flat", color="#BDBDBD")
    ax.set_yticks(y, labels)
    ax.invert_yaxis()
    ax.set_xlabel("Observation share (%)")
    ax.set_title("Position behavior only — external reference vs project persistent cases")
    ax.legend(ncol=3)
    reference_path = figures / "reference_position_behavior_comparison.png"
    fig.savefig(reference_path, dpi=150)
    plt.close(fig)
    rendered.append(str(reference_path))

    counts = master.groupby("timeframe").agg(
        return_be_positive=("Return_fee0", lambda x: 0),
        five_bp_survivors=("Return_5bp", lambda x: int((x > 0).sum())),
        near_always=("near_always_in_market", "sum"),
    )
    counts["return_be_positive"] = master.assign(
        positive=(master.Return_fee0 > 0) & (master.BE_bps > 0)
    ).groupby("timeframe").positive.sum()
    counts = counts.reindex(["10m", "15m", "5m", "1m"])
    fig, ax = plt.subplots(figsize=(10, 5.5), constrained_layout=True)
    counts.plot.bar(ax=ax, color=["#1565C0", "#2E7D32", "#EF6C00"])
    ax.set_title("Boss multi-timeframe screen — descriptive case counts")
    ax.set_xlabel("Signal timeframe")
    ax.set_ylabel("Logical cases")
    ax.legend(["Return + BE positive", "5bp survivor", "Near always in market"])
    summary_figure = figures / "timeframe_screen_counts.png"
    fig.savefig(summary_figure, dpi=150)
    plt.close(fig)
    rendered.append(str(summary_figure))

    top = master.sort_values(["Return_5bp", "BE_bps"], ascending=False).head(50)
    overview = {
        "strategies": int(master.strategy_id.nunique()),
        "symbols": int(master.symbol.nunique()),
        "cases": len(master),
        "failures": 0,
        "10m_return_be_positive": int(((master.timeframe == "10m") & (master.Return_fee0 > 0) & (master.BE_bps > 0)).sum()),
        "15m_return_be_positive": int(((master.timeframe == "15m") & (master.Return_fee0 > 0) & (master.BE_bps > 0)).sum()),
        "five_bp_survivors": int((master.Return_5bp > 0).sum()),
        "near_always": int(master.near_always_in_market.sum()),
    }
    html_path = root / "boss_multitimeframe_tick_review.html"
    html_path.write_text(
        "<!doctype html><meta charset='utf-8'><title>Boss Multi-Timeframe Tick Screen</title>"
        "<style>body{font-family:Arial;margin:28px;color:#1f2937}h1{color:#123a63}"
        "table{border-collapse:collapse;font-size:12px}th{background:#123a63;color:white}"
        "td,th{padding:6px 8px;border-bottom:1px solid #ddd;text-align:right}td:first-child,th:first-child{text-align:left}"
        ".cards{display:flex;gap:12px;flex-wrap:wrap}.card{background:#eef4fb;padding:12px 18px;border-radius:6px}img{max-width:1100px}</style>"
        "<h1>Boss Multi-Timeframe / Raw-Tick Screen</h1>"
        + "<div class='cards'>" + "".join(
            f"<div class='card'><b>{html.escape(key)}</b><br>{value}</div>" for key, value in overview.items()
        ) + "</div>"
        + "<h2>Timeframe counts</h2><img src='figures/timeframe_screen_counts.png'>"
        + "<h2>Reference position behavior comparison</h2><img src='figures/reference_position_behavior_comparison.png'>"
        + "<h2>Top descriptive cases</h2>" + top.to_html(index=False, escape=True)
        + "<p>Return is 1x arithmetic and non-compounded. The 5bp line is a hypothetical overlay. Tick means the first official Binance raw trade at or after the completed-bar boundary.</p>",
        encoding="utf-8",
    )
    return {"figures": len(rendered), "html": str(html_path), **overview}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root", type=Path,
        default=ROOT / "outputs/baseline_evaluation/boss_multitimeframe_tick_screen",
    )
    args = parser.parse_args()
    print(json.dumps(render(args.root), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
