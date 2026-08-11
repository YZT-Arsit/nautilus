#!/usr/bin/env python3
"""Build arithmetic-return risk and leverage sensitivity artifacts."""

from __future__ import annotations

import argparse
import math
import shutil
import zipfile
from pathlib import Path

import matplotlib as mpl


mpl.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


INPUT_COLUMNS = ("event_time_ns", "total_return", "turnover")
DAYS_PER_YEAR = 365.25


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--batch",
        action="append",
        required=True,
        help="Label/path pair, for example forward=D:\\...\\batch",
    )
    parser.add_argument(
        "--fee-bps",
        type=float,
        nargs="+",
        default=[0.0, 1.7, 5.0],
    )
    parser.add_argument(
        "--leverage",
        type=float,
        nargs="+",
        default=[0.5, 1.0, 2.0, 3.0, 5.0],
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def parse_batches(values: list[str]) -> dict[str, Path]:
    batches: dict[str, Path] = {}
    for value in values:
        label, separator, path_text = value.partition("=")
        if not separator or not label:
            raise ValueError(f"invalid --batch {value!r}; expected LABEL=PATH")
        root = Path(path_text)
        if not root.is_dir():
            raise ValueError(f"batch root does not exist: {root}")
        if label in batches:
            raise ValueError(f"duplicate batch label: {label}")
        batches[label] = root
    return batches


def discover(root: Path) -> dict[str, Path]:
    inputs = {
        path.parent.name: path
        for path in root.glob("*/timeseries.parquet")
        if path.is_file()
    }
    if not inputs:
        raise ValueError(f"no strategy timeseries found under {root}")
    return inputs


def daily_metrics(
    frame: pd.DataFrame,
    fee_bps: float,
) -> dict[str, float | int]:
    event_time = pd.to_datetime(frame["event_time_ns"], unit="ns", utc=True)
    net_return = (
        frame["total_return"].to_numpy(dtype=np.float64, copy=False)
        - frame["turnover"].to_numpy(dtype=np.float64, copy=False)
        * fee_bps
        / 10_000.0
    )
    daily = (
        pd.Series(net_return, index=event_time)
        .resample("1D")
        .sum()
        .to_numpy(dtype=np.float64, copy=False)
    )
    cumulative = np.cumsum(net_return)
    running_max = np.maximum.accumulate(np.r_[0.0, cumulative])[1:]
    drawdown = cumulative - running_max
    elapsed_years = (
        (event_time.iloc[-1] - event_time.iloc[0]).total_seconds()
        / (DAYS_PER_YEAR * 86_400.0)
    )
    daily_std = float(np.std(daily, ddof=1)) if len(daily) > 1 else math.nan
    daily_mean = float(np.mean(daily))
    downside = daily[daily < 0]
    downside_std = (
        float(np.sqrt(np.mean(np.square(downside))))
        if len(downside)
        else math.nan
    )
    return {
        "row_count": len(frame),
        "day_count": len(daily),
        "elapsed_years": elapsed_years,
        "total_simple_return_1x": float(np.sum(net_return)),
        "annual_arithmetic_return_1x": (
            float(np.sum(net_return)) / elapsed_years
            if elapsed_years > 0
            else math.nan
        ),
        "annualized_daily_volatility_1x": daily_std * math.sqrt(DAYS_PER_YEAR),
        "daily_sharpe": (
            daily_mean / daily_std * math.sqrt(DAYS_PER_YEAR)
            if daily_std > 0
            else math.nan
        ),
        "daily_sortino": (
            daily_mean / downside_std * math.sqrt(DAYS_PER_YEAR)
            if downside_std > 0
            else math.nan
        ),
        "max_arithmetic_drawdown_1x": float(-np.min(drawdown)),
        "minimum_cumulative_return_1x": float(np.min(cumulative)),
        "total_turnover_1x": float(frame["turnover"].sum()),
    }


def expand_leverage(
    batch: str,
    strategy: str,
    fee_bps: float,
    metrics: dict[str, float | int],
    leverages: list[float],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for leverage in leverages:
        total_return = float(metrics["total_simple_return_1x"]) * leverage
        drawdown = float(metrics["max_arithmetic_drawdown_1x"]) * leverage
        minimum = float(metrics["minimum_cumulative_return_1x"]) * leverage
        rows.append(
            {
                "batch": batch,
                "strategy": strategy,
                "fee_bps": fee_bps,
                "leverage": leverage,
                **metrics,
                "total_simple_return": total_return,
                "annual_arithmetic_return": float(
                    metrics["annual_arithmetic_return_1x"]
                )
                * leverage,
                "annualized_daily_volatility": float(
                    metrics["annualized_daily_volatility_1x"]
                )
                * leverage,
                "max_arithmetic_drawdown": drawdown,
                "minimum_cumulative_return": minimum,
                "total_turnover": float(metrics["total_turnover_1x"]) * leverage,
                "fixed_capital_loss_threshold_crossed": minimum <= -1.0,
                "sharpe_scale_invariant": True,
            }
        )
    return rows


def render_strategy_chart(
    batch: str,
    strategy: str,
    frame: pd.DataFrame,
    destination: Path,
) -> None:
    figure, axes = plt.subplots(1, 2, figsize=(14, 6))
    for fee_bps, fee_frame in frame.groupby("fee_bps", sort=True):
        axes[0].plot(
            fee_frame["leverage"],
            fee_frame["total_simple_return"] * 100,
            marker="o",
            label=f"{fee_bps:g} bp",
        )
        axes[1].plot(
            fee_frame["leverage"],
            fee_frame["max_arithmetic_drawdown"] * 100,
            marker="o",
            label=f"{fee_bps:g} bp",
        )
    axes[0].axhline(0, color="black", linewidth=0.8)
    axes[0].set_title("Total arithmetic return")
    axes[0].set_ylabel("Five-year return (%)")
    axes[1].set_title("Maximum arithmetic drawdown")
    axes[1].set_ylabel("Drawdown (percentage points)")
    for axis in axes:
        axis.set_xlabel("Exposure leverage (x capital)")
        axis.grid(alpha=0.25)
        axis.legend(title="One-way fee")
    figure.suptitle(f"{strategy} ({batch}) — leverage sensitivity")
    figure.text(
        0.5,
        0.012,
        "BTCUSDT 1m | fixed-capital arithmetic returns | slippage 0 bp | "
        "funding included | leverage scales exposure and traded notional",
        ha="center",
        fontsize=8,
    )
    figure.tight_layout(rect=(0, 0.04, 1, 0.95))
    figure.savefig(destination, dpi=160)
    plt.close(figure)


def main() -> int:  # noqa: C901 - artifact orchestration is intentionally linear
    args = parse_args()
    if any(value < 0 for value in args.fee_bps):
        raise ValueError("fee bps cannot be negative")
    if any(value <= 0 for value in args.leverage):
        raise ValueError("leverage must be positive")
    batches = parse_batches(args.batch)
    if args.output_dir.exists():
        if not args.overwrite:
            raise ValueError(f"output exists: {args.output_dir}")
        shutil.rmtree(args.output_dir)
    charts = args.output_dir / "charts"
    charts.mkdir(parents=True)

    rows: list[dict[str, object]] = []
    for batch, root in batches.items():
        for strategy, path in sorted(discover(root).items()):
            frame = pd.read_parquet(path, columns=list(INPUT_COLUMNS))
            for fee_bps in sorted(set(args.fee_bps)):
                metrics = daily_metrics(frame, fee_bps)
                rows.extend(
                    expand_leverage(
                        batch,
                        strategy,
                        fee_bps,
                        metrics,
                        sorted(set(args.leverage)),
                    )
                )

    summary = pd.DataFrame(rows).sort_values(
        ["batch", "strategy", "fee_bps", "leverage"]
    )
    summary.to_csv(args.output_dir / "risk_leverage_summary.csv", index=False)
    base = summary.loc[summary["leverage"].eq(1.0)].copy()
    base.to_csv(args.output_dir / "risk_summary_1x.csv", index=False)
    aggregate = (
        base.groupby(["batch", "fee_bps"], as_index=False)
        .agg(
            strategy_count=("strategy", "nunique"),
            positive_count=("total_simple_return", lambda s: int((s > 0).sum())),
            median_daily_sharpe=("daily_sharpe", "median"),
            maximum_daily_sharpe=("daily_sharpe", "max"),
            median_max_drawdown=("max_arithmetic_drawdown", "median"),
            median_total_return=("total_simple_return", "median"),
        )
        .sort_values(["batch", "fee_bps"])
    )
    aggregate.to_csv(args.output_dir / "risk_aggregate.csv", index=False)
    for (batch, strategy), frame in summary.groupby(["batch", "strategy"], sort=True):
        render_strategy_chart(
            str(batch),
            str(strategy),
            frame,
            charts / f"{batch}__{strategy}__leverage.png",
        )

    archive = args.output_dir / f"{args.output_dir.name}.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(args.output_dir.rglob("*")):
            if path.is_file() and path != archive:
                bundle.write(path, path.relative_to(args.output_dir))
    print(f"Built risk/leverage analysis: {archive}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
