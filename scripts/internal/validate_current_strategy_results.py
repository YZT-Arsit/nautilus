#!/usr/bin/env python3
"""Validate the canonical result hierarchy and reporting identities."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

import pandas as pd


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--baseline-root", type=Path)
    return parser.parse_args()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def main() -> int:
    args = parse_args()
    summary = pd.read_csv(args.root / "canonical_summary.csv")
    required_variants = {"original", "long_only", "short_only", "strict_reverse"}
    found_variants = set(summary["variant"])
    if found_variants != required_variants:
        raise AssertionError(
            f"variant set mismatch: found={sorted(found_variants)}"
        )
    strategy_count = int(summary["strategy"].nunique())
    variant_strategy_counts = {
        variant: int(
            summary.loc[summary["variant"] == variant, "strategy"].nunique()
        )
        for variant in required_variants
    }
    if any(count != strategy_count for count in variant_strategy_counts.values()):
        raise AssertionError(
            f"missing strategy variants: {variant_strategy_counts}"
        )
    direction = pd.read_csv(args.root / "direction_validation_summary.csv")
    if not direction["direction_validation_passed"].all():
        raise AssertionError("direction validation contains failures")
    maximum_direction_residual = float(direction["max_direction_residual"].max())
    if maximum_direction_residual > 1e-12:
        raise AssertionError(
            f"direction residual too large: {maximum_direction_residual}"
        )
    residual = (
        summary["final_return_1x"]
        - summary["turnover"] * summary["break_even_bps"] / 10_000.0
    ).abs()
    finite = residual[pd.notna(residual)]
    max_residual = float(finite.max()) if len(finite) else 0.0
    if max_residual > 1e-9:
        raise AssertionError(f"break-even residual too large: {max_residual}")

    pair_failures = []
    for key, group in summary.groupby(
        ["strategy", "symbol", "granularity", "lag", "variant"]
    ):
        if set(group["premium"]) != {"included", "excluded"}:
            pair_failures.append(f"{key}: premium pair")
            continue
        for column in ("turnover", "start_time", "end_time", "source_timeseries", "figure"):
            if group[column].nunique(dropna=False) != 1:
                pair_failures.append(f"{key}: {column}")
    if pair_failures:
        raise AssertionError(f"premium-only comparison failed: {pair_failures[:5]}")

    missing_sources = [value for value in summary["source_timeseries"].unique() if not Path(value).is_file()]
    missing_figures = [value for value in summary["figure"].unique() if not Path(value).is_file()]
    if missing_sources or missing_figures:
        raise AssertionError(
            f"missing source/figure: sources={len(missing_sources)} figures={len(missing_figures)}"
        )

    bar_metrics = list(args.root.glob("*/BTCUSDT/*/lag*m/*/metrics.json"))
    bar_validation_failures = []
    source_metric_max_abs_error = 0.0
    source_metric_max_rel_error = 0.0
    source_metric_mismatches = []
    baseline_metric_max_abs_error = 0.0
    baseline_metric_mismatches = []
    for path in bar_metrics:
        metrics = json.loads(path.read_text(encoding="utf-8"))
        if not all(metrics["validation"].values()):
            bar_validation_failures.append(str(path))
            continue
        source = json.loads(Path(metrics["source_summary"]).read_text(encoding="utf-8"))[
            metrics["source_variant"]
        ]
        comparisons = (
            (metrics["cases"]["included"]["final_return_1x"], source["total_simple_return_fee0"]),
            (metrics["cases"]["excluded"]["final_return_1x"], source["trading_simple_return"]),
            (metrics["cases"]["included"]["turnover"], source["total_turnover_x"]),
        )
        for left, right in comparisons:
            left_value = float(left)
            right_value = float(right)
            absolute_error = abs(left_value - right_value)
            relative_error = absolute_error / max(abs(right_value), 1.0)
            source_metric_max_abs_error = max(source_metric_max_abs_error, absolute_error)
            source_metric_max_rel_error = max(source_metric_max_rel_error, relative_error)
            if not math.isclose(left_value, right_value, rel_tol=1e-12, abs_tol=1e-10):
                source_metric_mismatches.append(
                    {
                        "metrics": str(path),
                        "rendered": left_value,
                        "source": right_value,
                        "absolute_error": absolute_error,
                        "relative_error": relative_error,
                    }
                )
        if args.baseline_root is not None and metrics["source_variant"] in {
            "normal",
            "strict_reverse",
        }:
            frequency = str(metrics["granularity"]).split()[0]
            case = f"{frequency}_lag{metrics['lag_minutes']}"
            baseline_path = (
                args.baseline_root
                / metrics["strategy"]
                / case
                / "summary.json"
            )
            if not baseline_path.is_file():
                baseline_metric_mismatches.append(
                    {"metrics": str(path), "missing_baseline": str(baseline_path)}
                )
            else:
                baseline = json.loads(baseline_path.read_text(encoding="utf-8"))[
                    metrics["source_variant"]
                ]
                current = json.loads(
                    Path(metrics["source_summary"]).read_text(encoding="utf-8")
                )[metrics["source_variant"]]
                for field in (
                    "trading_simple_return",
                    "funding_simple_return",
                    "total_simple_return_fee0",
                    "total_turnover_x",
                    "signal_count",
                    "execution_fill_count",
                ):
                    absolute_error = abs(float(current[field]) - float(baseline[field]))
                    baseline_metric_max_abs_error = max(
                        baseline_metric_max_abs_error, absolute_error
                    )
                    if not math.isclose(
                        float(current[field]),
                        float(baseline[field]),
                        rel_tol=1e-12,
                        abs_tol=1e-10,
                    ):
                        baseline_metric_mismatches.append(
                            {
                                "metrics": str(path),
                                "field": field,
                                "current": current[field],
                                "baseline": baseline[field],
                                "absolute_error": absolute_error,
                            }
                        )
    if bar_validation_failures:
        raise AssertionError(f"bar validation failures: {bar_validation_failures[:5]}")
    if source_metric_mismatches:
        raise AssertionError(f"new/source metric mismatch: {source_metric_mismatches[:3]}")
    if baseline_metric_mismatches:
        raise AssertionError(
            f"original/baseline metric mismatch: {baseline_metric_mismatches[:3]}"
        )

    config_failures = []
    for strategy in sorted(path.name for path in args.root.iterdir() if path.is_dir()):
        paths = list((args.root / strategy / "BTCUSDT").glob("*/lag*m/*/config.yaml"))
        if paths and len({sha256(path) for path in paths}) != 1:
            config_failures.append(strategy)
    if config_failures:
        raise AssertionError(f"lag config mismatch: {config_failures}")

    tick_validations = list(
        args.root.glob("continuous_tick_ma/BTCUSDT/tick/*/strategy_evaluation_validation.json")
    )
    for path in tick_validations:
        if not all(json.loads(path.read_text(encoding="utf-8")).values()):
            raise AssertionError(f"tick validation failed: {path}")
    temporary = [str(path) for path in args.root.rglob("*") if path.name.endswith((".tmp", ".part"))]
    if temporary:
        raise AssertionError(f"temporary artifacts in canonical hierarchy: {temporary[:5]}")

    result = {
        "status": "passed",
        "summary_rows": len(summary),
        "strategies": int(summary["strategy"].nunique()),
        "variant_strategy_counts": variant_strategy_counts,
        "direction_max_abs_residual": maximum_direction_residual,
        "bar_strategies": int(summary.loc[summary["granularity"].str.endswith("bar"), "strategy"].nunique()),
        "bar_figures": len(bar_metrics),
        "native_tick_figures": len(tick_validations),
        "break_even_max_abs_residual": max_residual,
        "premium_pair_failures": 0,
        "missing_source_timeseries": 0,
        "missing_figures": 0,
        "lag_config_mismatches": 0,
        "source_metric_max_abs_error": source_metric_max_abs_error,
        "source_metric_max_rel_error": source_metric_max_rel_error,
        "baseline_metric_max_abs_error": baseline_metric_max_abs_error,
        "baseline_metric_mismatches": 0,
        "temporary_artifacts": 0,
    }
    path = args.root / "validation_summary.json"
    path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
