#!/usr/bin/env python3
"""
Build the constant-notional arithmetic-return deliverable.

Input layout (one file per strategy):

    <input-root>/<strategy>/timeseries.parquet
    <input-root>/<strategy>/timeseries.csv

Flat ``<strategy>.parquet`` and ``<strategy>.csv`` files are also accepted. Each
file must contain:

    event_time_ns, trading_return, funding_return, total_return, turnover

Return columns are per-bar arithmetic returns relative to the fixed capital
base. ``turnover`` is per-bar traded notional divided by that same capital base.
The builder does not rerun or alter a backtest.
"""

from __future__ import annotations

import argparse
import json
import math
import shutil
import sys
import zipfile
from dataclasses import asdict
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import matplotlib as mpl


mpl.use("Agg")

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd


if TYPE_CHECKING:
    from collections.abc import Iterable


REQUIRED_COLUMNS = (
    "event_time_ns",
    "trading_return",
    "funding_return",
    "total_return",
    "turnover",
)
INPUT_FILENAMES = ("timeseries.parquet", "timeseries.csv")
IGNORED_ROOT_FILENAMES = {
    "evaluation_table.csv",
    "features_10m.parquet",
    "indicator_manual_check.csv",
    "signals_10m.csv",
    "strategy_summary.csv",
}
RETURN_TOLERANCE = 1e-10


@dataclass(frozen=True)
class Assumptions:
    capital_usdt: float
    leverage: float
    bar_frequency: str
    lag_bars: int
    lag_minutes: float
    slippage_bps_per_fill: float
    fee_bps: float
    return_method: str = "arithmetic_non_compounded"
    notional_policy: str = "strict_constant_notional"
    funding_label: str = "Premium/Funding"
    turnover_definition: str = "traded_notional_divided_by_fixed_capital"
    breakeven_definition: str = "fee_free_total_return_divided_by_total_turnover"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input-root",
        type=Path,
        required=True,
        help="Root containing one validated timeseries parquet/csv per strategy.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="New or empty directory for the deliverable.",
    )
    parser.add_argument("--capital-usdt", type=float, default=100_000.0)
    parser.add_argument("--leverage", type=float, default=1.0)
    parser.add_argument("--bar-frequency", default="1m")
    parser.add_argument("--lag-bars", type=int, default=1)
    parser.add_argument("--lag-minutes", type=float, default=1.0)
    parser.add_argument("--slippage-bps-per-fill", type=float, default=0.0)
    parser.add_argument("--fee-bps", type=float, default=0.0)
    parser.add_argument(
        "--notional-policy",
        choices=("strict_constant_notional", "fixed_return_base_source_positions"),
        default="strict_constant_notional",
    )
    parser.add_argument(
        "--reference-fee-bps",
        type=float,
        nargs="*",
        default=[1.7, 5.0],
        help="Reference horizontal lines on the break-even chart.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace an existing output directory.",
    )
    return parser.parse_args(argv)


def validate_args(args: argparse.Namespace) -> None:
    if not args.input_root.is_dir():
        raise ValueError(f"Input root does not exist: {args.input_root}")
    if args.capital_usdt <= 0:
        raise ValueError("--capital-usdt must be positive")
    if args.leverage <= 0:
        raise ValueError("--leverage must be positive")
    if args.lag_bars < 0 or args.lag_minutes < 0:
        raise ValueError("Lag values cannot be negative")
    if args.slippage_bps_per_fill < 0 or args.fee_bps < 0:
        raise ValueError("Cost values cannot be negative")
    if any(value < 0 for value in args.reference_fee_bps):
        raise ValueError("Reference fee values cannot be negative")


def discover_inputs(root: Path) -> dict[str, Path]:
    found: dict[str, Path] = {}

    for child in sorted(root.iterdir()):
        if child.is_dir():
            matches = [child / name for name in INPUT_FILENAMES if (child / name).is_file()]
            if len(matches) > 1:
                raise ValueError(
                    f"{child.name!r} has both parquet and CSV inputs; keep exactly one"
                )
            if matches:
                found[child.name] = matches[0]

    for suffix in (".parquet", ".csv"):
        for path in sorted(root.glob(f"*{suffix}")):
            if path.name in IGNORED_ROOT_FILENAMES:
                continue
            strategy = path.stem
            if strategy in found:
                raise ValueError(f"Duplicate input for strategy {strategy!r}")
            found[strategy] = path

    if not found:
        expected = root / "<strategy>" / "timeseries.parquet"
        raise ValueError(f"No strategy inputs found; expected layout like {expected}")
    return found


def load_input_frame(path: Path) -> pd.DataFrame:
    if path.suffix.lower() == ".parquet":
        return pd.read_parquet(path, columns=list(REQUIRED_COLUMNS))
    if path.suffix.lower() == ".csv":
        return pd.read_csv(path, usecols=list(REQUIRED_COLUMNS))
    raise ValueError(f"Unsupported input type: {path}")


def read_timeseries(path: Path, strategy: str) -> pd.DataFrame:
    frame = load_input_frame(path)
    missing = set(REQUIRED_COLUMNS).difference(frame.columns)
    if missing:
        raise ValueError(f"{strategy}: missing columns: {sorted(missing)}")
    if frame.empty:
        raise ValueError(f"{strategy}: input is empty")

    frame = frame.loc[:, REQUIRED_COLUMNS].copy()
    for column in REQUIRED_COLUMNS:
        frame[column] = pd.to_numeric(frame[column], errors="raise")
    if frame[list(REQUIRED_COLUMNS)].isna().any().any():
        raise ValueError(f"{strategy}: required columns contain null values")
    if frame[list(REQUIRED_COLUMNS)].isin([math.inf, -math.inf]).any().any():
        raise ValueError(f"{strategy}: required columns contain non-finite values")
    if (frame["turnover"] < 0).any():
        raise ValueError(f"{strategy}: turnover cannot be negative")
    if frame["event_time_ns"].duplicated().any():
        raise ValueError(f"{strategy}: duplicate event_time_ns values")

    frame = frame.sort_values("event_time_ns", kind="stable").reset_index(drop=True)
    component_error = (
        frame["trading_return"] + frame["funding_return"] - frame["total_return"]
    ).abs()
    max_error = float(component_error.max())
    if max_error > RETURN_TOLERANCE:
        raise ValueError(
            f"{strategy}: total_return != trading_return + funding_return "
            f"(max error {max_error:.12g})"
        )

    frame["event_time"] = pd.to_datetime(frame["event_time_ns"], unit="ns", utc=True)
    frame["trading_simple_return"] = frame["trading_return"].cumsum()
    frame["funding_simple_return"] = frame["funding_return"].cumsum()
    frame["total_simple_return"] = frame["total_return"].cumsum()
    frame["cumulative_turnover"] = frame["turnover"].cumsum()
    return frame


def assumption_caption(assumptions: Assumptions) -> str:
    return (
        f"Capital ${assumptions.capital_usdt:,.0f} | {assumptions.leverage:g}x | "
        f"{assumptions.bar_frequency} | lag {assumptions.lag_bars} bar "
        f"({assumptions.lag_minutes:g} min) | "
        f"slippage {assumptions.slippage_bps_per_fill:g} bp/fill | "
        f"fee {assumptions.fee_bps:g} bp | arithmetic, non-compounded"
    )


def policy_title(assumptions: Assumptions) -> str:
    if assumptions.notional_policy == "strict_constant_notional":
        return "strict $100,000 position notional"
    return "$100,000 fixed return base (source position sizing)"


def render_strategy_chart(
    strategy: str,
    frame: pd.DataFrame,
    destination: Path,
    assumptions: Assumptions,
) -> None:
    plot_frame = (
        frame.set_index("event_time")[
            ["trading_simple_return", "total_simple_return"]
        ]
        .resample("1D")
        .last()
        .dropna()
        .reset_index()
    )
    figure, axis = plt.subplots(figsize=(13, 7))
    axis.plot(
        plot_frame["event_time"],
        plot_frame["trading_simple_return"] * 100.0,
        linewidth=1.25,
        label="Trading simple return (excl. Premium/Funding)",
    )
    axis.plot(
        plot_frame["event_time"],
        plot_frame["total_simple_return"] * 100.0,
        linewidth=1.25,
        label="Total simple return (incl. Premium/Funding)",
    )
    axis.axhline(0.0, color="black", linewidth=0.8, alpha=0.65)
    axis.set_title(f"{strategy} — {policy_title(assumptions)}, 1x simple return")
    axis.set_xlabel("Time (UTC)")
    axis.set_ylabel("Cumulative arithmetic return (%)")
    axis.grid(alpha=0.25)
    axis.legend(loc="best")
    axis.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=5, maxticks=10))
    axis.xaxis.set_major_formatter(mdates.ConciseDateFormatter(axis.xaxis.get_major_locator()))
    figure.text(0.5, 0.012, assumption_caption(assumptions), ha="center", fontsize=8)
    figure.tight_layout(rect=(0, 0.035, 1, 1))
    figure.savefig(destination, dpi=160)
    plt.close(figure)


def render_turnover_chart(
    strategy: str,
    frame: pd.DataFrame,
    destination: Path,
    assumptions: Assumptions,
) -> None:
    plot_frame = (
        frame.set_index("event_time")[["cumulative_turnover"]]
        .resample("1D")
        .last()
        .dropna()
        .reset_index()
    )
    figure, axis = plt.subplots(figsize=(13, 7))
    axis.plot(
        plot_frame["event_time"],
        plot_frame["cumulative_turnover"],
        linewidth=1.25,
        color="#6a3d9a",
        label="Cumulative turnover",
    )
    axis.set_title(f"{strategy} — cumulative turnover, {policy_title(assumptions)}")
    axis.set_xlabel("Time (UTC)")
    axis.set_ylabel("Cumulative turnover (x fixed capital)")
    axis.grid(alpha=0.25)
    axis.legend(loc="best")
    axis.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=5, maxticks=10))
    axis.xaxis.set_major_formatter(mdates.ConciseDateFormatter(axis.xaxis.get_major_locator()))
    figure.text(
        0.5,
        0.012,
        (
            "Turnover_t = traded notional_t / fixed capital; "
            "cumulative turnover = sum(Turnover_t) | "
            + assumption_caption(assumptions)
        ),
        ha="center",
        fontsize=8,
    )
    figure.tight_layout(rect=(0, 0.035, 1, 1))
    figure.savefig(destination, dpi=160)
    plt.close(figure)


def render_return_turnover_chart(
    strategy: str,
    frame: pd.DataFrame,
    destination: Path,
    assumptions: Assumptions,
) -> None:
    plot_frame = (
        frame.set_index("event_time")
        [["trading_simple_return", "total_simple_return", "cumulative_turnover"]]
        .resample("1D")
        .last()
        .dropna()
        .reset_index()
    )
    trading_return = float(frame["trading_return"].sum())
    total_return = float(frame["total_return"].sum())
    total_turnover = float(frame["turnover"].sum())
    trading_breakeven_bps = (
        trading_return / total_turnover * 10_000.0
        if total_turnover > 0
        else math.nan
    )
    total_breakeven_bps = (
        total_return / total_turnover * 10_000.0
        if total_turnover > 0
        else math.nan
    )

    figure, left = plt.subplots(figsize=(13, 7))
    left.plot(
        plot_frame["event_time"],
        plot_frame["trading_simple_return"] * 100.0,
        linewidth=1.2,
        label="Trading simple return (excl. Premium/Funding)",
    )
    left.plot(
        plot_frame["event_time"],
        plot_frame["total_simple_return"] * 100.0,
        linewidth=1.2,
        label="Total simple return (incl. Premium/Funding)",
    )
    left.axhline(0.0, color="black", linewidth=0.8, alpha=0.65)
    left.set_xlabel("Time (UTC)")
    left.set_ylabel("Cumulative arithmetic return (%)")
    left.grid(alpha=0.25)
    left.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=5, maxticks=10))
    left.xaxis.set_major_formatter(
        mdates.ConciseDateFormatter(left.xaxis.get_major_locator())
    )

    right = left.twinx()
    right.plot(
        plot_frame["event_time"],
        plot_frame["cumulative_turnover"],
        color="#6a3d9a",
        linestyle="--",
        linewidth=1.15,
        label="Cumulative turnover",
    )
    right.set_ylabel("Cumulative turnover (x fixed capital)")

    left_handles, left_labels = left.get_legend_handles_labels()
    right_handles, right_labels = right.get_legend_handles_labels()
    left.legend(left_handles + right_handles, left_labels + right_labels, loc="best")
    left.set_title(
        f"{strategy} — additive return and turnover | "
        f"break-even excl. Funding {trading_breakeven_bps:.4f} bp | "
        f"incl. Funding {total_breakeven_bps:.4f} bp"
    )
    figure.text(
        0.5,
        0.012,
        (
            "Break-even one-way cost (bp) = final return (%) / "
            "final turnover (x) * 100 | "
            + assumption_caption(assumptions)
        ),
        ha="center",
        fontsize=8,
    )
    figure.tight_layout(rect=(0, 0.04, 1, 1))
    figure.savefig(destination, dpi=160)
    plt.close(figure)


def summarize_strategy(
    strategy: str,
    source: Path,
    frame: pd.DataFrame,
    assumptions: Assumptions,
) -> dict[str, object]:
    trading_return = float(frame["trading_return"].sum())
    funding_return = float(frame["funding_return"].sum())
    total_return = float(frame["total_return"].sum())
    turnover = float(frame["turnover"].sum())
    breakeven_bps = total_return / turnover * 10_000.0 if turnover > 0 else math.nan
    trading_breakeven_bps = (
        trading_return / turnover * 10_000.0 if turnover > 0 else math.nan
    )
    elapsed_years = (frame["event_time"].iloc[-1] - frame["event_time"].iloc[0]).total_seconds() / (
        365.25 * 24 * 60 * 60
    )
    annual_turnover = turnover / elapsed_years if elapsed_years > 0 else math.nan

    return {
        "strategy": strategy,
        "source_path": str(source),
        "row_count": len(frame),
        "first_event_time_utc": frame["event_time"].iloc[0].isoformat(),
        "last_event_time_utc": frame["event_time"].iloc[-1].isoformat(),
        "capital_usdt": assumptions.capital_usdt,
        "leverage": assumptions.leverage,
        "trading_simple_return": trading_return,
        "funding_simple_return": funding_return,
        "total_simple_return": total_return,
        "trading_pnl_usdt": trading_return * assumptions.capital_usdt,
        "funding_pnl_usdt": funding_return * assumptions.capital_usdt,
        "total_pnl_usdt": total_return * assumptions.capital_usdt,
        "total_turnover_x": turnover,
        "annualized_turnover_x": annual_turnover,
        "breakeven_fee_bps_excl_funding": trading_breakeven_bps,
        "breakeven_fee_bps": breakeven_bps,
        "bar_frequency": assumptions.bar_frequency,
        "lag_bars": assumptions.lag_bars,
        "lag_minutes": assumptions.lag_minutes,
        "slippage_bps_per_fill": assumptions.slippage_bps_per_fill,
        "fee_bps": assumptions.fee_bps,
        "return_method": assumptions.return_method,
        "notional_policy": assumptions.notional_policy,
    }


def render_cross_strategy_chart(
    summary: pd.DataFrame,
    destination: Path,
    assumptions: Assumptions,
    reference_fee_bps: Iterable[float],
) -> None:
    valid = summary.dropna(subset=["total_turnover_x", "breakeven_fee_bps"])
    figure, axis = plt.subplots(figsize=(14, 8))
    colors = valid["breakeven_fee_bps"].ge(0).map({True: "#2b8cbe", False: "#d7301f"})
    axis.scatter(
        valid["total_turnover_x"],
        valid["breakeven_fee_bps"],
        c=colors,
        alpha=0.8,
        edgecolors="white",
        linewidths=0.4,
    )
    axis.axhline(0.0, color="black", linewidth=0.9, label="0 bp")
    for value in sorted(set(reference_fee_bps)):
        axis.axhline(
            value,
            linestyle="--",
            linewidth=1.0,
            alpha=0.8,
            label=f"Reference fee {value:g} bp",
        )

    if len(valid) <= 30:
        annotated = valid
    else:
        positive = valid.loc[valid["breakeven_fee_bps"] >= 0].nlargest(
            5, "breakeven_fee_bps"
        )
        negative = valid.nsmallest(3, "breakeven_fee_bps")
        turnover = valid.nlargest(2, "total_turnover_x")
        annotated = pd.concat([positive, negative, turnover]).drop_duplicates("strategy")
    for row in annotated.itertuples(index=False):
        axis.annotate(
            row.strategy,
            (row.total_turnover_x, row.breakeven_fee_bps),
            xytext=(4, 4),
            textcoords="offset points",
            fontsize=7,
        )

    axis.set_title(f"Turnover vs fee break-even — {policy_title(assumptions)}")
    axis.set_xlabel("Total turnover (x fixed capital)")
    axis.set_ylabel("Break-even one-way fee (bp of traded notional)")
    axis.set_xscale("log")
    axis.grid(alpha=0.25)
    axis.legend(loc="best")
    figure.text(0.5, 0.012, assumption_caption(assumptions), ha="center", fontsize=8)
    figure.tight_layout(rect=(0, 0.035, 1, 1))
    figure.savefig(destination, dpi=160)
    plt.close(figure)


def write_assumptions(path: Path, assumptions: Assumptions, strategy_count: int) -> None:
    payload = {
        **asdict(assumptions),
        "strategy_count": strategy_count,
        "input_contract": {
            "required_columns": list(REQUIRED_COLUMNS),
            "event_time_ns": "UTC Unix timestamp in nanoseconds",
            "return_columns": "per-bar decimal arithmetic returns on fixed capital",
            "turnover": "per-bar traded notional / fixed capital",
            "total_return_invariant": "trading_return + funding_return",
        },
        "chart_contract": {
            "strategy_chart_line_1": "cumsum(trading_return)",
            "strategy_chart_line_2": "cumsum(total_return)",
            "cross_strategy_x": "sum(turnover)",
            "cross_strategy_y": "sum(total_return) / sum(turnover) * 10000",
        },
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build_zip(output_dir: Path) -> Path:
    archive = output_dir / f"{output_dir.name}.zip"
    files = sorted(path for path in output_dir.rglob("*") if path.is_file() and path != archive)
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for path in files:
            bundle.write(path, path.relative_to(output_dir))
    return archive


def prepare_output(output_dir: Path, overwrite: bool) -> None:
    if output_dir.exists():
        if not overwrite:
            raise ValueError(f"Output already exists: {output_dir}; pass --overwrite to replace it")
        if output_dir.is_dir():
            shutil.rmtree(output_dir)
        else:
            output_dir.unlink()
    (output_dir / "charts").mkdir(parents=True)


def run(args: argparse.Namespace) -> Path:
    validate_args(args)
    inputs = discover_inputs(args.input_root)
    assumptions = Assumptions(
        capital_usdt=args.capital_usdt,
        leverage=args.leverage,
        bar_frequency=args.bar_frequency,
        lag_bars=args.lag_bars,
        lag_minutes=args.lag_minutes,
        slippage_bps_per_fill=args.slippage_bps_per_fill,
        fee_bps=args.fee_bps,
        notional_policy=args.notional_policy,
    )
    prepare_output(args.output_dir, args.overwrite)

    rows: list[dict[str, object]] = []
    for strategy, source in inputs.items():
        frame = read_timeseries(source, strategy)
        render_strategy_chart(
            strategy,
            frame,
            args.output_dir / "charts" / f"{strategy}_simple_return.png",
            assumptions,
        )
        render_turnover_chart(
            strategy,
            frame,
            args.output_dir / "charts" / f"{strategy}_turnover.png",
            assumptions,
        )
        render_return_turnover_chart(
            strategy,
            frame,
            args.output_dir / "charts" / f"{strategy}_return_turnover.png",
            assumptions,
        )
        rows.append(summarize_strategy(strategy, source, frame, assumptions))

    summary = pd.DataFrame(rows).sort_values("strategy").reset_index(drop=True)
    summary.to_csv(args.output_dir / "strategy_summary.csv", index=False)
    render_cross_strategy_chart(
        summary,
        args.output_dir / "charts" / "turnover_vs_breakeven_bps.png",
        assumptions,
        args.reference_fee_bps,
    )
    write_assumptions(args.output_dir / "assumptions.json", assumptions, len(inputs))
    return build_zip(args.output_dir)


def main(argv: list[str] | None = None) -> int:
    try:
        archive = run(parse_args(argv))
    except (OSError, ValueError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"Built deliverable: {archive}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
