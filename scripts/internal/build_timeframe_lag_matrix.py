#!/usr/bin/env python3
"""
Build one return-plus-turnover figure per direction across timeframe/lag cases.
"""

from __future__ import annotations

import argparse
import json
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
    parser.add_argument(
        "--batch",
        action="append",
        required=True,
        help="Case/path pair, e.g. 1m_lag0=D:\\nautilus\\outputs\\...",
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def parse_batches(values: list[str]) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for value in values:
        label, separator, path_text = value.partition("=")
        if not separator or label in result:
            raise ValueError(f"invalid or duplicate --batch: {value}")
        root = Path(path_text)
        if not (root / "evaluation_table.csv").is_file():
            raise ValueError(f"missing evaluation table: {root}")
        result[label] = root
    expected = {"1m_lag0", "1m_lag1", "10m_lag0", "10m_lag1"}
    if set(result) != expected:
        raise ValueError(f"expected cases {sorted(expected)}, got {sorted(result)}")
    return result


def discover_variants(root: Path) -> dict[str, Path]:
    found: dict[str, Path] = {}
    for path in root.glob("ma_crossover_*_*/timeseries.parquet"):
        strategy = path.parent.name
        marker = "_long_only"
        if strategy.endswith(marker):
            variant = "long_only"
        elif strategy.endswith("_short_only"):
            variant = "short_only"
        elif strategy.endswith("_reverse_long_short"):
            variant = "reverse_long_short"
        elif strategy.endswith("_long_short"):
            variant = "long_short"
        else:
            continue
        found[variant] = path
    return found


def read_frame(path: Path) -> pd.DataFrame:
    frame = pd.read_parquet(path, columns=list(COLUMNS))
    frame["event_time"] = pd.to_datetime(frame["event_time_ns"], unit="ns", utc=True)
    frame["trading_cumulative"] = frame["trading_return"].cumsum()
    frame["total_cumulative"] = frame["total_return"].cumsum()
    frame["turnover_cumulative"] = frame["turnover"].cumsum()
    return frame


def signed_bps(return_value: float, turnover: float) -> float:
    return return_value / turnover * 10_000.0 if turnover > 0 else math.nan


def summary_row(label: str, variant: str, frame: pd.DataFrame, source: Path) -> dict:
    trading = float(frame["trading_return"].sum())
    funding = float(frame["funding_return"].sum())
    total = float(frame["total_return"].sum())
    turnover = float(frame["turnover"].sum())
    timeframe, lag_text = label.split("_")
    return {
        "case": label,
        "strategy_bar_frequency": timeframe,
        "execution_lag_minutes": int(lag_text.removeprefix("lag")),
        "direction_variant": variant,
        "trading_arithmetic_return": trading,
        "funding_arithmetic_return": funding,
        "total_arithmetic_return": total,
        "total_turnover_x": turnover,
        "signed_breakeven_bps_excl_funding": signed_bps(trading, turnover),
        "signed_breakeven_bps_incl_funding": signed_bps(total, turnover),
        "source_timeseries": str(source),
    }


def render_variant(
    variant: str,
    cases: dict[str, tuple[pd.DataFrame, dict]],
    destination: Path,
) -> None:
    order = ("1m_lag0", "1m_lag1", "10m_lag0", "10m_lag1")
    figure, axes = plt.subplots(2, 2, figsize=(18, 11), sharex=True)
    for axis, label in zip(axes.flat, order, strict=True):
        frame, summary = cases[label]
        daily = (
            frame.set_index("event_time")
            [["trading_cumulative", "total_cumulative", "turnover_cumulative"]]
            .resample("1D")
            .last()
            .dropna()
            .reset_index()
        )
        axis.plot(
            daily["event_time"],
            daily["trading_cumulative"] * 100.0,
            linewidth=1.0,
            label="Return excl. Premium/Funding",
        )
        axis.plot(
            daily["event_time"],
            daily["total_cumulative"] * 100.0,
            linewidth=1.0,
            label="Return incl. Premium/Funding",
        )
        axis.axhline(0.0, color="black", linewidth=0.7, alpha=0.6)
        axis.set_ylabel("Cumulative arithmetic return (%)")
        axis.grid(alpha=0.22)
        axis.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=4, maxticks=7))
        axis.xaxis.set_major_formatter(
            mdates.ConciseDateFormatter(axis.xaxis.get_major_locator())
        )
        right = axis.twinx()
        right.plot(
            daily["event_time"],
            daily["turnover_cumulative"],
            color="#6a3d9a",
            linestyle="--",
            linewidth=0.95,
            label="Cumulative turnover",
        )
        right.set_ylabel("Cumulative turnover (x fixed capital)")
        axis.set_title(
            f"{label.replace('_', ' ')} | signed BE excl. Funding "
            f"{summary['signed_breakeven_bps_excl_funding']:.4f} bp | "
            f"incl. Funding {summary['signed_breakeven_bps_incl_funding']:.4f} bp"
        )
        if label == "1m_lag0":
            handles, labels = axis.get_legend_handles_labels()
            right_handles, right_labels = right.get_legend_handles_labels()
            axis.legend(handles + right_handles, labels + right_labels, loc="best")
    figure.suptitle(
        f"MA crossover — {variant} | return + turnover | signed break-even bps",
        fontsize=15,
    )
    figure.text(
        0.5,
        0.012,
        "Signed break-even bp = arithmetic return / turnover x 10,000 | "
        "$100,000 strict notional | 1x | slippage 0 bp | fee 0 bp | no compounding",
        ha="center",
        fontsize=9,
    )
    figure.tight_layout(rect=(0, 0.035, 1, 0.96))
    figure.savefig(destination, dpi=160)
    plt.close(figure)


def main() -> int:
    args = parse_args()
    batches = parse_batches(args.batch)
    variants_by_case = {label: discover_variants(root) for label, root in batches.items()}
    expected_variants = {"long_only", "short_only", "long_short", "reverse_long_short"}
    for label, found in variants_by_case.items():
        if set(found) != expected_variants:
            raise ValueError(f"{label}: direction variants differ: {sorted(found)}")

    if args.output_dir.exists():
        if not args.overwrite:
            raise ValueError(f"output exists: {args.output_dir}")
        shutil.rmtree(args.output_dir)
    charts = args.output_dir / "charts"
    charts.mkdir(parents=True)

    rows: list[dict] = []
    for variant in sorted(expected_variants):
        cases: dict[str, tuple[pd.DataFrame, dict]] = {}
        for label, paths in variants_by_case.items():
            frame = read_frame(paths[variant])
            summary = summary_row(label, variant, frame, paths[variant])
            rows.append(summary)
            cases[label] = (frame, summary)
        render_variant(variant, cases, charts / f"{variant}_return_turnover_matrix.png")

    summary_frame = pd.DataFrame(rows).sort_values(
        ["direction_variant", "strategy_bar_frequency", "execution_lag_minutes"]
    )
    summary_frame.to_csv(args.output_dir / "signed_bps_summary.csv", index=False)
    (args.output_dir / "formula.json").write_text(
        json.dumps(
            {
                "turnover": "sum(abs(delta_quantity) * fill_price) / 100000",
                "signed_breakeven_bps": "arithmetic_return / turnover * 10000",
                "negative_bps": "no positive transaction cost is supportable",
                "absolute_value_applied": False,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    archive = args.output_dir / f"{args.output_dir.name}.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(args.output_dir.rglob("*")):
            if path.is_file() and path != archive:
                bundle.write(path, path.relative_to(args.output_dir))
    print(f"Built matrix deliverable: {archive}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
