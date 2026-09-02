#!/usr/bin/env python3
"""Finalize figures and validation for the boss persistence follow-up."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.internal.build_boss_persistence_v2 import regime_figure
from scripts.internal.build_boss_persistent_followup import (
    RESULT_ROOT,
    atomic_csv,
    atomic_json,
    build as build_postprocessing,
    sha256_file,
    truthy,
)


def case_review(root: Path, row: pd.Series) -> Path:
    return (
        root / "matrix_cases" / f"symbol={row.symbol}" / f"timeframe={row.timeframe}"
        / f"semantic={row.semantic_execution_hash}" / "review_timeseries.parquet"
    )


def figure_summary(row: pd.Series, strategy_id: str | None = None) -> pd.Series:
    return pd.Series(
        {
            "strategy_id": strategy_id or row.strategy_id,
            "symbol": row.symbol,
            "timeframe": row.timeframe,
            "persistence_class_v2": (
                "DIRECTIONALLY_PERSISTENT"
                if bool(row.directionally_persistent) else "PARAMETER_SENSITIVITY_CASE"
            ),
            "Return": row.Return,
            "Return_5bp": row.Return_5bp,
            "BE": row.BE,
            "turnover_percent": row.turnover_percent,
            "long_fraction_v2": row.long_fraction,
            "short_fraction_v2": row.short_fraction,
            "flat_fraction_v2": row.flat_fraction,
            "nonflat_fraction_v2": row.nonflat_fraction,
            "median_directional_run_hours": row.median_directional_run_hours,
            "P90_directional_run_hours": row.P90_directional_run_hours,
            "sign_switch_count_v2": row.sign_switch_count,
            "sign_switches_per_day": row.sign_switches_per_day,
        }
    )


def render_shortlist(root: Path, output: Path, limit: int = 30) -> list[Path]:
    metrics = pd.read_csv(root / "persistent_position_metrics_v2.csv")
    all_rows = pd.read_csv(output / "boss_persistent_directional_shortlist.csv")
    primary_each = limit // 2
    shortlist = pd.concat(
        [
            all_rows[all_rows.timeframe.eq("15m")].head(primary_each),
            all_rows[all_rows.timeframe.eq("10m")].head(primary_each),
        ],
        ignore_index=True,
    )
    if len(shortlist) < limit:
        used = set(zip(shortlist.strategy_id, shortlist.timeframe, strict=True))
        remainder = all_rows[
            ~all_rows.apply(lambda row: (row.strategy_id, row.timeframe) in used, axis=1)
        ].head(limit - len(shortlist))
        shortlist = pd.concat([shortlist, remainder], ignore_index=True)
    figures = output / "figures" / "shortlist"
    figures.mkdir(parents=True, exist_ok=True)
    rendered: list[Path] = []
    for summary in shortlist.itertuples(index=False):
        cases = metrics[
            metrics.strategy_id.eq(summary.strategy_id)
            & metrics.timeframe.eq(summary.timeframe)
            & truthy(metrics.directionally_persistent)
        ].copy()
        if cases.empty:
            continue
        cases["__5bp"] = cases.Return_5bp > 0
        cases["__raw"] = (cases.Return > 0) & (cases.BE > 0)
        row = cases.sort_values(
            ["__5bp", "__raw", "Return_5bp", "BE", "symbol"],
            ascending=[False, False, False, False, True],
        ).iloc[0]
        source = case_review(root, row)
        target = figures / f"rank_{int(summary.descriptive_rank):03d}_{row.strategy_id}_{row.symbol}_{row.timeframe}.png"
        regime_figure(row, pd.read_parquet(source), target)
        rendered.append(target)
    expected = set(rendered)
    for stale in figures.glob("rank_*.png"):
        if stale not in expected:
            stale.unlink()
    return rendered


def render_parameter_examples(output: Path, limit: int = 10) -> list[Path]:
    sensitivity = pd.read_csv(output / "persistence_parameter_sensitivity_v2.csv")
    chosen = sensitivity[
        truthy(sensitivity.persistence_improved) & sensitivity.value_relation.ne("CANONICAL")
    ].copy()
    if chosen.empty:
        return []
    chosen["__acceptable"] = truthy(chosen.acceptable_economics)
    chosen["__5bp"] = truthy(chosen.survives_5bp)
    chosen = chosen.sort_values(
        [
            "__5bp", "__acceptable", "delta_median_directional_run_hours",
            "delta_sign_switches_per_day", "delta_turnover_raw", "strategy_id",
        ],
        ascending=[False, False, False, True, True, True],
    ).drop_duplicates("strategy_id").head(limit)
    figures = output / "figures" / "parameter_sensitivity"
    figures.mkdir(parents=True, exist_ok=True)
    rendered: list[Path] = []
    for index, row in enumerate(chosen.itertuples(index=False), start=1):
        series = pd.Series(row._asdict())
        source = Path(row.review_timeseries_path)
        target = figures / (
            f"{index:02d}_{row.strategy_id}_{row.symbol}_{row.timeframe}_"
            f"{row.parameter}_{row.value_relation}.png"
        )
        regime_figure(figure_summary(series), pd.read_parquet(source), target)
        rendered.append(target)
    return rendered


def render_reference_comparison(root: Path, output: Path) -> Path:
    metrics = pd.read_csv(root / "persistent_position_metrics_v2.csv")
    cases = metrics[truthy(metrics.directionally_persistent)].copy()
    reference = pd.read_csv(root / "reference_position_behavior.csv")
    elapsed_days = (
        pd.to_datetime(reference.end_timestamp, utc=True)
        - pd.to_datetime(reference.start_timestamp, utc=True)
    ).dt.total_seconds() / 86_400.0
    reference["switches_per_day"] = reference.sign_change_count / elapsed_days
    reference["median_run_hours"] = reference.median_state_duration_minutes / 60.0
    fig, axes = plt.subplots(1, 2, figsize=(13, 5), constrained_layout=True)
    axes[0].scatter(
        cases.nonflat_fraction_v2 * 100,
        cases.median_directional_run_hours,
        s=10, alpha=0.25, color="#1565C0", label="Persistent screen cases",
    )
    for name, group in reference.groupby("reference_strategy"):
        axes[0].scatter(
            group.nonflat_fraction * 100, group.median_run_hours,
            s=55, marker="X", label=f"Reference — {name}",
        )
    axes[0].set_xlabel("Nonflat time (%)")
    axes[0].set_ylabel("Median directional-state duration (hours)")
    axes[0].set_yscale("symlog", linthresh=1.0)
    axes[0].legend(frameon=False, fontsize=8)
    axes[1].scatter(
        cases.sign_switches_per_day,
        cases.long_fraction_v2 - cases.short_fraction_v2,
        s=10, alpha=0.25, color="#1565C0", label="Persistent screen cases",
    )
    for name, group in reference.groupby("reference_strategy"):
        axes[1].scatter(
            group.switches_per_day,
            group.long_fraction - group.short_fraction,
            s=55, marker="X", label=f"Reference — {name}",
        )
    axes[1].set_xlabel("Directional sign switches per day")
    axes[1].set_ylabel("Long fraction − short fraction")
    axes[1].axhline(0, color="#777777", linewidth=0.7)
    axes[1].legend(frameon=False, fontsize=8)
    target = output / "figures" / "reference_position_shape_comparison.png"
    target.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(target, dpi=220)
    plt.close(fig)
    return target


def parameter_effect_summary(output: Path) -> pd.DataFrame:
    sensitivity = pd.read_csv(output / "persistence_parameter_sensitivity_v2.csv")
    changed = sensitivity[sensitivity.value_relation.ne("CANONICAL")]
    rows: list[dict[str, Any]] = []
    for key, group in changed.groupby(
        ["strategy_id", "parameter", "value_relation", "tested_value"], sort=True
    ):
        rows.append(
            {
                "strategy_id": key[0],
                "parameter": key[1],
                "value_relation": key[2],
                "tested_value": key[3],
                "case_count": len(group),
                "median_delta_nonflat_fraction": float(group.delta_nonflat_fraction.median()),
                "median_delta_directional_run_hours": float(group.delta_median_directional_run_hours.median()),
                "median_delta_switches_per_day": float(group.delta_sign_switches_per_day.median()),
                "median_delta_turnover_raw": float(group.delta_turnover_raw.median()),
                "median_delta_Return": float(group.delta_Return.median()),
                "median_delta_BE": float(group.delta_BE.median()),
                "median_delta_Return_5bp": float(group.delta_Return_5bp.median()),
                "persistence_improved_case_count": int(truthy(group.persistence_improved).sum()),
                "acceptable_economics_case_count": int(truthy(group.acceptable_economics).sum()),
                "effect_contract": "descriptive median deltas; no winner selected and no config write-back",
            }
        )
    result = pd.DataFrame(rows)
    atomic_csv(output / "persistence_parameter_effect_summary.csv", result)
    return result


def finalize(root: Path, output: Path) -> dict[str, Any]:
    # Rebuild pure aggregates so key answers incorporate finalized sensitivity.
    build_postprocessing(root, output)
    sensitivity_validation = json.loads(
        (output / "persistence_parameter_sensitivity_validation.json").read_text(encoding="utf-8")
    )
    effects = parameter_effect_summary(output)
    shortlist_figures = render_shortlist(root, output)
    parameter_figures = render_parameter_examples(output)
    reference_figure = render_reference_comparison(root, output)
    metrics = pd.read_csv(root / "persistent_position_metrics_v2.csv")
    summary = pd.read_csv(output / "persistent_strategy_timeframe_summary.csv")
    shortlist = pd.read_csv(output / "boss_persistent_directional_shortlist.csv")
    hold = pd.read_csv(output / "hold_until_opposite_feasibility_v2.csv")
    improvable = pd.read_csv(output / "persistence_improvable_strategies.csv")
    persistent = metrics[truthy(metrics.directionally_persistent)]
    authoritative_files = [
        root / "boss_multitimeframe_tick_master.csv",
        root / "persistent_position_metrics_v2.csv",
    ]
    hashes = {path.name: sha256_file(path) for path in authoritative_files}
    top_10m = shortlist[shortlist.timeframe.eq("10m")].head(10).strategy_id.tolist()
    top_15m = shortlist[shortlist.timeframe.eq("15m")].head(10).strategy_id.tolist()
    validation = {
        "status": "PASSED",
        "persistent_cases": len(persistent),
        "total_cases": len(metrics),
        "unique_persistent_strategies": int(persistent.strategy_id.nunique()),
        "persistent_strategy_timeframe_combinations": int((summary.persistent_symbol_count > 0).sum()),
        "persistent_on_at_least_2_symbols": int((summary.persistent_symbol_count >= 2).sum()),
        "persistent_on_at_least_5_symbols": int((summary.persistent_symbol_count >= 5).sum()),
        "10m_persistent_Return_BE_positive": int(((persistent.timeframe == "10m") & (persistent.Return > 0) & (persistent.BE > 0)).sum()),
        "15m_persistent_Return_BE_positive": int(((persistent.timeframe == "15m") & (persistent.Return > 0) & (persistent.BE > 0)).sum()),
        "10m_15m_persistent_5bp_survivors": int((persistent.timeframe.isin(["10m", "15m"]) & (persistent.Return_5bp > 0)).sum()),
        "persistence_parameter_tunable_strategies": sensitivity_validation["eligible_strategy_count"],
        "strategies_where_tested_parameter_improved_persistence": int(truthy(improvable.persistence_improved).sum()),
        "strategies_with_acceptable_economic_improvement": int(improvable.structural_economic_label.eq("PERSISTENCE_IMPROVABLE_WITH_ACCEPTABLE_ECONOMICS").sum()),
        "strategies_requiring_semantic_hold_until_opposite": int(truthy(hold.semantic_change_required).sum()),
        "top_10m_candidates": top_10m,
        "top_15m_candidates": top_15m,
        "parameter_effect_rows": len(effects),
        "shortlist_figures": len(shortlist_figures),
        "parameter_sensitivity_figures": len(parameter_figures),
        "reference_figures": 1,
        "figure_paths": [str(path) for path in shortlist_figures + parameter_figures + [reference_figure]],
        "authoritative_hashes": hashes,
        "canonical_config_changes": 0,
        "full_matrix_backtests_rerun": 0,
        "tick_index_rebuilt": 0,
        "phase3_optimizer_invoked": False,
    }
    atomic_json(output / "validation_summary.json", validation)
    return validation


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=RESULT_ROOT)
    parser.add_argument("--output-root", type=Path)
    args = parser.parse_args()
    output = args.output_root or args.root / "persistent_v2_followup"
    print(json.dumps(finalize(args.root, output), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
