#!/usr/bin/env python3
"""Build the boss persistent-position follow-up from completed matrix results.

This module is deliberately post-processing only.  It never invokes strategy
execution or rebuilds the compact tick index.
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
import yaml


ROOT = Path(__file__).resolve().parents[2]
RESULT_ROOT = ROOT / "outputs/baseline_evaluation/boss_multitimeframe_tick_screen"
OUTPUT_NAME = "persistent_v2_followup"
SYMBOLS = (
    "XRPUSDT", "DOGEUSDT", "SUIUSDT", "BNBUSDT", "ETHUSDT",
    "BTCUSDT", "1000PEPEUSDT", "SOLUSDT", "ADAUSDT",
)
TIMEFRAMES = ("1m", "5m", "10m", "15m")
TF_ORDER = {"15m": 0, "10m": 1, "5m": 2, "1m": 3}
PROVENANCE_KEYS = {
    "source_registry_id", "semantic_provenance", "contracts_applied",
    "defaulted_parameters",
}


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


def truthy(series: pd.Series) -> pd.Series:
    if series.dtype == bool:
        return series
    return series.astype(str).str.lower().eq("true")


def validate_metrics(metrics: pd.DataFrame) -> None:
    if len(metrics) != 9_612:
        raise ValueError(f"expected 9,612 metrics rows, found {len(metrics)}")
    if metrics[["strategy_id", "symbol", "timeframe"]].duplicated().any():
        raise ValueError("duplicate logical cases in v2 metrics")
    persistent = truthy(metrics.directionally_persistent)
    always = truthy(metrics.always_in_market)
    expected = {
        "persistent": 930,
        "always": 4_175,
        "always_not_persistent": 3_245,
    }
    observed = {
        "persistent": int(persistent.sum()),
        "always": int(always.sum()),
        "always_not_persistent": int((always & ~persistent).sum()),
    }
    if observed != expected:
        raise ValueError(f"v2 persistence reconciliation failed: {observed} != {expected}")
    fraction_error = (
        metrics.long_fraction_v2 + metrics.short_fraction_v2 + metrics.flat_fraction_v2 - 1.0
    ).abs().max()
    nonflat_error = (
        metrics.nonflat_fraction_v2
        - metrics.long_fraction_v2
        - metrics.short_fraction_v2
    ).abs().max()
    if fraction_error > 1e-12 or nonflat_error > 1e-12:
        raise ValueError(
            f"position fraction reconciliation failed: total={fraction_error}, nonflat={nonflat_error}"
        )


def strategy_timeframe_summary(metrics: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for (strategy, timeframe), group in metrics.groupby(["strategy_id", "timeframe"], sort=True):
        persistent = truthy(group.directionally_persistent)
        always = truthy(group.always_in_market)
        persistent_group = group[persistent]
        rows.append(
            {
                "strategy_id": strategy,
                "timeframe": timeframe,
                "symbols_tested": int(group.symbol.nunique()),
                "persistent_symbol_count": int(persistent.sum()),
                "always_in_market_symbol_count": int(always.sum()),
                "persistent_symbol_fraction": float(persistent.mean()),
                "median_nonflat_fraction": float(group.nonflat_fraction_v2.median()),
                "median_directional_run_hours": float(group.median_directional_run_hours.median()),
                "median_P90_directional_run_hours": float(group.P90_directional_run_hours.median()),
                "median_sign_switches_per_day": float(group.sign_switches_per_day.median()),
                "median_turnover_raw": float(group.turnover_raw.median()),
                "median_turnover_pct": float(group.turnover_percent.median()),
                "positive_Return_symbol_count": int((group.Return > 0).sum()),
                "positive_BE_symbol_count": int((group.BE > 0).sum()),
                "Return_BE_positive_symbol_count": int(((group.Return > 0) & (group.BE > 0)).sum()),
                "positive_5bp_symbol_count": int((group.Return_5bp > 0).sum()),
                "Return_BE_positive_persistent_symbols": int(
                    (persistent & (group.Return > 0) & (group.BE > 0)).sum()
                ),
                "5bp_positive_persistent_symbols": int((persistent & (group.Return_5bp > 0)).sum()),
                "median_Return": float(group.Return.median()),
                "median_BE": float(group.BE.median()),
                "median_5bp_Return": float(group.Return_5bp.median()),
                "persistent_case_median_Return": (
                    float(persistent_group.Return.median()) if len(persistent_group) else np.nan
                ),
                "persistent_case_median_BE": (
                    float(persistent_group.BE.median()) if len(persistent_group) else np.nan
                ),
                "persistent_case_median_5bp_Return": (
                    float(persistent_group.Return_5bp.median()) if len(persistent_group) else np.nan
                ),
            }
        )
    return pd.DataFrame(rows)


def cross_symbol_matrix(metrics: pd.DataFrame, summary: pd.DataFrame) -> pd.DataFrame:
    flags = metrics.assign(_persistent=truthy(metrics.directionally_persistent)).pivot(
        index=["strategy_id", "timeframe"], columns="symbol", values="_persistent"
    )
    flags = flags.reindex(columns=SYMBOLS).fillna(False).astype(bool)
    flags.columns = [f"{symbol}_persistent" for symbol in flags.columns]
    result = flags.reset_index()
    result["persistent_symbol_count"] = result.filter(like="_persistent").sum(axis=1)
    result = result.merge(
        summary[
            [
                "strategy_id", "timeframe", "Return_BE_positive_persistent_symbols",
                "5bp_positive_persistent_symbols",
            ]
        ],
        on=["strategy_id", "timeframe"],
        validate="one_to_one",
    )
    return result


def economic_matrix(metrics: pd.DataFrame) -> pd.DataFrame:
    frame = metrics[truthy(metrics.directionally_persistent)].copy()
    frame["persistent_economic_class"] = np.select(
        [frame.Return_5bp > 0, (frame.Return > 0) & (frame.BE > 0)],
        ["PERSISTENT_5BP_SURVIVOR", "PERSISTENT_RETURN_BE_POSITIVE"],
        default="PERSISTENT_ONLY",
    )
    columns = [
        "strategy_id", "symbol", "timeframe", "Return", "Return_5bp", "BE",
        "turnover_raw", "turnover_percent", "MDD", "nonflat_fraction_v2",
        "median_directional_run_hours", "P90_directional_run_hours",
        "sign_switches_per_day", "persistent_economic_class",
    ]
    return frame[columns].sort_values(["timeframe", "strategy_id", "symbol"])


def shortlist(summary: pd.DataFrame) -> pd.DataFrame:
    result = summary[summary.persistent_symbol_count > 0].copy()
    result["shortlist_classes"] = result.apply(
        lambda row: ";".join(
            label
            for condition, label in (
                (row.persistent_symbol_count >= 2, "MULTI_SYMBOL_PERSISTENT"),
                (row.persistent_symbol_count >= 5, "BROADLY_PERSISTENT"),
                (row.Return_BE_positive_persistent_symbols >= 1, "PERSISTENT_AND_POSITIVE"),
                (row["5bp_positive_persistent_symbols"] >= 1, "PERSISTENT_AND_COST_SURVIVING"),
                (row.Return_BE_positive_persistent_symbols >= 2, "MULTI_SYMBOL_PERSISTENT_POSITIVE"),
                (row["5bp_positive_persistent_symbols"] >= 2, "MULTI_SYMBOL_PERSISTENT_5BP"),
            )
            if condition
        ),
        axis=1,
    )
    result["__tf"] = result.timeframe.map(TF_ORDER)
    result = result.sort_values(
        [
            "__tf", "5bp_positive_persistent_symbols",
            "Return_BE_positive_persistent_symbols", "persistent_symbol_count",
            "median_directional_run_hours", "median_sign_switches_per_day",
            "median_turnover_raw", "strategy_id",
        ],
        ascending=[True, False, False, False, False, True, True, True],
    ).drop(columns="__tf")
    result.insert(0, "descriptive_rank", np.arange(1, len(result) + 1))
    result["ranking_contract"] = (
        "15m,10m,5m,1m; 5bp-positive persistent symbols DESC; Return+BE-positive "
        "persistent symbols DESC; persistent symbols DESC; median run DESC; switches/day ASC; "
        "turnover ASC; no weighted score"
    )
    return result.reset_index(drop=True)


def timeframe_effect(metrics: pd.DataFrame) -> pd.DataFrame:
    value_map = {
        "nonflat_fraction_v2": "nonflat",
        "median_directional_run_hours": "median_run_hours",
        "sign_switches_per_day": "switches_per_day",
        "turnover_raw": "turnover_raw",
        "Return": "Return",
        "BE": "BE",
        "Return_5bp": "Return_5bp",
    }
    wide = metrics.pivot(index=["strategy_id", "symbol"], columns="timeframe", values=list(value_map))
    rows: list[dict[str, Any]] = []
    for key, values in wide.iterrows():
        row: dict[str, Any] = {"strategy_id": key[0], "symbol": key[1]}
        for source, label in value_map.items():
            for timeframe in TIMEFRAMES:
                row[f"{timeframe}_{label}"] = float(values[(source, timeframe)])
            for timeframe in ("5m", "10m", "15m"):
                row[f"delta_{timeframe}_vs_1m_{label}"] = (
                    row[f"{timeframe}_{label}"] - row[f"1m_{label}"]
                )
        rows.append(row)
    return pd.DataFrame(rows)


def timeframe_effect_summary(effect: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for timeframe in ("5m", "10m", "15m"):
        run = effect[f"delta_{timeframe}_vs_1m_median_run_hours"]
        switches = effect[f"delta_{timeframe}_vs_1m_switches_per_day"]
        turnover = effect[f"delta_{timeframe}_vs_1m_turnover_raw"]
        ret = effect[f"delta_{timeframe}_vs_1m_Return"]
        be = effect[f"delta_{timeframe}_vs_1m_BE"]
        all_three = (run > 0) & (switches < 0) & (turnover < 0) & (be > 0)
        rows.append(
            {
                "comparison": f"{timeframe}_vs_1m",
                "case_count": len(effect),
                "increases_median_directional_run_count": int((run > 0).sum()),
                "increases_median_directional_run_fraction": float((run > 0).mean()),
                "decreases_switches_per_day_count": int((switches < 0).sum()),
                "decreases_switches_per_day_fraction": float((switches < 0).mean()),
                "decreases_turnover_count": int((turnover < 0).sum()),
                "decreases_turnover_fraction": float((turnover < 0).mean()),
                "improves_BE_count": int((be > 0).sum()),
                "improves_BE_fraction": float((be > 0).mean()),
                "improves_Return_count": int((ret > 0).sum()),
                "improves_Return_fraction": float((ret > 0).mean()),
                "improves_persistence_turnover_BE_count": int(all_three.sum()),
                "improves_persistence_turnover_BE_fraction": float(all_three.mean()),
            }
        )
    return pd.DataFrame(rows)


def reference_like(metrics: pd.DataFrame, reference: pd.DataFrame) -> pd.DataFrame:
    persistent = metrics[truthy(metrics.directionally_persistent)].copy()
    ref = reference.copy()
    ref["elapsed_days"] = (
        pd.to_datetime(ref.end_timestamp, utc=True) - pd.to_datetime(ref.start_timestamp, utc=True)
    ).dt.total_seconds() / 86_400.0
    ref["switches_per_day"] = ref.sign_change_count / ref.elapsed_days
    ref["median_directional_run_hours"] = ref.median_state_duration_minutes / 60.0
    rows: list[dict[str, Any]] = []
    for symbol, group in persistent.groupby("symbol"):
        matches = ref[ref.symbol == symbol]
        for case in group.itertuples(index=False):
            for reference_row in matches.itertuples(index=False):
                rows.append(
                    {
                        "strategy_id": case.strategy_id,
                        "symbol": symbol,
                        "timeframe": case.timeframe,
                        "reference_strategy": reference_row.reference_strategy,
                        "nonflat_fraction": case.nonflat_fraction_v2,
                        "reference_nonflat_fraction": reference_row.nonflat_fraction,
                        "delta_nonflat_fraction": case.nonflat_fraction_v2 - reference_row.nonflat_fraction,
                        "median_directional_run_hours": case.median_directional_run_hours,
                        "reference_median_state_hours": reference_row.median_directional_run_hours,
                        "delta_median_run_hours": case.median_directional_run_hours - reference_row.median_directional_run_hours,
                        "switches_per_day": case.sign_switches_per_day,
                        "reference_switches_per_day": reference_row.switches_per_day,
                        "delta_switches_per_day": case.sign_switches_per_day - reference_row.switches_per_day,
                        "long_fraction": case.long_fraction_v2,
                        "reference_long_fraction": reference_row.long_fraction,
                        "delta_long_fraction": case.long_fraction_v2 - reference_row.long_fraction,
                        "short_fraction": case.short_fraction_v2,
                        "reference_short_fraction": reference_row.short_fraction,
                        "delta_short_fraction": case.short_fraction_v2 - reference_row.short_fraction,
                        "comparison_contract": "explicit deltas only; no weighted distance or Return comparison",
                    }
                )
    return pd.DataFrame(rows).sort_values(
        ["reference_strategy", "symbol", "timeframe", "strategy_id"]
    )


def config(strategy_id: str) -> dict[str, Any]:
    return yaml.safe_load(
        (ROOT / "strategies" / strategy_id / "config.yaml").read_text(encoding="utf-8")
    ) or {}


def structure_audit(
    strategies: list[str], parameter_audit: pd.DataFrame, class_by_strategy: dict[str, str]
) -> pd.DataFrame:
    tunable = set(parameter_audit.strategy_id.astype(str))
    rows = []
    for strategy_id in sorted(strategies):
        source = config(strategy_id)
        params = {k: v for k, v in source.get("params", {}).items() if k not in PROVENANCE_KEYS}
        family = str(params.get("family", ""))
        keys = set(params)
        hold_native = family in {
            "bollinger_width_cross", "sma_price_cross", "ema_crossover", "psar_reversal",
            "macd_zero_persistent", "ao_zero_persistent", "ema_ao_persistent",
        }
        hysteresis = any("neutral" in key for key in keys) or (
            {"lower_threshold", "upper_threshold"} <= keys
        )
        confirmation = any("consecutive" in key or "confirmation" in key for key in keys)
        lookback = any("window" in key or "lookback" in key for key in keys)
        threshold = any("threshold" in key for key in keys)
        if hold_native:
            mapping = "BINARY_LONG_SHORT_STATE"
            reason = "canonical opposite crossover/reversal or persistent sign state"
        elif hysteresis:
            mapping = "HYSTERESIS_WITH_FLAT_STATE"
            reason = "neutral/entry-exit threshold band controls directional state duration"
        elif confirmation:
            mapping = "CONFIRMED_EVENT_STATE"
            reason = "existing confirmation/persistence bars delay state changes"
        elif lookback:
            mapping = "LOOKBACK_DRIVEN_SIGNAL_WITH_FLAT_STATE"
            reason = "lookback and explicit source exits govern directional runs"
        else:
            mapping = "EVENT_ENTRY_EXIT_WITH_FLAT_STATE"
            reason = "explicit event entry/exit semantics create flat waiting periods"
        existing_class = str(
            source.get("metadata", {}).get("persistence_structure_class", "")
        )
        # The completed matrix classification remains authoritative.
        classification = class_by_strategy[strategy_id]
        rows.append(
            {
                "strategy_id": strategy_id,
                "family": family,
                "position_mapping_type": mapping,
                "flat_state_explicit": not hold_native,
                "hold_until_opposite_native": hold_native,
                "hysteresis_present": hysteresis,
                "confirmation_or_persistence_bars_present": confirmation,
                "persistence_parameter_present": strategy_id in tunable,
                "threshold_parameter_present": threshold,
                "lookback_parameter_present": lookback,
                "persistence_structure_class": classification,
                "main_reason_for_long_directional_runs": reason,
                "config_metadata_class_if_present": existing_class,
            }
        )
    return pd.DataFrame(rows)


def hold_feasibility_v2(feasibility: pd.DataFrame, structures: pd.DataFrame) -> pd.DataFrame:
    base = feasibility.merge(
        structures[["strategy_id", "persistence_structure_class"]],
        on="strategy_id", how="left", validate="one_to_one",
    )
    structural = base.persistence_structure_class.eq("STRUCTURALLY_FLAT_REQUIRED")
    directional = truthy(base.directional_signal_available)
    return pd.DataFrame(
        {
            "strategy_id": base.strategy_id,
            "directional_state_available": directional,
            "canonical_flat_reason": base.canonical_flat_reason.fillna(""),
            "variant_possible": structural & directional,
            "semantic_change_required": structural,
            "likely_effect_nonflat": np.where(structural, "INCREASE", "NOT_APPLICABLE"),
            "likely_effect_holding_duration": np.where(structural, "INCREASE", "NOT_APPLICABLE"),
            "likely_effect_turnover": np.where(structural, "LIKELY_DECREASE_OR_AMBIGUOUS", "NOT_APPLICABLE"),
            "needs_separate_authorization": structural,
            "recommendation": np.where(
                structural & directional,
                "MECHANICALLY_POSSIBLE_BUT_DO_NOT_IMPLEMENT_WITHOUT_AUTHORIZATION",
                "USE_EXISTING_CANONICAL_STRUCTURE",
            ),
        }
    )


def key_answers(
    metrics: pd.DataFrame,
    summary: pd.DataFrame,
    effect_summary: pd.DataFrame,
    parameter_audit: pd.DataFrame,
    hold: pd.DataFrame,
    sensitivity_path: Path,
) -> pd.DataFrame:
    persistent = truthy(metrics.directionally_persistent)
    p = metrics[persistent]
    lookup = effect_summary.set_index("comparison")
    improved = 0
    if sensitivity_path.is_file():
        sensitivity = pd.read_csv(sensitivity_path)
        if "persistence_improved" in sensitivity:
            improved = int(sensitivity.loc[truthy(sensitivity.persistence_improved), "strategy_id"].nunique())
    answers = [
        ("930 persistent cases correspond to how many unique strategies?", int(p.strategy_id.nunique()), "strategy_id"),
        ("How many strategy×timeframe combinations are persistent on >=2 symbols?", int((summary.persistent_symbol_count >= 2).sum()), "strategy×timeframe"),
        ("How many are persistent on >=5 symbols?", int((summary.persistent_symbol_count >= 5).sum()), "strategy×timeframe"),
        ("How many 10m persistent cases are Return+BE positive?", int(((p.timeframe == "10m") & (p.Return > 0) & (p.BE > 0)).sum()), "strategy×symbol×timeframe"),
        ("How many 15m persistent cases are Return+BE positive?", int(((p.timeframe == "15m") & (p.Return > 0) & (p.BE > 0)).sum()), "strategy×symbol×timeframe"),
        ("How many 10m/15m persistent cases survive 5bp?", int((p.timeframe.isin(["10m", "15m"]) & (p.Return_5bp > 0)).sum()), "strategy×symbol×timeframe"),
        ("Does 10m generally increase holding duration vs 1m?", f"{int(lookup.loc['10m_vs_1m','increases_median_directional_run_count'])}/{int(lookup.loc['10m_vs_1m','case_count'])}", "strategy×symbol paired cases"),
        ("Does 15m generally increase holding duration vs 1m?", f"{int(lookup.loc['15m_vs_1m','increases_median_directional_run_count'])}/{int(lookup.loc['15m_vs_1m','case_count'])}", "strategy×symbol paired cases"),
        ("Does 10m/15m reduce turnover?", f"10m {int(lookup.loc['10m_vs_1m','decreases_turnover_count'])}/{int(lookup.loc['10m_vs_1m','case_count'])}; 15m {int(lookup.loc['15m_vs_1m','decreases_turnover_count'])}/{int(lookup.loc['15m_vs_1m','case_count'])}", "strategy×symbol paired cases"),
        ("How many strategies are persistence-parameter-tunable?", int(parameter_audit.strategy_id.nunique()), "strategy_id"),
        ("How many can improve persistence using an existing parameter?", improved, "strategy_id; populated after sensitivity"),
        ("How many require a semantic HOLD_UNTIL_OPPOSITE change?", int(truthy(hold.semantic_change_required).sum()), "strategy_id"),
    ]
    return pd.DataFrame(answers, columns=["question", "result", "denominator"])


def build(root: Path, output_root: Path | None = None) -> dict[str, Any]:
    output = output_root or root / OUTPUT_NAME
    metrics_path = root / "persistent_position_metrics_v2.csv"
    master_path = root / "boss_multitimeframe_tick_master.csv"
    protected_before = {path.name: sha256_file(path) for path in (metrics_path, master_path)}
    metrics = pd.read_csv(metrics_path)
    validate_metrics(metrics)
    summary = strategy_timeframe_summary(metrics)
    cross = cross_symbol_matrix(metrics, summary)
    economic = economic_matrix(metrics)
    boss_shortlist = shortlist(summary)
    effect = timeframe_effect(metrics)
    effect_summary = timeframe_effect_summary(effect)
    reference = reference_like(metrics, pd.read_csv(root / "reference_position_behavior.csv"))
    parameter_audit_all = pd.read_csv(root / "persistence_parameter_audit.csv")
    class_by_strategy = (
        metrics[["strategy_id", "persistence_structure_class"]]
        .drop_duplicates()
        .set_index("strategy_id")
        .persistence_structure_class.to_dict()
    )
    tunable_ids = {
        strategy_id for strategy_id, value in class_by_strategy.items()
        if value == "PERSISTENCE_PARAMETER_TUNABLE"
    }
    parameter_audit = parameter_audit_all[
        parameter_audit_all.strategy_id.astype(str).isin(tunable_ids)
    ].copy()
    structures = structure_audit(
        sorted(metrics.strategy_id.unique()), parameter_audit, class_by_strategy
    )
    hold = hold_feasibility_v2(
        pd.read_csv(root / "hold_until_opposite_feasibility.csv"), structures
    )
    outputs = {
        "persistent_strategy_timeframe_summary.csv": summary,
        "persistent_cross_symbol_matrix.csv": cross,
        "persistent_economic_matrix.csv": economic,
        "boss_persistent_directional_shortlist.csv": boss_shortlist,
        "timeframe_persistence_effect.csv": effect,
        "timeframe_persistence_effect_summary.csv": effect_summary,
        "reference_like_position_candidates.csv": reference,
        "persistence_structure_audit_v2.csv": structures,
        "hold_until_opposite_feasibility_v2.csv": hold,
    }
    for name, frame in outputs.items():
        atomic_csv(output / name, frame)
    key = key_answers(
        metrics, summary, effect_summary, parameter_audit, hold,
        output / "persistence_improvable_strategies.csv",
    )
    atomic_csv(output / "boss_persistent_key_answers.csv", key)
    protected_after = {path.name: sha256_file(path) for path in (metrics_path, master_path)}
    if protected_before != protected_after:
        raise ValueError("authoritative completed result changed during follow-up")
    persistent = metrics[truthy(metrics.directionally_persistent)]
    result = {
        "status": "POSTPROCESSING_PASSED_SENSITIVITY_PENDING",
        "total_cases": len(metrics),
        "persistent_cases": len(persistent),
        "unique_persistent_strategies": int(persistent.strategy_id.nunique()),
        "persistent_strategy_timeframe_combinations": int((summary.persistent_symbol_count > 0).sum()),
        "persistent_on_at_least_2_symbols": int((summary.persistent_symbol_count >= 2).sum()),
        "persistent_on_at_least_5_symbols": int((summary.persistent_symbol_count >= 5).sum()),
        "10m_persistent_Return_BE_positive": int(((persistent.timeframe == "10m") & (persistent.Return > 0) & (persistent.BE > 0)).sum()),
        "15m_persistent_Return_BE_positive": int(((persistent.timeframe == "15m") & (persistent.Return > 0) & (persistent.BE > 0)).sum()),
        "10m_15m_persistent_5bp_survivors": int((persistent.timeframe.isin(["10m", "15m"]) & (persistent.Return_5bp > 0)).sum()),
        "persistence_parameter_tunable_strategies": int(parameter_audit.strategy_id.nunique()),
        "hold_until_opposite_semantic_change_strategies": int(truthy(hold.semantic_change_required).sum()),
        "canonical_result_hashes_unchanged": True,
        "full_matrix_backtests_rerun": 0,
        "tick_index_rebuilt": 0,
        "phase3_optimizer_invoked": False,
        "output_root": str(output),
    }
    atomic_json(output / "followup_postprocessing_validation.json", result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=RESULT_ROOT)
    parser.add_argument("--output-root", type=Path)
    args = parser.parse_args()
    print(json.dumps(build(args.root, args.output_root), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
