#!/usr/bin/env python3
"""Build strict forward-versus-reversed position comparison artifacts."""

from __future__ import annotations

import argparse
import math
import shutil
import zipfile
from pathlib import Path

import matplotlib as mpl


mpl.use("Agg")

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd


COLUMNS = (
    "event_time_ns",
    "trading_return",
    "funding_return",
    "total_return",
    "turnover",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--forward-root", type=Path, required=True)
    parser.add_argument("--reverse-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def discover(root: Path) -> set[str]:
    return {
        path.parent.name
        for path in root.glob("*/timeseries.parquet")
        if path.is_file()
    }


def read_frame(root: Path, strategy: str) -> pd.DataFrame:
    frame = pd.read_parquet(root / strategy / "timeseries.parquet", columns=list(COLUMNS))
    frame["event_time"] = pd.to_datetime(frame["event_time_ns"], unit="ns", utc=True)
    return frame


def break_even_bps(total_return: float, turnover: float) -> float:
    return total_return / turnover * 10_000.0 if turnover > 0 else math.nan


def render_chart(
    strategy: str,
    forward: pd.DataFrame,
    reverse: pd.DataFrame,
    destination: Path,
) -> None:
    if not forward["event_time_ns"].equals(reverse["event_time_ns"]):
        raise ValueError(f"{strategy}: forward/reverse timestamps differ")
    plot = pd.DataFrame(
        {
            "event_time": forward["event_time"],
            "forward_trading": forward["trading_return"].cumsum(),
            "forward_total": forward["total_return"].cumsum(),
            "reverse_trading": reverse["trading_return"].cumsum(),
            "reverse_total": reverse["total_return"].cumsum(),
        }
    )
    plot = plot.set_index("event_time").resample("1D").last().dropna().reset_index()

    figure, axis = plt.subplots(figsize=(13, 7))
    axis.plot(
        plot["event_time"],
        plot["forward_trading"] * 100,
        label="Forward trading (excl. Premium/Funding)",
        linewidth=1.15,
    )
    axis.plot(
        plot["event_time"],
        plot["forward_total"] * 100,
        label="Forward total (incl. Premium/Funding)",
        linewidth=1.15,
    )
    axis.plot(
        plot["event_time"],
        plot["reverse_trading"] * 100,
        label="Reversed trading (excl. Premium/Funding)",
        linewidth=1.15,
    )
    axis.plot(
        plot["event_time"],
        plot["reverse_total"] * 100,
        label="Reversed total (incl. Premium/Funding)",
        linewidth=1.15,
    )
    axis.axhline(0, color="black", linewidth=0.8, alpha=0.65)
    axis.set_title(f"{strategy} — strict forward vs position direction x -1")
    axis.set_xlabel("Time (UTC)")
    axis.set_ylabel("Cumulative arithmetic return (%)")
    axis.grid(alpha=0.25)
    axis.legend(loc="best")
    axis.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=5, maxticks=10))
    axis.xaxis.set_major_formatter(mdates.ConciseDateFormatter(axis.xaxis.get_major_locator()))
    figure.text(
        0.5,
        0.012,
        "$100,000 strict notional | 1x | BTCUSDT 1m | lag 1 min | "
        "slippage 0 bp | fee 0 bp | arithmetic, non-compounded",
        ha="center",
        fontsize=8,
    )
    figure.tight_layout(rect=(0, 0.035, 1, 1))
    figure.savefig(destination, dpi=160)
    plt.close(figure)


def main() -> int:
    args = parse_args()
    forward_names = discover(args.forward_root)
    reverse_names = discover(args.reverse_root)
    if forward_names != reverse_names:
        raise ValueError(
            "strategy sets differ: "
            f"forward_only={sorted(forward_names - reverse_names)}, "
            f"reverse_only={sorted(reverse_names - forward_names)}"
        )
    if args.output_dir.exists():
        if not args.overwrite:
            raise ValueError(f"output exists: {args.output_dir}")
        shutil.rmtree(args.output_dir)
    charts = args.output_dir / "charts"
    charts.mkdir(parents=True)

    rows: list[dict[str, object]] = []
    for strategy in sorted(forward_names):
        forward = read_frame(args.forward_root, strategy)
        reverse = read_frame(args.reverse_root, strategy)
        render_chart(strategy, forward, reverse, charts / f"{strategy}_direction_compare.png")
        forward_total = float(forward["total_return"].sum())
        reverse_total = float(reverse["total_return"].sum())
        forward_turnover = float(forward["turnover"].sum())
        reverse_turnover = float(reverse["turnover"].sum())
        rows.append(
            {
                "strategy": strategy,
                "forward_trading_return": float(forward["trading_return"].sum()),
                "forward_funding_return": float(forward["funding_return"].sum()),
                "forward_total_return": forward_total,
                "reverse_trading_return": float(reverse["trading_return"].sum()),
                "reverse_funding_return": float(reverse["funding_return"].sum()),
                "reverse_total_return": reverse_total,
                "forward_turnover_x": forward_turnover,
                "reverse_turnover_x": reverse_turnover,
                "turnover_match": math.isclose(
                    forward_turnover,
                    reverse_turnover,
                    rel_tol=0,
                    abs_tol=1e-9,
                ),
                "forward_breakeven_fee_bps": break_even_bps(
                    forward_total, forward_turnover
                ),
                "reverse_breakeven_fee_bps": break_even_bps(
                    reverse_total, reverse_turnover
                ),
            }
        )

    pd.DataFrame(rows).to_csv(args.output_dir / "direction_comparison.csv", index=False)
    archive = args.output_dir / f"{args.output_dir.name}.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(args.output_dir.rglob("*")):
            if path.is_file() and path != archive:
                bundle.write(path, path.relative_to(args.output_dir))
    print(f"Built direction comparison: {archive}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
