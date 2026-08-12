#!/usr/bin/env python3
"""Build per-strategy return/turnover and fee comparison deliverables."""

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

from results.strategy_evaluation import (
    build_additive_strategy_evaluation_from_columns,
    render_additive_strategy_evaluation,
    validate_strategy_evaluation,
)


CASES = ("1m_lag0", "1m_lag1", "10m_lag0", "10m_lag1")
VARIANTS = ("normal", "strict_reverse")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--blocked-strategy", action="append", default=[])
    parser.add_argument("--canonical-layout", action="store_true")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--source-config-root", type=Path)
    parser.add_argument("--strategy", action="append", dest="strategies")
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def discover(batch_root: Path) -> list[str]:
    return sorted(
        path.name
        for path in batch_root.iterdir()
        if path.is_dir()
        and all((path / case / "timeseries.parquet").is_file() for case in CASES)
        and all((path / case / "summary.json").is_file() for case in CASES)
    )


def read_case(strategy_root: Path, case: str) -> tuple[pd.DataFrame, dict]:
    columns = ["event_time_ns"]
    for variant in VARIANTS:
        columns.extend(
            [
                f"{variant}_trading_return",
                f"{variant}_funding_return",
                f"{variant}_total_return",
                f"{variant}_turnover",
                f"{variant}_vip9_total_return",
                f"{variant}_vip0_total_return",
            ]
        )
    frame = pd.read_parquet(strategy_root / case / "timeseries.parquet", columns=columns)
    frame["event_time"] = pd.to_datetime(frame["event_time_ns"], unit="ns", utc=True)
    summary = json.loads((strategy_root / case / "summary.json").read_text(encoding="utf-8"))
    return frame, summary


def daily_cumulative(frame: pd.DataFrame) -> pd.DataFrame:
    values = frame.drop(columns=["event_time_ns"]).copy()
    numeric = [column for column in values if column != "event_time"]
    values[numeric] = values[numeric].cumsum()
    return values.set_index("event_time").resample("1D").last().dropna().reset_index()


def format_bps(value: float | None) -> str:
    if value is None or not math.isfinite(float(value)):
        return "n/a"
    return f"{float(value):.4f}"


def render_return_turnover(
    strategy: str, cases: dict[str, tuple[pd.DataFrame, dict]], destination: Path
) -> None:
    figure, axes = plt.subplots(2, 2, figsize=(19, 11), sharex=True)
    for axis, case in zip(axes.flat, CASES, strict=True):
        frame, summary = cases[case]
        daily = daily_cumulative(frame)
        colors = {"normal": "#1f77b4", "strict_reverse": "#d62728"}
        labels = {"normal": "Normal", "strict_reverse": "Strict reverse"}
        for variant in VARIANTS:
            axis.plot(
                daily["event_time"],
                daily[f"{variant}_trading_return"] * 100.0,
                color=colors[variant],
                linestyle=":",
                linewidth=0.9,
                alpha=0.75,
                label=f"{labels[variant]} excl. Funding",
            )
            axis.plot(
                daily["event_time"],
                daily[f"{variant}_total_return"] * 100.0,
                color=colors[variant],
                linewidth=1.2,
                label=f"{labels[variant]} incl. Funding",
            )
        right = axis.twinx()
        right.plot(
            daily["event_time"],
            daily["normal_turnover"],
            color="#6a3d9a",
            linestyle="--",
            linewidth=1.0,
            label="Cumulative turnover",
        )
        normal_bps = summary["normal"]["breakeven_fee_bps"]
        reverse_bps = summary["strict_reverse"]["breakeven_fee_bps"]
        axis.set_title(
            f"{case.replace('_', ' ')} | signed BE normal {format_bps(normal_bps)} bp | "
            f"reverse {format_bps(reverse_bps)} bp"
        )
        axis.set_ylabel("Cumulative arithmetic return (%)")
        right.set_ylabel("Cumulative turnover (x capital)")
        axis.axhline(0.0, color="black", linewidth=0.7, alpha=0.6)
        axis.grid(alpha=0.22)
        axis.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=4, maxticks=7))
        axis.xaxis.set_major_formatter(
            mdates.ConciseDateFormatter(axis.xaxis.get_major_locator())
        )
        if case == "1m_lag0":
            handles, labels_ = axis.get_legend_handles_labels()
            right_handles, right_labels = right.get_legend_handles_labels()
            axis.legend(handles + right_handles, labels_ + right_labels, loc="best", fontsize=8)
    figure.suptitle(f"{strategy} — return + turnover — normal vs strict reverse", fontsize=15)
    figure.text(
        0.5,
        0.012,
        "Signed break-even bp = arithmetic return / turnover x 10,000 (sign retained) | "
        "$100,000 strict notional | 1x | 1m execution clock | slippage 0 bp | "
        "fee 0 bp | no compounding",
        ha="center",
        fontsize=9,
    )
    figure.tight_layout(rect=(0, 0.035, 1, 0.96))
    figure.savefig(destination, dpi=150)
    plt.close(figure)


def render_fee_comparison(
    strategy: str, cases: dict[str, tuple[pd.DataFrame, dict]], destination: Path
) -> None:
    figure, axes = plt.subplots(2, 2, figsize=(19, 11), sharex=True)
    palette = {
        ("normal", "fee0"): "#1f77b4",
        ("normal", "vip9"): "#2ca02c",
        ("normal", "vip0"): "#17becf",
        ("strict_reverse", "fee0"): "#d62728",
        ("strict_reverse", "vip9"): "#ff7f0e",
        ("strict_reverse", "vip0"): "#9467bd",
    }
    for axis, case in zip(axes.flat, CASES, strict=True):
        frame, summary = cases[case]
        daily = daily_cumulative(frame)
        for variant in VARIANTS:
            label_prefix = "Normal" if variant == "normal" else "Reverse"
            for fee_name, column in (
                ("fee0", f"{variant}_total_return"),
                ("vip9", f"{variant}_vip9_total_return"),
                ("vip0", f"{variant}_vip0_total_return"),
            ):
                axis.plot(
                    daily["event_time"],
                    daily[column] * 100.0,
                    color=palette[(variant, fee_name)],
                    linewidth=1.0,
                    label=f"{label_prefix} {fee_name.upper()}",
                )
        axis.set_title(case.replace("_", " "))
        axis.set_ylabel("Cumulative arithmetic return (%)")
        axis.axhline(0.0, color="black", linewidth=0.7, alpha=0.6)
        axis.grid(alpha=0.22)
        axis.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=4, maxticks=7))
        axis.xaxis.set_major_formatter(
            mdates.ConciseDateFormatter(axis.xaxis.get_major_locator())
        )
        if case == "1m_lag0":
            axis.legend(loc="best", fontsize=8)
    figure.suptitle(f"{strategy} — fee comparison — normal vs strict reverse", fontsize=15)
    figure.text(
        0.5,
        0.012,
        "Fee0 / VIP9 taker 1.7 bp / VIP0 taker 5.0 bp | Funding included | "
        "$100,000 strict notional | 1x | slippage 0 bp | arithmetic, non-compounded",
        ha="center",
        fontsize=9,
    )
    figure.tight_layout(rect=(0, 0.035, 1, 0.96))
    figure.savefig(destination, dpi=150)
    plt.close(figure)


def summary_rows(strategy: str, case: str, summary: dict) -> list[dict]:
    rows: list[dict] = []
    timeframe, lag_text = case.split("_")
    for variant in VARIANTS:
        values = summary[variant]
        turnover = float(values["total_turnover_x"])
        trading = float(values["trading_simple_return"])
        funding = float(values["funding_simple_return"])
        total = float(values["total_simple_return_fee0"])
        rows.append(
            {
                "strategy": strategy,
                "case": case,
                "strategy_bar_frequency": timeframe,
                "execution_lag_minutes": int(lag_text.removeprefix("lag")),
                "variant": variant,
                "trading_arithmetic_return": trading,
                "funding_arithmetic_return": funding,
                "total_arithmetic_return_fee0": total,
                "total_arithmetic_return_vip9": values["total_simple_return_vip9"],
                "total_arithmetic_return_vip0": values["total_simple_return_vip0"],
                "total_turnover_x": turnover,
                "signed_breakeven_bps_excl_funding": (
                    trading / turnover * 10_000.0 if turnover > 0 else math.nan
                ),
                "signed_breakeven_bps_incl_funding": (
                    total / turnover * 10_000.0 if turnover > 0 else math.nan
                ),
                "signal_count": values["signal_count"],
                "execution_fill_count": values["execution_fill_count"],
                "funding_event_count": values["funding_event_count"],
                "max_boundary_notional_error_usdt": values[
                    "max_boundary_notional_error_usdt"
                ],
                "source_timeseries": str(
                    Path(strategy) / case / "timeseries.parquet"
                ),
            }
        )
    return rows


def _atomic_json(path: Path, value: dict) -> None:
    def safe(item):
        if isinstance(item, dict):
            return {key: safe(child) for key, child in item.items()}
        if isinstance(item, list):
            return [safe(child) for child in item]
        if isinstance(item, float) and not math.isfinite(item):
            return None
        return item

    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(safe(value), indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def build_canonical_case(
    *,
    batch_root: Path,
    output_dir: Path,
    source_config_root: Path | None,
    strategy: str,
    symbol: str,
    case: str,
) -> list[dict]:
    frequency, lag_text = case.split("_", 1)
    lag_minutes = int(lag_text.removeprefix("lag"))
    source = batch_root / strategy / case / "timeseries.parquet"
    frame = pd.read_parquet(
        source,
        columns=[
            "event_time_ns",
            "normal_direction",
            "normal_trading_return",
            "normal_funding_return",
            "normal_turnover",
        ],
    )
    series, metrics = build_additive_strategy_evaluation_from_columns(
        event_time_ns=frame["event_time_ns"].to_numpy(copy=False),
        trading_return=frame["normal_trading_return"].to_numpy(copy=False),
        funding_return=frame["normal_funding_return"].to_numpy(copy=False),
        turnover=frame["normal_turnover"].to_numpy(copy=False),
        executed_direction=frame["normal_direction"].to_numpy(copy=False),
    )
    validation = validate_strategy_evaluation(series, metrics, tolerance=1e-9)
    case_dir = output_dir / strategy / symbol / frequency / f"lag{lag_minutes}m"
    figure_name = f"{symbol}_{frequency}_lag{lag_minutes}m_performance.png"
    figure = render_additive_strategy_evaluation(
        series,
        metrics,
        destination=case_dir / figure_name,
        run_name=f"{strategy}/{symbol}/{frequency} bar",
        lag_label=f"{lag_minutes} minute additional execution lag",
    )
    source_summary = json.loads(
        (batch_root / strategy / case / "summary.json").read_text(encoding="utf-8")
    )["normal"]
    metadata = {
        "strategy": strategy,
        "symbol": symbol,
        "granularity": f"{frequency} bar",
        "lag": f"{lag_minutes}m additional execution lag",
        "lag_minutes": lag_minutes,
        "premium_definition": "funding_return",
        "turnover_definition": "sum(abs(delta_quantity) * fill_price) / 100000",
        "cost_equation": "net_return = return - turnover * cost_bps / 10000",
        "leverage": 1.0,
        "position_definition": "executed direction expressed as signed leverage percent",
        "source_timeseries": str(source.resolve()),
        "source_summary": str((batch_root / strategy / case / "summary.json").resolve()),
        "source_row_count": int(len(frame)),
        "start_time": source_summary["first_event_time_utc"],
        "end_time": source_summary["last_event_time_utc"],
        "cases": metrics,
        "validation": validation,
        "figure": str(Path(figure).resolve()),
    }
    case_dir.mkdir(parents=True, exist_ok=True)
    _atomic_json(case_dir / "metrics.json", metadata)
    if source_config_root is not None:
        config = source_config_root / strategy / "fee_5bps" / "config.yaml"
        if config.is_file():
            shutil.copy2(config, case_dir / "config.yaml")
            metadata["source_config"] = str(config.resolve())
            _atomic_json(case_dir / "metrics.json", metadata)
    rows: list[dict] = []
    for premium in ("included", "excluded"):
        values = metrics[premium]
        rows.append(
            {
                "strategy": strategy,
                "symbol": symbol,
                "granularity": f"{frequency} bar",
                "lag": f"{lag_minutes}m additional execution lag",
                "premium": premium,
                "final_return_1x": values["final_return_1x"],
                "turnover": values["turnover"],
                "break_even_bps": values["break_even_bps"],
                "max_drawdown": values["max_drawdown"],
                "start_time": source_summary["first_event_time_utc"],
                "end_time": source_summary["last_event_time_utc"],
                "source_timeseries": str(source.resolve()),
                "figure": str(Path(figure).resolve()),
            }
        )
    return rows


def build_canonical(args: argparse.Namespace, strategies: list[str]) -> None:
    args.output_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    for strategy in strategies:
        for case in CASES:
            rows.extend(
                build_canonical_case(
                    batch_root=args.batch_root,
                    output_dir=args.output_dir,
                    source_config_root=args.source_config_root,
                    strategy=strategy,
                    symbol=args.symbol,
                    case=case,
                )
            )
        print(f"CANONICAL_COMPLETE {strategy}", flush=True)
    summary = pd.DataFrame(rows).sort_values(
        ["strategy", "granularity", "lag", "premium"]
    )
    summary.to_csv(args.output_dir / "canonical_summary.csv", index=False)
    (args.output_dir / "canonical_summary.html").write_text(
        summary.to_html(index=False, float_format=lambda value: f"{value:.8f}"),
        encoding="utf-8",
    )
    _atomic_json(
        args.output_dir / "artifact_manifest.json",
        {
            "strategy_count": len(strategies),
            "case_count": len(CASES),
            "premium_case_count": 2,
            "summary_rows": len(summary),
            "figure_count": len(strategies) * len(CASES),
            "primary_variant": "normal",
            "reverse_variant_regenerated": False,
            "reverse_reason": "strict_reverse is an exact sign inversion of executed direction",
            "batch_root": str(args.batch_root.resolve()),
        },
    )


def main() -> int:
    args = parse_args()
    strategies = discover(args.batch_root)
    if not strategies:
        raise ValueError("no complete strategies found")
    if args.strategies:
        missing = sorted(set(args.strategies) - set(strategies))
        if missing:
            raise ValueError(f"strategies unavailable: {missing}")
        strategies = sorted(set(args.strategies))
    if args.canonical_layout:
        if args.output_dir.exists() and any(args.output_dir.iterdir()) and not args.overwrite:
            raise ValueError(f"output exists: {args.output_dir}")
        build_canonical(args, strategies)
        print(
            f"COMPLETE canonical strategies={len(strategies)} "
            f"figures={len(strategies) * len(CASES)} output={args.output_dir}"
        )
        return 0
    if args.output_dir.exists():
        if not args.overwrite:
            raise ValueError(f"output exists: {args.output_dir}")
        shutil.rmtree(args.output_dir)
    charts = args.output_dir / "strategies"
    charts.mkdir(parents=True)
    rows: list[dict] = []
    for strategy in strategies:
        destination = charts / strategy
        destination.mkdir()
        cases = {
            case: read_case(args.batch_root / strategy, case) for case in CASES
        }
        render_return_turnover(
            strategy, cases, destination / "return_turnover_matrix.png"
        )
        render_fee_comparison(
            strategy, cases, destination / "fee_comparison_matrix.png"
        )
        for case, (_, summary) in cases.items():
            rows.extend(summary_rows(strategy, case, summary))
    evaluation = pd.DataFrame(rows).sort_values(
        ["strategy", "strategy_bar_frequency", "execution_lag_minutes", "variant"]
    )
    evaluation.to_csv(args.output_dir / "evaluation_table.csv", index=False)
    pd.DataFrame(
        [{"strategy": value, "status": "blocked"} for value in args.blocked_strategy]
    ).to_csv(args.output_dir / "blocked_strategies.csv", index=False)
    (args.output_dir / "formula.json").write_text(
        json.dumps(
            {
                "arithmetic_return": "sum(per_minute_return)",
                "turnover": "sum(abs(delta_quantity) * fill_price) / 100000",
                "signed_breakeven_bps": "arithmetic_return / turnover * 10000",
                "absolute_value_applied": False,
                "negative_bps": "no positive transaction cost is supportable",
                "vip9_taker_bps": 1.7,
                "vip0_taker_bps": 5.0,
                "slippage_bps": 0.0,
                "notional_usdt": 100000.0,
                "leverage": 1.0,
                "compounding": False,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (args.output_dir / "artifact_manifest.json").write_text(
        json.dumps(
            {
                "strategy_count": len(strategies),
                "case_count": len(CASES),
                "variant_count": len(VARIANTS),
                "evaluation_rows": len(evaluation),
                "chart_count": len(strategies) * 2,
                "batch_root": str(args.batch_root),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    archive = args.output_dir / "all_strategies_timeframe_lag_boss_bundle.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(args.output_dir.rglob("*")):
            if path.is_file() and path != archive:
                bundle.write(path, path.relative_to(args.output_dir))
    print(
        f"COMPLETE strategies={len(strategies)} rows={len(evaluation)} "
        f"charts={len(strategies) * 2} archive={archive}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
