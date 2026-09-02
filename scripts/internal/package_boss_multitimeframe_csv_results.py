#!/usr/bin/env python3
"""Package the completed boss multi-timeframe screen as auditable CSV tables.

This module never runs strategies or reads the tick index.  Every output is a
deterministic projection of the already-completed 9,612-row master result.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
RESULT_ROOT = ROOT / "outputs/baseline_evaluation/boss_multitimeframe_tick_screen"
SYMBOLS = (
    "XRPUSDT", "DOGEUSDT", "SUIUSDT", "BNBUSDT", "ETHUSDT",
    "BTCUSDT", "1000PEPEUSDT", "SOLUSDT", "ADAUSDT",
)
TIMEFRAMES = ("1m", "5m", "10m", "15m")
TIMEFRAME_DELIVERY_ORDER = {"15m": 0, "10m": 1, "5m": 2, "1m": 3}
EXPECTED = {
    "strategies": 267,
    "symbols": 9,
    "timeframes": 4,
    "logical_cases": 9_612,
    "completed": 9_612,
    "failures": 0,
    "10m_return_be_positive": 1_123,
    "15m_return_be_positive": 1_214,
    "multi_symbol_positive": 770,
    "five_bp_survivors": 676,
    "near_always_in_market": 4_175,
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_csv(path: Path, frame: pd.DataFrame) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    frame.to_csv(temporary, index=False, encoding="utf-8-sig")
    os.replace(temporary, path)


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def required_master_columns() -> set[str]:
    return {
        "strategy_id", "status", "symbol", "timeframe", "Return_fee0",
        "Return_5bp", "Turnover_raw", "Turnover_pct", "BE_bps", "MDD",
        "episode_count", "long_fraction", "short_fraction", "flat_fraction",
        "nonflat_fraction", "median_holding_duration_seconds",
        "p90_holding_duration_seconds", "position_change_count",
        "sign_switch_count", "first_tick_wait_median_ms",
        "first_tick_wait_p95_ms", "persistence_structure_class",
    }


def normalize_master_delivery_schema(master: pd.DataFrame) -> pd.DataFrame:
    """Add boss-facing aliases without changing any computed source column."""
    result = master.copy()
    aliases = {
        "Signed_BE_bps": "BE_bps",
        "completed_episode_count": "episode_count",
        "median_holding_duration": "median_holding_duration_seconds",
        "P90_holding_duration": "p90_holding_duration_seconds",
        "first_tick_wait_P95_ms": "first_tick_wait_p95_ms",
    }
    for target, source in aliases.items():
        if target not in result:
            result[target] = result[source]
    return result


def strategy_summary(master: pd.DataFrame) -> pd.DataFrame:
    return (
        master.groupby(["strategy_id", "timeframe"], as_index=False)
        .agg(
            symbols_tested=("symbol", "nunique"),
            positive_Return_symbols=("Return_fee0", lambda x: int((x > 0).sum())),
            positive_BE_symbols=("BE_bps", lambda x: int((x > 0).sum())),
            positive_Return_BE_symbols=(
                "strategy_id",
                lambda x: 0,
            ),
            positive_5bp_symbols=("Return_5bp", lambda x: int((x > 0).sum())),
            median_Return=("Return_fee0", "median"),
            median_BE=("BE_bps", "median"),
            median_Turnover_pct=("Turnover_pct", "median"),
            median_nonflat_fraction=("nonflat_fraction", "median"),
            median_flat_fraction=("flat_fraction", "median"),
            median_holding_duration=("median_holding_duration_seconds", "median"),
            median_turnover_raw=("Turnover_raw", "median"),
        )
    )


def add_joint_positive_counts(summary: pd.DataFrame, master: pd.DataFrame) -> pd.DataFrame:
    joint = (
        master.assign(_joint=(master.Return_fee0 > 0) & (master.BE_bps > 0))
        .groupby(["strategy_id", "timeframe"], as_index=False)["_joint"]
        .sum()
        .rename(columns={"_joint": "_joint_count"})
    )
    result = summary.drop(columns=["positive_Return_BE_symbols"]).merge(
        joint, on=["strategy_id", "timeframe"], validate="one_to_one"
    )
    result = result.rename(columns={"_joint_count": "positive_Return_BE_symbols"})
    result["positive_Return_BE_symbols"] = result["positive_Return_BE_symbols"].astype(int)
    columns = [
        "strategy_id", "timeframe", "symbols_tested", "positive_Return_symbols",
        "positive_BE_symbols", "positive_Return_BE_symbols", "positive_5bp_symbols",
        "median_Return", "median_BE", "median_Turnover_pct",
        "median_nonflat_fraction", "median_flat_fraction",
        "median_holding_duration", "median_turnover_raw",
    ]
    return result[columns]


def symbol_summary(master: pd.DataFrame) -> pd.DataFrame:
    result = (
        master.assign(_joint=(master.Return_fee0 > 0) & (master.BE_bps > 0))
        .groupby(["symbol", "timeframe"], as_index=False)
        .agg(
            strategies_tested=("strategy_id", "nunique"),
            Return_BE_positive_count=("_joint", "sum"),
            Return_5bp_positive_count=("Return_5bp", lambda x: int((x > 0).sum())),
            median_Return=("Return_fee0", "median"),
            median_BE=("BE_bps", "median"),
            median_Turnover_pct=("Turnover_pct", "median"),
            median_nonflat_fraction=("nonflat_fraction", "median"),
            median_holding_duration=("median_holding_duration_seconds", "median"),
        )
    )
    result["Return_BE_positive_count"] = result["Return_BE_positive_count"].astype(int)
    result["_order"] = result.timeframe.map(TIMEFRAME_DELIVERY_ORDER)
    return result.sort_values(["_order", "symbol"]).drop(columns="_order")


def timeframe_summary(master: pd.DataFrame) -> pd.DataFrame:
    result = (
        master.assign(_joint=(master.Return_fee0 > 0) & (master.BE_bps > 0))
        .groupby("timeframe", as_index=False)
        .agg(
            total_cases=("strategy_id", "size"),
            Return_positive=("Return_fee0", lambda x: int((x > 0).sum())),
            BE_positive=("BE_bps", lambda x: int((x > 0).sum())),
            Return_BE_positive=("_joint", "sum"),
            Return_5bp_positive=("Return_5bp", lambda x: int((x > 0).sum())),
            median_Return=("Return_fee0", "median"),
            median_BE=("BE_bps", "median"),
            median_Turnover_pct=("Turnover_pct", "median"),
            median_nonflat_fraction=("nonflat_fraction", "median"),
            median_holding_duration=("median_holding_duration_seconds", "median"),
        )
    )
    result["Return_BE_positive"] = result["Return_BE_positive"].astype(int)
    result["_order"] = result.timeframe.map({name: i for i, name in enumerate(TIMEFRAMES)})
    return result.sort_values("_order").drop(columns="_order")


def shortlist(master: pd.DataFrame, summary: pd.DataFrame) -> pd.DataFrame:
    multi_keys = set(
        map(
            tuple,
            summary.loc[
                summary.positive_Return_BE_symbols >= 2,
                ["strategy_id", "timeframe"],
            ].to_records(index=False),
        )
    )
    included = master[
        master.apply(lambda row: (row.strategy_id, row.timeframe) in multi_keys, axis=1)
        | (master.Return_5bp > 0)
        | (master.nonflat_fraction >= 0.90)
        | (
            master.timeframe.isin(["10m", "15m"])
            & (master.Return_fee0 > 0)
            & (master.BE_bps > 0)
        )
    ].copy()

    def reasons(row: pd.Series) -> str:
        values = []
        if (row.strategy_id, row.timeframe) in multi_keys:
            values.append("MULTI_SYMBOL_POSITIVE")
        if row.Return_5bp > 0:
            values.append("FIVE_BP_SURVIVOR")
        if row.nonflat_fraction >= 0.90:
            values.append("PERSISTENT_POSITION")
        if row.timeframe == "10m" and row.Return_fee0 > 0 and row.BE_bps > 0:
            values.append("10M_RETURN_BE_POSITIVE")
        if row.timeframe == "15m" and row.Return_fee0 > 0 and row.BE_bps > 0:
            values.append("15M_RETURN_BE_POSITIVE")
        return ";".join(values)

    included["shortlist_reason"] = included.apply(reasons, axis=1)
    included["Return"] = included.Return_fee0
    included["BE"] = included.BE_bps
    included["median_holding_duration"] = included.median_holding_duration_seconds
    included["_order"] = included.timeframe.map(TIMEFRAME_DELIVERY_ORDER)
    included["_multi"] = included.shortlist_reason.str.contains("MULTI_SYMBOL_POSITIVE")
    included["_five"] = included.shortlist_reason.str.contains("FIVE_BP_SURVIVOR")
    included = included.sort_values(
        ["_order", "_multi", "_five", "BE", "nonflat_fraction", "strategy_id", "symbol"],
        ascending=[True, False, False, False, False, True, True],
    )
    return included[
        [
            "strategy_id", "symbol", "timeframe", "Return", "Return_5bp", "BE",
            "Turnover_pct", "MDD", "nonflat_fraction", "long_fraction",
            "short_fraction", "flat_fraction", "median_holding_duration",
            "shortlist_reason",
        ]
    ]


def normalize_persistent(master: pd.DataFrame) -> pd.DataFrame:
    frame = master[
        (master.nonflat_fraction >= 0.90)
        | (
            master.timeframe.isin(["10m", "15m"])
            & (master.Return_fee0 > 0)
            & (master.BE_bps > 0)
        )
    ].copy()
    frame["Return"] = frame.Return_fee0
    frame["BE"] = frame.BE_bps
    frame["completed_episode_count"] = frame.episode_count
    frame["median_holding_duration"] = frame.median_holding_duration_seconds
    frame["P90_holding_duration"] = frame.p90_holding_duration_seconds
    frame["shortlist_reason"] = np.select(
        [frame.Return_5bp > 0, frame.nonflat_fraction >= 0.90],
        ["FIVE_BP_SURVIVOR", "PERSISTENT_POSITION"],
        default="RETURN_BE_POSITIVE_10M_15M",
    )
    return frame.sort_values(
        ["nonflat_fraction", "BE_bps", "strategy_id", "symbol", "timeframe"],
        ascending=[False, False, True, True, True],
    )


def normalize_parameter_audit(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    result["parameter_role"] = result["role"]
    result["expected_effect_on_nonflat"] = result["expected_effect_on_flat_fraction"]
    result["expected_effect_on_holding_duration"] = "NOT_INFERRED_NO_SENSITIVITY_RUN"
    result["expected_effect_on_turnover"] = "NOT_INFERRED_NO_SENSITIVITY_RUN"
    first = [
        "strategy_id", "parameter", "canonical_value", "parameter_role",
        "expected_effect_on_nonflat", "expected_effect_on_holding_duration",
        "expected_effect_on_turnover", "safe_to_sensitivity_test", "reason",
    ]
    return result[first + [column for column in result if column not in first]]


def normalize_reference(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    result["median_state_duration"] = result.median_state_duration_minutes
    return result


def normalize_feasibility(frame: pd.DataFrame, master: pd.DataFrame) -> pd.DataFrame:
    classes = (
        master.groupby("strategy_id").persistence_structure_class
        .agg(lambda values: values.iloc[0] if values.nunique() == 1 else "INCONSISTENT")
    )
    result = frame.copy()
    result["directional_signal_available"] = result.directional_score_available
    result["always_in_market_capable"] = result.strategy_id.map(classes).eq(
        "ALWAYS_IN_MARKET_CAPABLE"
    )
    result["persistence_parameter_tunable"] = result.strategy_id.map(classes).eq(
        "PERSISTENCE_PARAMETER_TUNABLE"
    )
    result["hold_until_opposite_variant_possible"] = result.variant_mechanically_possible
    first = [
        "strategy_id", "directional_signal_available", "canonical_flat_reason",
        "always_in_market_capable", "persistence_parameter_tunable",
        "semantic_change_required", "hold_until_opposite_variant_possible",
        "recommendation",
    ]
    return result[first + [column for column in result if column not in first]]


def core_counts(master: pd.DataFrame, summary: pd.DataFrame) -> dict[str, int]:
    return {
        "strategies": int(master.strategy_id.nunique()),
        "symbols": int(master.symbol.nunique()),
        "timeframes": int(master.timeframe.nunique()),
        "logical_cases": len(master),
        "completed": int(master.status.eq("COMPLETED").sum()),
        "failures": int(master.status.ne("COMPLETED").sum()),
        "10m_return_be_positive": int(
            ((master.timeframe == "10m") & (master.Return_fee0 > 0) & (master.BE_bps > 0)).sum()
        ),
        "15m_return_be_positive": int(
            ((master.timeframe == "15m") & (master.Return_fee0 > 0) & (master.BE_bps > 0)).sum()
        ),
        "multi_symbol_positive": int((summary.positive_Return_BE_symbols >= 2).sum()),
        "five_bp_survivors": int((master.Return_5bp > 0).sum()),
        "near_always_in_market": int((master.nonflat_fraction >= 0.90).sum()),
    }


def key_answers(
    master: pd.DataFrame,
    summary: pd.DataFrame,
    by_symbol: pd.DataFrame,
    by_timeframe: pd.DataFrame,
    params: pd.DataFrame,
    validation: dict[str, Any],
) -> pd.DataFrame:
    tf = by_timeframe.set_index("timeframe")

    def comparison(column: str, label: str) -> str:
        base = float(tf.loc["1m", column])
        ten = float(tf.loc["10m", column])
        fifteen = float(tf.loc["15m", column])
        direction = "YES" if ten >= base and fifteen >= base else "NO"
        if label == "turnover":
            direction = "YES" if ten <= base and fifteen <= base else "NO"
        return f"{direction}; 1m={base:.6g}, 10m={ten:.6g}, 15m={fifteen:.6g}"

    symbol_rank = (
        by_symbol.groupby("symbol", as_index=False).Return_BE_positive_count.sum()
        .sort_values(["Return_BE_positive_count", "symbol"], ascending=[False, True])
        .head(5)
    )
    multi = summary[summary.positive_Return_BE_symbols >= 2].sort_values(
        ["positive_Return_BE_symbols", "positive_5bp_symbols", "strategy_id", "timeframe"],
        ascending=[False, False, True, True],
    ).head(10)
    classes = master.groupby("strategy_id").persistence_structure_class.first()
    structural = sorted(classes[classes.eq("STRUCTURALLY_FLAT_REQUIRED")].index)
    top = validation["top_persistent_strategy_ids"]
    rows = [
        ("10m 有多少正 Return+BE？", str(int(tf.loc["10m", "Return_BE_positive"]))),
        ("15m 有多少正 Return+BE？", str(int(tf.loc["15m", "Return_BE_positive"]))),
        ("10m/15m 是否整体降低 turnover？", comparison("median_Turnover_pct", "turnover")),
        ("10m/15m 是否提高 holding duration？", comparison("median_holding_duration", "holding")),
        ("10m/15m 是否提高 nonflat_fraction？", comparison("median_nonflat_fraction", "nonflat")),
        (
            "哪些币种正结果最多？",
            "; ".join(f"{row.symbol}={int(row.Return_BE_positive_count)}" for row in symbol_rank.itertuples()),
        ),
        (
            "哪些 strategy 在多个币种上都正？",
            "; ".join(
                f"{row.strategy_id}/{row.timeframe}={int(row.positive_Return_BE_symbols)}"
                for row in multi.itertuples()
            ),
        ),
        ("哪些 strategy 最像 reference 的 ±1 持仓？", "; ".join(top)),
        (
            "哪些 strategy persistence 可通过已有参数调整？",
            f"{params.strategy_id.nunique()} strategies; "
            + "; ".join(sorted(params.strategy_id.unique())[:10]),
        ),
        (
            "哪些必须改语义才能变成 hold-until-opposite？",
            f"{len(structural)} strategies; " + "; ".join(structural[:10]),
        ),
    ]
    return pd.DataFrame(rows, columns=["question", "result"])


def package(root: Path) -> dict[str, Any]:
    source_paths = {
        name: root / name
        for name in (
            "boss_multitimeframe_tick_master.csv",
            "reference_position_behavior.csv",
            "validation_summary.json",
        )
    }
    missing = [str(path) for path in source_paths.values() if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"required source artifacts missing: {missing}")
    protected_source_names = {"validation_summary.json"}
    source_hashes = {
        name: sha256_file(path)
        for name, path in source_paths.items()
        if name in protected_source_names
    }
    master = pd.read_csv(source_paths["boss_multitimeframe_tick_master.csv"])
    missing_columns = sorted(required_master_columns() - set(master.columns))
    if missing_columns:
        raise ValueError(f"master columns missing: {missing_columns}")
    if master[["strategy_id", "symbol", "timeframe"]].duplicated().any():
        raise ValueError("duplicate logical cases in master")
    if set(master.symbol) != set(SYMBOLS) or set(master.timeframe) != set(TIMEFRAMES):
        raise ValueError("symbol/timeframe scope mismatch")
    original_master = master.copy()
    master = normalize_master_delivery_schema(master)

    strategy = add_joint_positive_counts(strategy_summary(master), master)
    counts = core_counts(master, strategy)
    if counts != EXPECTED:
        raise ValueError(f"core count reconciliation failed: {counts} != {EXPECTED}")
    validation = json.loads(source_paths["validation_summary.json"].read_text(encoding="utf-8-sig"))
    if validation.get("status") != "PASSED":
        raise ValueError("core validation summary is not PASSED")

    by_symbol = symbol_summary(master)
    by_timeframe = timeframe_summary(master)
    candidates = shortlist(master, strategy)
    persistent = normalize_persistent(master)
    parameter_source = pd.read_csv(root / "persistence_parameter_audit.csv")
    params = normalize_parameter_audit(parameter_source)
    reference = normalize_reference(pd.read_csv(source_paths["reference_position_behavior.csv"]))
    if (
        len(reference) != 18
        or set(reference.reference_strategy) != {"conservative", "aggressive"}
        or reference.groupby("reference_strategy").symbol.nunique().min() != 9
    ):
        raise ValueError("reference behavior reconciliation failed")
    fraction_error = (
        reference.long_fraction + reference.short_fraction + reference.flat_fraction - 1.0
    ).abs().max()
    if fraction_error > 1e-12:
        raise ValueError(f"reference position fractions do not sum to one: {fraction_error}")
    feasibility = normalize_feasibility(
        pd.read_csv(root / "hold_until_opposite_feasibility.csv"), master
    )
    answers = key_answers(master, strategy, by_symbol, by_timeframe, params, validation)
    definitions = pd.DataFrame(
        [
            {
                "metric": "Multi-symbol positive",
                "value": counts["multi_symbol_positive"],
                "unit": "strategy × timeframe",
                "definition": "positive Return and signed BE on at least 2 of 9 symbols",
                "denominator": len(strategy),
            },
            {
                "metric": "Near-always-in-market",
                "value": counts["near_always_in_market"],
                "unit": "strategy × symbol × timeframe case",
                "definition": "nonflat_fraction >= 0.90",
                "denominator": len(master),
            },
        ]
    )

    outputs = {
        "boss_multitimeframe_strategy_summary.csv": strategy,
        "boss_multitimeframe_symbol_summary.csv": by_symbol,
        "boss_multitimeframe_timeframe_summary.csv": by_timeframe,
        "boss_multitimeframe_candidates.csv": candidates,
        "persistent_position_candidates.csv": persistent,
        "persistence_parameter_audit.csv": params,
        "reference_position_behavior.csv": reference,
        "hold_until_opposite_feasibility.csv": feasibility,
        "boss_multitimeframe_key_answers.csv": answers,
        "boss_multitimeframe_definitions.csv": definitions,
    }
    for name, frame in outputs.items():
        atomic_csv(root / name, frame)
    atomic_csv(root / "boss_multitimeframe_tick_master.csv", master)
    reloaded_master = pd.read_csv(root / "boss_multitimeframe_tick_master.csv")
    pd.testing.assert_frame_equal(
        reloaded_master[original_master.columns],
        original_master,
        check_dtype=False,
        check_exact=False,
        rtol=1e-15,
        atol=1e-15,
    )

    figures = sorted((root / "figures").glob("*.png"))
    if len(figures) < 3:
        raise ValueError(f"insufficient PNG figures: {len(figures)}")
    if not (root / "figures/reference_position_behavior_comparison.png").is_file():
        raise ValueError("reference behavior comparison figure is missing")
    after_hashes = {
        name: sha256_file(path)
        for name, path in source_paths.items()
        if name in protected_source_names
    }
    if after_hashes != source_hashes:
        raise ValueError("protected source result changed during packaging")

    result = {
        "status": "PASSED",
        **counts,
        "multi_symbol_positive_definition": definitions.iloc[0].definition,
        "near_always_in_market_definition": definitions.iloc[1].definition,
        "strategy_summary_rows": len(strategy),
        "symbol_summary_rows": len(by_symbol),
        "timeframe_summary_rows": len(by_timeframe),
        "candidate_rows": len(candidates),
        "persistent_rows": len(persistent),
        "parameter_audit_rows": len(params),
        "reference_rows": len(reference),
        "figure_count": len(figures),
        "protected_source_hash_changes": 0,
        "computed_master_value_changes": 0,
        "backtests_rerun": 0,
        "tick_index_rebuilt": 0,
        "strategy_semantic_changes": 0,
        "canonical_parameter_changes": 0,
        "symbols_changed": 0,
        "timeframes_changed": 0,
        "xlsx_status": "SKIPPED_OPTIONAL",
        "outputs": sorted(outputs),
    }
    atomic_json(root / "csv_packaging_validation_summary.json", result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=RESULT_ROOT)
    args = parser.parse_args()
    print(json.dumps(package(args.root), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
