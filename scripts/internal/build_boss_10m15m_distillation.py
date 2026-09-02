#!/usr/bin/env python3
"""Distill the completed boss tick screen into a 10m/15m candidate package.

This is deliberately a read-only post-processing pass over the completed
9,612-case matrix, persistence-v2 metrics, and bounded sensitivity outputs.
It never invokes a strategy runner or touches the compact tick index.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.internal.build_boss_persistence_v2 import regime_figure


DEFAULT_ROOT = PROJECT_ROOT / "outputs/baseline_evaluation/boss_multitimeframe_tick_screen"
FOLLOWUP = "persistent_v2_followup"
OUTPUT_NAME = "final_candidate_distillation"
TIMEFRAMES = ("1m", "5m", "10m", "15m")
PRIMARY_TIMEFRAMES = ("10m", "15m")
TF_ORDER = {"15m": 0, "10m": 1, "5m": 2, "1m": 3}


def truthy(series: pd.Series) -> pd.Series:
    if series.dtype == bool:
        return series.fillna(False)
    return series.astype(str).str.strip().str.lower().eq("true")


def atomic_csv(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    frame.to_csv(temporary, index=False, encoding="utf-8-sig")
    os.replace(temporary, path)


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False, default=str) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def config_snapshot() -> dict[str, str]:
    paths = sorted(
        path
        for pattern in ("config.yaml", "config.py", "strategy.py", "plugin.py")
        for path in (PROJECT_ROOT / "strategies").rglob(pattern)
    )
    return {str(path.relative_to(PROJECT_ROOT)): sha256_file(path) for path in paths}


def validate_sources(metrics: pd.DataFrame, sensitivity: pd.DataFrame) -> dict[str, int]:
    if len(metrics) != 9_612:
        raise ValueError(f"expected 9,612 v2 rows, found {len(metrics)}")
    if metrics[["strategy_id", "symbol", "timeframe"]].duplicated().any():
        raise ValueError("duplicate logical cases in persistence-v2 metrics")
    if set(metrics.timeframe.unique()) != set(TIMEFRAMES):
        raise ValueError(f"timeframe universe changed: {sorted(metrics.timeframe.unique())}")
    persistent = truthy(metrics.directionally_persistent)
    observed = {
        "persistent_cases": int(persistent.sum()),
        "persistent_unique_strategies": int(metrics.loc[persistent, "strategy_id"].nunique()),
        "persistent_strategy_timeframe": int(
            metrics.loc[persistent, ["strategy_id", "timeframe"]].drop_duplicates().shape[0]
        ),
        "10m_persistent_Return_BE_positive_cases": int(
            (persistent & metrics.timeframe.eq("10m") & (metrics.Return > 0) & (metrics.BE > 0)).sum()
        ),
        "15m_persistent_Return_BE_positive_cases": int(
            (persistent & metrics.timeframe.eq("15m") & (metrics.Return > 0) & (metrics.BE > 0)).sum()
        ),
        "10m15m_persistent_5bp_survivor_cases": int(
            (persistent & metrics.timeframe.isin(PRIMARY_TIMEFRAMES) & (metrics.Return_5bp > 0)).sum()
        ),
    }
    expected = {
        "persistent_cases": 930,
        "persistent_unique_strategies": 48,
        "persistent_strategy_timeframe": 144,
        "10m_persistent_Return_BE_positive_cases": 124,
        "15m_persistent_Return_BE_positive_cases": 147,
        "10m15m_persistent_5bp_survivor_cases": 173,
    }
    if observed != expected:
        raise ValueError(f"persistence source reconciliation failed: {observed} != {expected}")
    if sensitivity.empty or not {
        "semantic_execution_hash", "parameter", "tested_value", "value_relation",
        "delta_median_directional_run_hours", "delta_sign_switches_per_day",
    }.issubset(sensitivity.columns):
        raise ValueError("bounded parameter-sensitivity output is missing required columns")
    return observed


def classify_level(persistent_symbols: int, economic_symbols: int, tunable: bool) -> str:
    if persistent_symbols >= 5 and economic_symbols >= 5:
        return "LEVEL_A_BROAD_PERSISTENT_ECONOMIC"
    if persistent_symbols >= 3 and economic_symbols >= 3:
        return "LEVEL_B_MULTI_SYMBOL_PERSISTENT_ECONOMIC"
    if persistent_symbols >= 2 or economic_symbols >= 3 or tunable:
        return "LEVEL_C_STRUCTURALLY_INTERESTING"
    return "NOT_SHORTLISTED"


def strategy_summary(metrics: pd.DataFrame, tunable_ids: set[str]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    primary = metrics[metrics.timeframe.isin(PRIMARY_TIMEFRAMES)]
    for (strategy_id, timeframe), group in primary.groupby(["strategy_id", "timeframe"], sort=True):
        persistent = truthy(group.directionally_persistent)
        economic = (group.Return > 0) & (group.BE > 0)
        five_bp = group.Return_5bp > 0
        structure = group.persistence_class_v2.mode()
        row = {
            "strategy_id": strategy_id,
            "timeframe": timeframe,
            "semantic_execution_hash": str(group.semantic_execution_hash.iloc[0]),
            "representative_strategy_id": str(group.representative_strategy_id.iloc[0]),
            "symbols_tested": int(group.symbol.nunique()),
            "persistent_symbol_count": int(persistent.sum()),
            "persistent_symbol_fraction": float(persistent.mean()),
            "persistent_and_Return_BE_positive_symbols": int((persistent & economic).sum()),
            "persistent_and_5bp_positive_symbols": int((persistent & five_bp).sum()),
            "all_Return_BE_positive_symbols": int(economic.sum()),
            "all_5bp_positive_symbols": int(five_bp.sum()),
            "median_Return": float(group.Return.median()),
            "median_BE": float(group.BE.median()),
            "median_5bp_Return": float(group.Return_5bp.median()),
            "median_Turnover_pct": float(group.turnover_percent.median()),
            "median_nonflat_fraction": float(group.nonflat_fraction_v2.median()),
            "median_directional_run_hours": float(group.median_directional_run_hours.median()),
            "P25_directional_run_hours": float(group.median_directional_run_hours.quantile(0.25)),
            "P90_directional_run_hours": float(group.median_directional_run_hours.quantile(0.90)),
            "median_switches_per_day": float(group.sign_switches_per_day.median()),
            "max_switches_per_day": float(group.sign_switches_per_day.max()),
            "median_holding_duration": float(group.median_holding_duration_seconds.median()),
            "persistence_structure_class": (
                str(structure.iloc[0]) if not structure.empty else "MIXED"
            ),
            "parameter_tunable": strategy_id in tunable_ids,
        }
        row["shortlist_level"] = classify_level(
            row["persistent_symbol_count"], row["all_Return_BE_positive_symbols"],
            bool(row["parameter_tunable"]),
        )
        row["FIVE_BP_BROAD"] = row["persistent_and_5bp_positive_symbols"] >= 5
        row["FIVE_BP_MULTI"] = row["persistent_and_5bp_positive_symbols"] >= 3
        rows.append(row)
    return pd.DataFrame(rows).sort_values(["timeframe", "strategy_id"]).reset_index(drop=True)


def collapse_semantic(summary: pd.DataFrame) -> pd.DataFrame:
    invariant = [
        "persistent_symbol_count", "persistent_and_Return_BE_positive_symbols",
        "persistent_and_5bp_positive_symbols", "all_Return_BE_positive_symbols",
        "all_5bp_positive_symbols", "shortlist_level",
    ]
    rows: list[dict[str, Any]] = []
    for (semantic_hash, timeframe), group in summary.groupby(
        ["semantic_execution_hash", "timeframe"], sort=True
    ):
        if group[invariant].nunique(dropna=False).max() != 1:
            raise ValueError(f"semantic-equivalent rows disagree: {semantic_hash} {timeframe}")
        members = sorted(group.strategy_id.unique())
        declared = [value for value in group.representative_strategy_id.unique() if value in members]
        representative = sorted(declared)[0] if declared else members[0]
        base = group[group.strategy_id.eq(representative)]
        if base.empty:
            base = group.sort_values("strategy_id").head(1)
        row = base.iloc[0].to_dict()
        row.update(
            {
                "representative_strategy_id": representative,
                "equivalent_source_ids": ";".join(members),
                "raw_strategy_id_count": len(members),
                "independence_contract": "pre-performance semantic_execution_hash",
            }
        )
        rows.append(row)
    return pd.DataFrame(rows)


def select_top(independent: pd.DataFrame, limit: int = 20) -> pd.DataFrame:
    eligible = independent[independent.shortlist_level.str.startswith(("LEVEL_A", "LEVEL_B"))].copy()
    eligible["__level"] = eligible.shortlist_level.map(
        {"LEVEL_A_BROAD_PERSISTENT_ECONOMIC": 0,
         "LEVEL_B_MULTI_SYMBOL_PERSISTENT_ECONOMIC": 1}
    )
    eligible["__tf"] = eligible.timeframe.map(TF_ORDER)
    eligible = eligible.sort_values(
        ["__level", "all_5bp_positive_symbols",
         "all_Return_BE_positive_symbols", "persistent_symbol_count",
         "median_BE", "median_Turnover_pct", "__tf", "representative_strategy_id"],
        ascending=[True, False, False, False, False, True, True, True],
    ).drop(columns=["__level", "__tf"])
    eligible.insert(0, "transparent_rank", np.arange(1, len(eligible) + 1))
    eligible["ordering_contract"] = (
        "Level A before B; all 5bp-positive symbols DESC; all Return&BE-positive symbols DESC; "
        "persistent symbols DESC; median signed BE DESC; median turnover ASC; no score"
    )
    return eligible.head(limit).reset_index(drop=True)


def timeframe_paths(metrics: pd.DataFrame, strategy_ids: set[str]) -> pd.DataFrame:
    selected = metrics[metrics.strategy_id.isin(strategy_ids)].copy()
    values = {
        "Return": "Return", "BE": "BE", "Return_5bp": "Return_5bp",
        "turnover_percent": "turnover_pct", "nonflat_fraction_v2": "nonflat_fraction",
        "median_directional_run_hours": "median_directional_run_hours",
        "sign_switches_per_day": "switches_per_day",
    }
    wide = selected.pivot(index=["strategy_id", "symbol"], columns="timeframe", values=list(values))
    rows: list[dict[str, Any]] = []
    for (strategy_id, symbol), series in wide.iterrows():
        row: dict[str, Any] = {"strategy_id": strategy_id, "symbol": symbol}
        for source, label in values.items():
            for timeframe in TIMEFRAMES:
                row[f"{timeframe}_{label}"] = float(series[(source, timeframe)])
        for timeframe in PRIMARY_TIMEFRAMES:
            run_up = row[f"{timeframe}_median_directional_run_hours"] > row["1m_median_directional_run_hours"]
            switches_down = row[f"{timeframe}_switches_per_day"] < row["1m_switches_per_day"]
            turnover_down = row[f"{timeframe}_turnover_pct"] < row["1m_turnover_pct"]
            be_up = row[f"{timeframe}_BE"] > row["1m_BE"]
            row[f"{timeframe}_PERSISTENCE_IMPROVED"] = bool(run_up and switches_down)
            row[f"{timeframe}_TURNOVER_IMPROVED"] = bool(turnover_down)
            row[f"{timeframe}_ECONOMICS_IMPROVED"] = bool(be_up)
            row[f"{timeframe}_JOINT_IMPROVEMENT"] = bool(
                run_up and switches_down and turnover_down and be_up
            )
        rows.append(row)
    return pd.DataFrame(rows).sort_values(["strategy_id", "symbol"]).reset_index(drop=True)


def timeframe_joint_summary(paths: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for timeframe in PRIMARY_TIMEFRAMES:
        rows.append(
            {
                "timeframe_vs_1m": timeframe,
                "strategy_symbol_cases": len(paths),
                "PERSISTENCE_IMPROVED": int(truthy(paths[f"{timeframe}_PERSISTENCE_IMPROVED"]).sum()),
                "TURNOVER_IMPROVED": int(truthy(paths[f"{timeframe}_TURNOVER_IMPROVED"]).sum()),
                "ECONOMICS_IMPROVED": int(truthy(paths[f"{timeframe}_ECONOMICS_IMPROVED"]).sum()),
                "JOINT_IMPROVEMENT": int(truthy(paths[f"{timeframe}_JOINT_IMPROVEMENT"]).sum()),
                "denominator_contract": "raw strategy_id × symbol cases in final A/B set plus xlsx_s2_0124",
            }
        )
    return pd.DataFrame(rows)


def compare_10m_15m(summary: pd.DataFrame) -> pd.DataFrame:
    relevant = summary[summary.shortlist_level.ne("NOT_SHORTLISTED")]
    value_columns = [
        "persistent_symbol_count", "all_Return_BE_positive_symbols", "all_5bp_positive_symbols",
        "median_directional_run_hours", "median_switches_per_day", "median_Turnover_pct", "median_BE",
    ]
    wide = relevant.pivot(index="strategy_id", columns="timeframe", values=value_columns)
    rows = []
    for strategy_id, values in wide.iterrows():
        row: dict[str, Any] = {"strategy_id": strategy_id}
        for source in value_columns:
            for timeframe in PRIMARY_TIMEFRAMES:
                value = values.get((source, timeframe), np.nan)
                row[f"{timeframe}_{source}"] = float(value) if pd.notna(value) else np.nan
        if pd.isna(row["10m_persistent_symbol_count"]) or pd.isna(row["15m_persistent_symbol_count"]):
            observation = "ONLY_ONE_PRIMARY_TIMEFRAME_AVAILABLE"
        elif (
            row["15m_median_directional_run_hours"] > row["10m_median_directional_run_hours"]
            and row["15m_median_switches_per_day"] < row["10m_median_switches_per_day"]
            and row["15m_median_Turnover_pct"] < row["10m_median_Turnover_pct"]
        ):
            observation = "15M_LONGER_RUNS_LOWER_SWITCHING_LOWER_TURNOVER"
        elif (
            row["10m_median_directional_run_hours"] > row["15m_median_directional_run_hours"]
            and row["10m_median_switches_per_day"] < row["15m_median_switches_per_day"]
            and row["10m_median_Turnover_pct"] < row["15m_median_Turnover_pct"]
        ):
            observation = "10M_LONGER_RUNS_LOWER_SWITCHING_LOWER_TURNOVER"
        else:
            observation = "MIXED_TIMEFRAME_EFFECT"
        row["descriptive_observation"] = observation
        rows.append(row)
    return pd.DataFrame(rows).sort_values("strategy_id").reset_index(drop=True)


def cross_symbol_detail(metrics: pd.DataFrame, independent: pd.DataFrame) -> pd.DataFrame:
    selected = independent[independent.shortlist_level.str.startswith(("LEVEL_A", "LEVEL_B"))]
    keys = set(zip(selected.semantic_execution_hash, selected.timeframe, strict=True))
    frame = metrics[
        metrics.apply(lambda row: (row.semantic_execution_hash, row.timeframe) in keys, axis=1)
    ].copy()
    frame = frame.drop_duplicates(["semantic_execution_hash", "symbol", "timeframe"])
    level_map = selected.set_index(["semantic_execution_hash", "timeframe"])["shortlist_level"]
    representative_map = selected.set_index(["semantic_execution_hash", "timeframe"])["representative_strategy_id"]
    frame["shortlist_level"] = [level_map.loc[(a, b)] for a, b in zip(frame.semantic_execution_hash, frame.timeframe, strict=True)]
    frame["representative_strategy_id"] = [representative_map.loc[(a, b)] for a, b in zip(frame.semantic_execution_hash, frame.timeframe, strict=True)]
    columns = [
        "representative_strategy_id", "semantic_execution_hash", "shortlist_level", "timeframe", "symbol",
        "Return", "Return_5bp", "BE", "turnover_percent", "nonflat_fraction_v2",
        "long_fraction_v2", "short_fraction_v2", "flat_fraction_v2",
        "median_directional_run_hours", "P90_directional_run_hours", "sign_switches_per_day",
        "directionally_persistent",
    ]
    return frame[columns].sort_values(["shortlist_level", "timeframe", "representative_strategy_id", "symbol"])


def parameter_examples(
    sensitivity: pd.DataFrame, independent: pd.DataFrame
) -> tuple[pd.DataFrame, dict[tuple[str, str], dict[str, Any]]]:
    selected = independent[independent.shortlist_level.str.startswith(("LEVEL_A", "LEVEL_B"))]
    keys = set(zip(selected.semantic_execution_hash, selected.timeframe, strict=True))
    changed = sensitivity[sensitivity.value_relation.ne("CANONICAL")].copy()
    changed = changed[
        changed.apply(lambda row: (row.semantic_execution_hash, row.timeframe) in keys, axis=1)
    ]
    changed["strict_persistence_improvement"] = (
        (changed.delta_median_directional_run_hours > 0)
        & (changed.delta_sign_switches_per_day < 0)
    )
    examples = changed[changed.strict_persistence_improvement].copy()
    examples["economic_bucket"] = np.select(
        [examples.Return_5bp > 0, examples.BE > 0],
        ["B_5BP_RETURN_REMAINS_POSITIVE", "A_BE_REMAINS_POSITIVE"],
        default="C_ECONOMICS_FAIL",
    )
    examples["effect_class"] = np.select(
        [
            (examples.BE > 0) & (examples.Return_5bp > 0),
            examples.BE > 0,
            examples.BE <= 0,
        ],
        [
            "LOWER_SWITCHING_WITH_STABLE_COST_CAPACITY",
            "LONGER_HOLDING_LOWER_TURNOVER_BE_POSITIVE",
            "PERSISTENCE_IMPROVES_BUT_ECONOMICS_FAILS",
        ],
        default="LITTLE_PERSISTENCE_EFFECT",
    )
    examples = examples.sort_values(
        ["economic_bucket", "delta_median_directional_run_hours", "delta_sign_switches_per_day", "strategy_id"],
        ascending=[True, False, True, True],
    )
    conclusions: dict[tuple[str, str], dict[str, Any]] = {}
    for key in keys:
        group = changed[(changed.semantic_execution_hash == key[0]) & (changed.timeframe == key[1])]
        strict = group[group.strict_persistence_improvement]
        if group.empty:
            value = {"existing_persistence_parameter": "", "parameter_sensitivity_conclusion": "NOT_TESTED"}
        elif strict.empty:
            value = {
                "existing_persistence_parameter": ";".join(sorted(group.parameter.astype(str).unique())),
                "parameter_sensitivity_conclusion": "LITTLE_PERSISTENCE_EFFECT",
            }
        elif (strict.Return_5bp > 0).any():
            value = {
                "existing_persistence_parameter": ";".join(sorted(strict.parameter.astype(str).unique())),
                "parameter_sensitivity_conclusion": "LOWER_SWITCHING_LONGER_RUNS_5BP_SURVIVES",
            }
        elif (strict.BE > 0).any():
            value = {
                "existing_persistence_parameter": ";".join(sorted(strict.parameter.astype(str).unique())),
                "parameter_sensitivity_conclusion": "LOWER_SWITCHING_LONGER_RUNS_BE_POSITIVE",
            }
        else:
            value = {
                "existing_persistence_parameter": ";".join(sorted(strict.parameter.astype(str).unique())),
                "parameter_sensitivity_conclusion": "PERSISTENCE_IMPROVES_BUT_ECONOMICS_FAILS",
            }
        conclusions[key] = value
    return examples.reset_index(drop=True), conclusions


def hold_candidates(hold: pd.DataFrame, summary: pd.DataFrame) -> pd.DataFrame:
    economic = summary.groupby("strategy_id").agg(
        positive_symbols=("all_Return_BE_positive_symbols", "max"),
        median_nonflat_fraction=("median_nonflat_fraction", "median"),
        structural_class=("persistence_structure_class", lambda x: ";".join(sorted(set(map(str, x))))),
    ).reset_index()
    frame = hold.merge(economic, on="strategy_id", how="left", validate="one_to_one")
    directional_col = "directional_state_available" if "directional_state_available" in frame else "directional_signal_available"
    eligible = (
        truthy(frame[directional_col])
        & truthy(frame.semantic_change_required)
        & frame.positive_symbols.fillna(0).ge(1)
    )
    result = frame[eligible].copy()
    result["why_hold_until_opposite_could_increase_persistence"] = (
        "directional state exists; canonical flat transitions interrupt directional runs"
    )
    result["semantic_change_required"] = "YES"
    result["selection_contract"] = (
        "directional state available; semantic change required; at least one 10m/15m "
        "Return&BE-positive symbol; top 30 by economic breadth then current nonflat fraction"
    )
    return result.sort_values(
        ["positive_symbols", "median_nonflat_fraction", "strategy_id"],
        ascending=[False, False, True],
    ).head(30)


def choose_figure_case(metrics: pd.DataFrame, semantic_hash: str, timeframe: str) -> pd.Series:
    cases = metrics[
        metrics.semantic_execution_hash.eq(semantic_hash) & metrics.timeframe.eq(timeframe)
    ].drop_duplicates(["semantic_execution_hash", "symbol", "timeframe"]).copy()
    cases["__persistent"] = truthy(cases.directionally_persistent)
    cases["__5bp"] = cases.Return_5bp > 0
    cases["__economic"] = (cases.Return > 0) & (cases.BE > 0)
    return cases.sort_values(
        ["__persistent", "__5bp", "__economic", "Return_5bp", "BE", "symbol"],
        ascending=[False, False, False, False, False, True],
    ).iloc[0]


def case_review(root: Path, row: pd.Series) -> Path:
    return (
        root / "matrix_cases" / f"symbol={row.symbol}" / f"timeframe={row.timeframe}"
        / f"semantic={row.semantic_execution_hash}" / "review_timeseries.parquet"
    )


def render_figures(
    root: Path, output: Path, metrics: pd.DataFrame, independent: pd.DataFrame,
    examples: pd.DataFrame, max_figures: int = 36,
) -> dict[tuple[str, str], str]:
    candidates = independent[independent.shortlist_level.str.startswith(("LEVEL_A", "LEVEL_B"))].copy()
    candidates["__level"] = candidates.shortlist_level.map(
        {"LEVEL_A_BROAD_PERSISTENT_ECONOMIC": 0,
         "LEVEL_B_MULTI_SYMBOL_PERSISTENT_ECONOMIC": 1}
    )
    candidates = candidates.sort_values(
        ["__level", "persistent_and_5bp_positive_symbols",
         "persistent_and_Return_BE_positive_symbols", "persistent_symbol_count", "median_BE"],
        ascending=[True, False, False, False, False],
    )
    figures = output / "figures"
    figures.mkdir(parents=True, exist_ok=True)
    paths: dict[tuple[str, str], str] = {}
    rendered = 0
    for item in candidates.itertuples(index=False):
        if rendered >= max_figures:
            break
        row = choose_figure_case(metrics, item.semantic_execution_hash, item.timeframe)
        source = case_review(root, row)
        if not source.is_file():
            raise FileNotFoundError(source)
        target = figures / (
            f"{item.shortlist_level.split('_')[1]}_{item.representative_strategy_id}_"
            f"{row.symbol}_{item.timeframe}.png"
        )
        summary = row.copy()
        summary["strategy_id"] = item.representative_strategy_id
        regime_figure(summary, pd.read_parquet(source), target)
        paths[(item.semantic_execution_hash, item.timeframe)] = str(target)
        rendered += 1

    # Add a small number of noncanonical parameter examples only when they are
    # not already represented by the main figure set.
    example_dir = figures / "parameter_examples"
    for item in examples.drop_duplicates(["semantic_execution_hash", "timeframe"]).itertuples(index=False):
        if rendered >= max_figures:
            break
        if (item.semantic_execution_hash, item.timeframe) in paths:
            continue
        source = Path(item.review_timeseries_path)
        if not source.is_file():
            continue
        target = example_dir / f"PARAM_{item.strategy_id}_{item.symbol}_{item.timeframe}_{item.parameter}.png"
        summary = pd.Series(
            {
                "strategy_id": item.strategy_id, "symbol": item.symbol, "timeframe": item.timeframe,
                "persistence_class_v2": "EXISTING_PARAMETER_COMPARISON",
                "Return": item.Return, "Return_5bp": item.Return_5bp, "BE": item.BE,
                "turnover_percent": item.turnover_percent,
                "long_fraction_v2": item.long_fraction, "short_fraction_v2": item.short_fraction,
                "flat_fraction_v2": item.flat_fraction, "nonflat_fraction_v2": item.nonflat_fraction,
                "median_directional_run_hours": item.median_directional_run_hours,
                "P90_directional_run_hours": item.P90_directional_run_hours,
                "sign_switch_count_v2": item.sign_switch_count,
                "sign_switches_per_day": item.sign_switches_per_day,
            }
        )
        regime_figure(summary, pd.read_parquet(source), target)
        rendered += 1
    return paths


def build(root: Path, output: Path, render: bool = True) -> dict[str, Any]:
    protected_before = config_snapshot()
    source_paths = {
        "metrics": root / "persistent_position_metrics_v2.csv",
        "master": root / "boss_multitimeframe_tick_master.csv",
        "sensitivity": root / FOLLOWUP / "persistence_parameter_sensitivity_v2.csv",
        "hold": root / FOLLOWUP / "hold_until_opposite_feasibility_v2.csv",
        "reference": root / FOLLOWUP / "reference_like_position_candidates.csv",
    }
    for path in source_paths.values():
        if not path.is_file():
            raise FileNotFoundError(path)
    source_hashes_before = {name: sha256_file(path) for name, path in source_paths.items()}
    metrics = pd.read_csv(source_paths["metrics"])
    master = pd.read_csv(source_paths["master"])
    sensitivity = pd.read_csv(source_paths["sensitivity"])
    hold = pd.read_csv(source_paths["hold"])
    reference = pd.read_csv(source_paths["reference"])
    observed = validate_sources(metrics, sensitivity)
    if len(master) != 9_612:
        raise ValueError(f"master row count changed: {len(master)}")
    tunable_ids = set(sensitivity.strategy_id.unique())
    summary = strategy_summary(metrics, tunable_ids)
    independent = collapse_semantic(summary)
    final_independent = independent[independent.shortlist_level.str.startswith(("LEVEL_A", "LEVEL_B"))].copy()
    top = select_top(independent)
    raw_ids = set(
        summary[summary.shortlist_level.str.startswith(("LEVEL_A", "LEVEL_B"))].strategy_id
    ) | {"xlsx_s2_0124"}
    paths = timeframe_paths(metrics, raw_ids)
    joint = timeframe_joint_summary(paths)
    comparison = compare_10m_15m(summary)
    detail = cross_symbol_detail(metrics, independent)
    examples, conclusions = parameter_examples(sensitivity, independent)
    hold_shortlist = hold_candidates(hold, summary)
    reference_shortlist = reference[
        reference.strategy_id.isin(set(final_independent.representative_strategy_id))
        & reference.timeframe.isin(PRIMARY_TIMEFRAMES)
    ].copy()

    figure_paths: dict[tuple[str, str], str] = {}
    if render:
        figure_paths = render_figures(root, output, metrics, independent, examples)

    final_rows = []
    for item in final_independent.itertuples(index=False):
        row = item._asdict()
        key = (item.semantic_execution_hash, item.timeframe)
        row.update(conclusions.get(key, {
            "existing_persistence_parameter": "",
            "parameter_sensitivity_conclusion": "NOT_TESTED",
        }))
        row["hold_until_opposite_needed"] = "NO_FOR_CURRENT_CANONICAL_RESULT"
        row["figure_path"] = figure_paths.get(key, "")
        final_rows.append(row)
    final = pd.DataFrame(final_rows)
    if not final.empty:
        final["__level"] = final.shortlist_level.map(
            {"LEVEL_A_BROAD_PERSISTENT_ECONOMIC": 0,
             "LEVEL_B_MULTI_SYMBOL_PERSISTENT_ECONOMIC": 1}
        )
        final = final.sort_values(
            ["__level", "all_5bp_positive_symbols",
             "all_Return_BE_positive_symbols", "persistent_symbol_count",
             "median_BE", "median_Turnover_pct"],
            ascending=[True, False, False, False, False, True],
        ).drop(columns="__level")

    output.mkdir(parents=True, exist_ok=True)
    outputs = {
        "boss_10m15m_strategy_summary.csv": summary,
        "boss_10m_vs_15m_comparison.csv": comparison,
        "boss_shortlist_timeframe_paths.csv": paths,
        "boss_timeframe_joint_improvement_summary.csv": joint,
        "boss_10m15m_cross_symbol_detail.csv": detail,
        "boss_10m15m_independent_candidates.csv": independent,
        "boss_10m15m_final_shortlist.csv": final,
        "boss_10m15m_top_candidates.csv": top,
        "boss_parameter_persistence_examples.csv": examples,
        "boss_hold_until_opposite_candidates.csv": hold_shortlist,
        "boss_reference_shape_comparison.csv": reference_shortlist,
    }
    for name, frame in outputs.items():
        atomic_csv(output / name, frame)

    counts = {
        "independent_Level_A_10m": int(((independent.timeframe == "10m") & independent.shortlist_level.str.startswith("LEVEL_A")).sum()),
        "independent_Level_A_15m": int(((independent.timeframe == "15m") & independent.shortlist_level.str.startswith("LEVEL_A")).sum()),
        "independent_Level_B_10m": int(((independent.timeframe == "10m") & independent.shortlist_level.str.startswith("LEVEL_B")).sum()),
        "independent_Level_B_15m": int(((independent.timeframe == "15m") & independent.shortlist_level.str.startswith("LEVEL_B")).sum()),
        "independent_10m_persistent_ge5": int(((independent.timeframe == "10m") & (independent.persistent_symbol_count >= 5)).sum()),
        "independent_15m_persistent_ge5": int(((independent.timeframe == "15m") & (independent.persistent_symbol_count >= 5)).sum()),
        "independent_Return_BE_positive_ge5": int(
            ((independent.persistent_symbol_count >= 5)
             & (independent.all_Return_BE_positive_symbols >= 5)).sum()
        ),
        "independent_5bp_positive_ge5": int(
            ((independent.shortlist_level.str.startswith(("LEVEL_A", "LEVEL_B")))
             & (independent.all_5bp_positive_symbols >= 5)).sum()
        ),
        "final_independent_strategy_timeframe_candidates": int(len(final)),
        "final_unique_semantic_groups": int(final.semantic_execution_hash.nunique()),
        "existing_parameter_persistence_improvable_final": int(
            examples.semantic_execution_hash.nunique()
        ),
        "hold_until_opposite_semantic_variant_shortlist": int(len(hold_shortlist)),
    }
    key_rows = [
        ("Independent Level A 10m", counts["independent_Level_A_10m"], "independent semantic group × timeframe"),
        ("Independent Level A 15m", counts["independent_Level_A_15m"], "independent semantic group × timeframe"),
        ("Independent Level B 10m", counts["independent_Level_B_10m"], "independent semantic group × timeframe"),
        ("Independent Level B 15m", counts["independent_Level_B_15m"], "independent semantic group × timeframe"),
        ("10m persistent on >=5 symbols", counts["independent_10m_persistent_ge5"], "independent semantic group × timeframe"),
        ("15m persistent on >=5 symbols", counts["independent_15m_persistent_ge5"], "independent semantic group × timeframe"),
        ("Return+BE positive on >=5 symbols", counts["independent_Return_BE_positive_ge5"], "independent semantic group × timeframe across 10m and 15m"),
        ("5bp-positive on >=5 symbols among final A/B", counts["independent_5bp_positive_ge5"], "independent semantic group × timeframe across 10m and 15m"),
        ("Final independent A/B candidates", counts["final_independent_strategy_timeframe_candidates"], "independent semantic group × timeframe"),
        ("Existing-parameter persistence-improvable", counts["existing_parameter_persistence_improvable_final"], "independent semantic group represented in final A/B set"),
        ("Hold-until-opposite review shortlist", counts["hold_until_opposite_semantic_variant_shortlist"], "raw strategy ID; not backtested"),
    ]
    answers = pd.DataFrame(key_rows, columns=["question", "result", "denominator"])
    atomic_csv(output / "boss_10m15m_key_answers.csv", answers)

    source_hashes_after = {name: sha256_file(path) for name, path in source_paths.items()}
    protected_after = config_snapshot()
    if source_hashes_before != source_hashes_after:
        raise ValueError("authoritative source artifacts changed during post-processing")
    if protected_before != protected_after:
        raise ValueError("canonical strategy/config hashes changed during post-processing")
    validation = {
        "status": "PASSED",
        "source_reconciliation": observed,
        "counts": counts,
        "all_48_persistent_strategy_ids_reconciled": observed["persistent_unique_strategies"] == 48,
        "all_144_persistent_strategy_timeframe_reconciled": observed["persistent_strategy_timeframe"] == 144,
        "semantic_equivalence_contract": "pre-performance semantic_execution_hash",
        "semantic_group_metric_inconsistencies": 0,
        "source_hashes_unchanged": source_hashes_before == source_hashes_after,
        "canonical_config_modifications": 0,
        "strategy_semantic_modifications": 0,
        "full_matrix_backtests_rerun": 0,
        "tick_index_rebuild": 0,
        "parameter_grid_extensions": 0,
        "figure_count": len(list((output / "figures").rglob("*.png"))) if render else 0,
        "output_files": sorted(outputs) + ["boss_10m15m_key_answers.csv"],
        "source_hashes": source_hashes_before,
    }
    atomic_json(output / "validation_summary.json", validation)
    return validation


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--skip-figures", action="store_true")
    args = parser.parse_args()
    output = args.output or args.root / OUTPUT_NAME
    result = build(args.root, output, render=not args.skip_figures)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
