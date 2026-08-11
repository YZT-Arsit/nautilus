#!/usr/bin/env python3
"""Summarize strict-notional results across minute-based execution lags."""

from __future__ import annotations

import argparse
import shutil
import zipfile
from pathlib import Path

import matplotlib as mpl


mpl.use("Agg")

import matplotlib.pyplot as plt
import pandas as pd


METRICS = (
    "strategy",
    "trading_simple_return",
    "funding_simple_return",
    "total_simple_return_fee0",
    "total_turnover_x",
    "breakeven_fee_bps",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--batch",
        action="append",
        required=True,
        help="Lag/path pair, for example 1=D:\\...\\batch_lag1",
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def parse_batches(values: list[str]) -> dict[int, Path]:
    batches: dict[int, Path] = {}
    for value in values:
        lag_text, separator, path_text = value.partition("=")
        if not separator:
            raise ValueError(f"invalid --batch {value!r}; expected LAG=PATH")
        lag = int(lag_text)
        if lag < 0 or lag in batches:
            raise ValueError(f"invalid or duplicate lag: {lag}")
        path = Path(path_text)
        if not (path / "evaluation_table.csv").is_file():
            raise ValueError(f"missing evaluation_table.csv under {path}")
        batches[lag] = path
    return dict(sorted(batches.items()))


def load_summary(batches: dict[int, Path]) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    expected: set[str] | None = None
    for lag, root in batches.items():
        frame = pd.read_csv(root / "evaluation_table.csv", usecols=list(METRICS))
        names = set(frame["strategy"])
        if expected is None:
            expected = names
        elif names != expected:
            raise ValueError(f"strategy set differs at lag {lag}")
        frame.insert(1, "lag_minutes", lag)
        frame["batch_root"] = str(root)
        frames.append(frame)
    return pd.concat(frames, ignore_index=True).sort_values(
        ["strategy", "lag_minutes"]
    )


def render_strategy_chart(strategy: str, frame: pd.DataFrame, destination: Path) -> None:
    figure, left = plt.subplots(figsize=(10, 6))
    left.plot(
        frame["lag_minutes"],
        frame["total_simple_return_fee0"] * 100,
        marker="o",
        label="Total simple return (fee 0, incl. Premium/Funding)",
    )
    left.plot(
        frame["lag_minutes"],
        frame["trading_simple_return"] * 100,
        marker="o",
        label="Trading simple return (fee 0)",
    )
    left.axhline(0, color="black", linewidth=0.8, alpha=0.65)
    left.set_xlabel("Execution lag (minutes, 1m execution clock)")
    left.set_ylabel("Five-year arithmetic return (%)")
    left.grid(alpha=0.25)
    left.set_title(f"{strategy} — execution lag sensitivity")

    right = left.twinx()
    right.plot(
        frame["lag_minutes"],
        frame["total_turnover_x"],
        color="#6a3d9a",
        linestyle="--",
        marker="s",
        label="Total turnover",
    )
    right.set_ylabel("Total turnover (x fixed capital)")

    handles_left, labels_left = left.get_legend_handles_labels()
    handles_right, labels_right = right.get_legend_handles_labels()
    left.legend(handles_left + handles_right, labels_left + labels_right, loc="best")
    figure.text(
        0.5,
        0.012,
        "$100,000 strict notional | 1x | BTCUSDT 1m | slippage 0 bp | "
        "fee 0 bp | arithmetic, non-compounded",
        ha="center",
        fontsize=8,
    )
    figure.tight_layout(rect=(0, 0.04, 1, 1))
    figure.savefig(destination, dpi=160)
    plt.close(figure)


def main() -> int:
    args = parse_args()
    batches = parse_batches(args.batch)
    summary = load_summary(batches)
    if args.output_dir.exists():
        if not args.overwrite:
            raise ValueError(f"output exists: {args.output_dir}")
        shutil.rmtree(args.output_dir)
    charts = args.output_dir / "charts"
    charts.mkdir(parents=True)

    summary.to_csv(args.output_dir / "lag_sweep_summary.csv", index=False)
    aggregate = (
        summary.groupby("lag_minutes", as_index=False)
        .agg(
            strategy_count=("strategy", "nunique"),
            positive_strategy_count=("total_simple_return_fee0", lambda s: int((s > 0).sum())),
            median_total_simple_return=("total_simple_return_fee0", "median"),
            mean_total_simple_return=("total_simple_return_fee0", "mean"),
            median_turnover_x=("total_turnover_x", "median"),
            median_breakeven_fee_bps=("breakeven_fee_bps", "median"),
        )
        .sort_values("lag_minutes")
    )
    aggregate.to_csv(args.output_dir / "lag_sweep_aggregate.csv", index=False)
    for strategy, frame in summary.groupby("strategy", sort=True):
        render_strategy_chart(
            str(strategy),
            frame,
            charts / f"{strategy}_lag_sensitivity.png",
        )

    archive = args.output_dir / f"{args.output_dir.name}.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(args.output_dir.rglob("*")):
            if path.is_file() and path != archive:
                bundle.write(path, path.relative_to(args.output_dir))
    print(f"Built lag sweep summary: {archive}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
