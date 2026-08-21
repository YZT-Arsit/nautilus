#!/usr/bin/env python3
"""Build per-strategy return/turnover and fee comparison deliverables."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import math
import re
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
from results.trade_episode import (
    build_de_risk_episodes,
    render_episode_break_even,
    write_episode_csv,
)


CASES = ("1m_lag0", "1m_lag1", "10m_lag0", "10m_lag1")
SOURCE_VARIANTS = ("normal", "long_only", "short_only", "strict_reverse")
REPORT_VARIANTS = {
    "normal": "original",
    "long_only": "long_only",
    "short_only": "short_only",
    "strict_reverse": "strict_reverse",
}
VARIANTS = SOURCE_VARIANTS


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--blocked-strategy", action="append", default=[])
    parser.add_argument("--canonical-layout", action="store_true")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--source-config-root", type=Path)
    parser.add_argument("--strategy", action="append", dest="strategies")
    parser.add_argument(
        "--case", action="append", dest="cases",
        help="Case directory to render, e.g. 1m_lag0 (repeatable; defaults to legacy four).",
    )
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Independent strategy render workers (default: 1).",
    )
    return parser.parse_args()


def discover(batch_root: Path, cases: tuple[str, ...] = CASES) -> list[str]:
    return sorted(
        path.name
        for path in batch_root.iterdir()
        if path.is_dir()
        and all((path / case / "timeseries.parquet").is_file() for case in cases)
        and all((path / case / "summary.json").is_file() for case in cases)
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
        colors = {
            "normal": "#1f77b4",
            "long_only": "#2ca02c",
            "short_only": "#ff7f0e",
            "strict_reverse": "#d62728",
        }
        labels = {
            "normal": "Original",
            "long_only": "Long only",
            "short_only": "Short only",
            "strict_reverse": "Strict reverse",
        }
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
        ("long_only", "fee0"): "#2ca02c",
        ("long_only", "vip9"): "#98df8a",
        ("long_only", "vip0"): "#17becf",
        ("short_only", "fee0"): "#ff7f0e",
        ("short_only", "vip9"): "#ffbb78",
        ("short_only", "vip0"): "#bcbd22",
        ("strict_reverse", "fee0"): "#d62728",
        ("strict_reverse", "vip9"): "#ff7f0e",
        ("strict_reverse", "vip0"): "#9467bd",
    }
    for axis, case in zip(axes.flat, CASES, strict=True):
        frame, summary = cases[case]
        daily = daily_cumulative(frame)
        for variant in VARIANTS:
            label_prefix = {
                "normal": "Original",
                "long_only": "Long only",
                "short_only": "Short only",
                "strict_reverse": "Reverse",
            }[variant]
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
) -> tuple[list[dict], list[dict]]:
    frequency, lag_text = case.split("_", 1)
    lag_minutes = int(lag_text.removeprefix("lag"))
    source_root = batch_root / strategy / case
    source = source_root / "timeseries.parquet"
    columns = ["event_time_ns"]
    for variant in SOURCE_VARIANTS:
        columns.extend(
            [
                f"{variant}_direction",
                f"{variant}_trading_return",
                f"{variant}_funding_return",
                f"{variant}_turnover",
            ]
        )
    frame = pd.read_parquet(source, columns=columns)
    source_summaries = json.loads(
        (source_root / "summary.json").read_text(encoding="utf-8")
    )
    direction_rows = json.loads(
        (source_root / "direction_validation.json").read_text(encoding="utf-8")
    )
    direction_by_variant = {row["variant"]: row for row in direction_rows}
    output_rows: list[dict] = []

    for source_variant in SOURCE_VARIANTS:
        variant = REPORT_VARIANTS[source_variant]
        direction_validation = direction_by_variant[variant]
        if not direction_validation["direction_validation_passed"]:
            raise AssertionError(f"direction validation failed: {strategy} {case} {variant}")
        prefix = f"{source_variant}_"
        times = frame["event_time_ns"].to_numpy(copy=False)
        trading = frame[f"{prefix}trading_return"].to_numpy(copy=False)
        funding = frame[f"{prefix}funding_return"].to_numpy(copy=False)
        turnover = frame[f"{prefix}turnover"].to_numpy(copy=False)
        executed_direction = frame[f"{prefix}direction"].to_numpy(copy=False)
        series, metrics = build_additive_strategy_evaluation_from_columns(
            event_time_ns=times,
            trading_return=trading,
            funding_return=funding,
            turnover=turnover,
            executed_direction=executed_direction,
        )
        validation = validate_strategy_evaluation(series, metrics, tolerance=1e-9)
        case_dir = (
            output_dir
            / strategy
            / symbol
            / frequency
            / f"lag{lag_minutes}m"
            / variant
        )
        figure_name = (
            f"{symbol}_{frequency}_lag{lag_minutes}m_{variant}_performance.png"
        )
        figure = render_additive_strategy_evaluation(
            series,
            metrics,
            destination=case_dir / figure_name,
            run_name=(
                f"{strategy}/{variant}/{symbol}/{frequency} bar"
                f" [{source_summaries[source_variant].get('semantic_provenance', 'SOURCE_EXACT')}]"
            ),
            lag_label=f"{lag_minutes} minute additional execution lag",
        )
        case_dir.mkdir(parents=True, exist_ok=True)
        evaluation_path = case_dir / "strategy_evaluation.parquet"
        temporary_evaluation = evaluation_path.with_suffix(".parquet.tmp")
        pd.DataFrame(series).to_parquet(
            temporary_evaluation, index=False, compression="zstd"
        )
        temporary_evaluation.replace(evaluation_path)

        episode_rows: list[dict] = []
        episode_summaries: dict[str, dict] = {}
        for premium, gross_return in (
            ("included", trading + funding),
            ("excluded", trading),
        ):
            premium_rows, premium_summary = build_de_risk_episodes(
                event_time_ns=times,
                executed_position=executed_direction,
                turnover_increment=turnover,
                gross_return_increment=gross_return,
                strategy=strategy,
                symbol=symbol,
                granularity=f"{frequency} bar",
                lag=f"{lag_minutes}m physical-time",
                premium_mode=premium,
                variant=variant,
            )
            episode_rows.extend(premium_rows)
            episode_summaries[premium] = premium_summary
        episode_table = write_episode_csv(
            case_dir / "per_trade_break_even.csv", episode_rows
        )
        episode_figure = render_episode_break_even(
            episode_rows,
            destination=(
                case_dir
                / f"{symbol}_{frequency}_lag{lag_minutes}m_{variant}_per_trade_be.png"
            ),
            title=(
                f"{strategy}/{variant}/{symbol}/{frequency} bar — "
                f"Per-Episode Break-even Cost (lag={lag_minutes}m physical-time)"
            ),
        )
        _atomic_json(
            case_dir / "per_trade_break_even_summary.json", episode_summaries
        )
        source_summary = source_summaries[source_variant]
        metadata = {
            "strategy": strategy,
            "variant": variant,
            "source_variant": source_variant,
            "symbol": symbol,
            "granularity": f"{frequency} bar",
            "lag": f"{lag_minutes}m additional execution lag",
            "lag_minutes": lag_minutes,
            "premium_definition": "funding_return",
            "turnover_definition": "sum(abs(delta_quantity) * fill_price) / 100000",
            "cost_equation": "net_return = return - turnover * cost_bps / 10000",
            "episode_reversal_decomposition": (
                "aggregate position reversal turnover is split in proportion to "
                "closing and opening absolute exposure; opening turnover is not "
                "charged to the completed episode"
            ),
            "leverage": 1.0,
            "position_definition": "executed direction expressed as signed leverage percent",
            "semantic_provenance": source_summary.get("semantic_provenance", "SOURCE_EXACT"),
            "contracts_applied": source_summary.get("contracts_applied", ""),
            "defaulted_parameters": source_summary.get("defaulted_parameters", ""),
            "source_timeseries": str(source.resolve()),
            "source_summary": str((source_root / "summary.json").resolve()),
            "source_direction_validation": str(
                (source_root / "direction_validation.json").resolve()
            ),
            "source_row_count": int(len(frame)),
            "start_time": source_summary["first_event_time_utc"],
            "end_time": source_summary["last_event_time_utc"],
            "cases": metrics,
            "validation": validation,
            "direction_validation": direction_validation,
            "figure": str(Path(figure).resolve()),
            "strategy_evaluation": str(evaluation_path.resolve()),
            "per_trade_break_even_table": str(Path(episode_table).resolve()),
            "per_trade_break_even_figure": str(Path(episode_figure).resolve()),
            "per_trade_break_even_summary": episode_summaries,
        }
        if source_config_root is not None:
            legacy_config = source_config_root / strategy / "fee_5bps" / "config.yaml"
            canonical_config = source_config_root / strategy / "config.yaml"
            config = legacy_config if legacy_config.is_file() else canonical_config
            if config.is_file():
                shutil.copy2(config, case_dir / "config.yaml")
                metadata["source_config"] = str(config.resolve())
        _atomic_json(case_dir / "metrics.json", metadata)

        for premium in ("included", "excluded"):
            values = metrics[premium]
            episode_summary = episode_summaries[premium]
            global_residual = (
                abs(
                    float(values["final_return_1x"])
                    - float(values["turnover"])
                    * float(values["break_even_bps"])
                    / 10_000.0
                )
                if float(values["turnover"]) > 0.0
                else abs(float(values["final_return_1x"]))
            )
            output_rows.append(
                {
                    "strategy": strategy,
                    "semantic_provenance": source_summary.get("semantic_provenance", "SOURCE_EXACT"),
                    "contracts_applied": source_summary.get("contracts_applied", ""),
                    "defaulted_parameters": source_summary.get("defaulted_parameters", ""),
                    "defaulted_parameter_count": (
                        0 if not source_summary.get("defaulted_parameters")
                        else len(str(source_summary["defaulted_parameters"]).split(";"))
                    ),
                    "symbol": symbol,
                    "timeframe": frequency,
                    "granularity": f"{frequency} bar",
                    "lag": f"{lag_minutes}m additional execution lag",
                    "lag_minutes": lag_minutes,
                    "variant": variant,
                    "premium": premium,
                    "final_return_1x": values["final_return_1x"],
                    "turnover": values["turnover"],
                    "global_BE_bps": values["break_even_bps"],
                    "break_even_bps": values["break_even_bps"],
                    "max_drawdown": values["max_drawdown"],
                    "trade_count": episode_summary["completed_episode_count"],
                    "median_trade_BE_bps": episode_summary["break_even_bps_median"],
                    "mean_trade_BE_bps": episode_summary["break_even_bps_mean"],
                    "global_BE_validation_residual": global_residual,
                    "per_trade_BE_validation_residual": episode_summary[
                        "maximum_break_even_residual"
                    ],
                    "direction_validation_passed": direction_validation[
                        "direction_validation_passed"
                    ],
                    "max_direction_residual": direction_validation[
                        "max_direction_residual"
                    ],
                    "start_time": source_summary["first_event_time_utc"],
                    "end_time": source_summary["last_event_time_utc"],
                    "source_timeseries": str(source.resolve()),
                    "figure": str(Path(figure).resolve()),
                    "figure_relative": Path(figure).relative_to(output_dir).as_posix(),
                    "per_trade_BE_figure": str(Path(episode_figure).resolve()),
                    "per_trade_BE_figure_relative": Path(episode_figure).relative_to(
                        output_dir
                    ).as_posix(),
                    "per_trade_BE_table": str(Path(episode_table).resolve()),
                }
            )
    return output_rows, direction_rows


def build_canonical_strategy(
    *,
    batch_root: Path,
    output_dir: Path,
    source_config_root: Path | None,
    strategy: str,
    symbol: str,
    cases: tuple[str, ...],
) -> tuple[str, list[dict], list[dict]]:
    """Render one strategy independently so reporting can scale by strategy."""
    rows: list[dict] = []
    direction_rows: list[dict] = []
    for case in cases:
        case_rows, case_direction_rows = build_canonical_case(
            batch_root=batch_root,
            output_dir=output_dir,
            source_config_root=source_config_root,
            strategy=strategy,
            symbol=symbol,
            case=case,
        )
        rows.extend(case_rows)
        direction_rows.extend(case_direction_rows)
    return strategy, rows, direction_rows


def build_canonical(args: argparse.Namespace, strategies: list[str], cases: tuple[str, ...]) -> None:
    args.output_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    direction_rows: list[dict] = []
    worker_count = max(1, min(int(args.workers), len(strategies)))
    work = {
        "batch_root": args.batch_root,
        "output_dir": args.output_dir,
        "source_config_root": args.source_config_root,
        "symbol": args.symbol,
        "cases": cases,
    }
    if worker_count == 1:
        results = (
            build_canonical_strategy(strategy=strategy, **work)
            for strategy in strategies
        )
        for strategy, strategy_rows, strategy_direction_rows in results:
            rows.extend(strategy_rows)
            direction_rows.extend(strategy_direction_rows)
            print(f"CANONICAL_COMPLETE {strategy}", flush=True)
    else:
        with concurrent.futures.ProcessPoolExecutor(max_workers=worker_count) as pool:
            futures = {
                pool.submit(build_canonical_strategy, strategy=strategy, **work): strategy
                for strategy in strategies
            }
            for future in concurrent.futures.as_completed(futures):
                strategy, strategy_rows, strategy_direction_rows = future.result()
                rows.extend(strategy_rows)
                direction_rows.extend(strategy_direction_rows)
                print(f"CANONICAL_COMPLETE {strategy}", flush=True)
    summary = pd.DataFrame(rows).sort_values(
        ["strategy", "granularity", "lag", "variant", "premium"]
    )
    summary.to_csv(args.output_dir / "canonical_summary.csv", index=False)
    html_summary = summary.copy()
    html_summary["figure"] = html_summary["figure_relative"].map(
        lambda value: f'<a href="{value}">performance</a>'
    )
    html_summary["per_trade_BE_figure"] = html_summary[
        "per_trade_BE_figure_relative"
    ].map(lambda value: f'<a href="{value}">per-trade BE</a>')
    html_summary = html_summary.drop(
        columns=["figure_relative", "per_trade_BE_figure_relative"]
    )
    (args.output_dir / "canonical_summary.html").write_text(
        html_summary.to_html(
            index=False,
            escape=False,
            float_format=lambda value: f"{value:.8f}",
        ),
        encoding="utf-8",
    )
    direction_summary = pd.DataFrame(direction_rows).drop_duplicates(
        ["strategy", "case", "variant"]
    ).sort_values(["strategy", "case", "variant"])
    direction_summary.to_csv(
        args.output_dir / "direction_validation_summary.csv", index=False
    )
    maximum_global_residual = float(summary["global_BE_validation_residual"].max())
    maximum_episode_residual = float(summary["per_trade_BE_validation_residual"].max())
    validation_summary = {
        "status": "passed",
        "existing_registered_strategies": len(strategies),
        "cases_per_strategy": len(cases),
        "variants": list(REPORT_VARIANTS.values()),
        "variant_strategy_counts": {
            variant: int(
                summary.loc[summary["variant"] == variant, "strategy"].nunique()
            )
            for variant in REPORT_VARIANTS.values()
        },
        "missing_strategy_variants": 0,
        "direction_validation_failures": int(
            (~direction_summary["direction_validation_passed"]).sum()
        ),
        "maximum_direction_residual": float(
            direction_summary["max_direction_residual"].max()
        ),
        "global_break_even_maximum_residual": maximum_global_residual,
        "per_trade_break_even_maximum_residual": maximum_episode_residual,
    }
    _atomic_json(args.output_dir / "validation_summary.json", validation_summary)
    _atomic_json(
        args.output_dir / "artifact_manifest.json",
        {
            "strategy_count": len(strategies),
            "case_count": len(cases),
            "variant_count": len(REPORT_VARIANTS),
            "premium_case_count": 2,
            "summary_rows": len(summary),
            "performance_figure_count": len(strategies) * len(cases) * len(REPORT_VARIANTS),
            "per_trade_figure_count": len(strategies) * len(cases) * len(REPORT_VARIANTS),
            "figure_count": len(strategies) * len(cases) * len(REPORT_VARIANTS) * 2,
            "variants": list(REPORT_VARIANTS.values()),
            "primary_variant": "original",
            "strict_reverse_regenerated": True,
            "batch_root": str(args.batch_root.resolve()),
            "validation": validation_summary,
        },
    )


def main() -> int:
    args = parse_args()
    cases = tuple(dict.fromkeys(args.cases or CASES))
    for case in cases:
        if not re.fullmatch(r"\d+[smhd]_lag\d+", case):
            raise ValueError(f"invalid case {case!r}; expected e.g. 5m_lag1 or 1d_lag1440")
    strategies = discover(args.batch_root, cases)
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
        build_canonical(args, strategies, cases)
        print(
            f"COMPLETE canonical strategies={len(strategies)} "
            f"figures={len(strategies) * len(cases) * len(REPORT_VARIANTS) * 2} "
            f"output={args.output_dir}"
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
