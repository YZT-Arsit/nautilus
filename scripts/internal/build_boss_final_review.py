#!/usr/bin/env python3
"""Build the frozen 14-group boss final review without new research runs."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import TwoSlopeNorm
import numpy as np
import pandas as pd
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.internal.build_boss_persistence_v2 import regime_figure
from scripts.internal.build_boss_10m15m_distillation import (
    atomic_csv,
    atomic_json,
    case_review,
    config_snapshot,
    sha256_file,
    truthy,
)


DEFAULT_ROOT = PROJECT_ROOT / "outputs/baseline_evaluation/boss_multitimeframe_tick_screen"
DISTILLATION = "final_candidate_distillation"
FOLLOWUP = "persistent_v2_followup"
OUTPUT_NAME = "boss_final_review"
SYMBOLS = (
    "XRPUSDT", "DOGEUSDT", "SUIUSDT", "BNBUSDT", "ETHUSDT",
    "BTCUSDT", "1000PEPEUSDT", "SOLUSDT", "ADAUSDT",
)
TIMEFRAMES = ("1m", "5m", "10m", "15m")
PRIMARY_TIMEFRAMES = ("10m", "15m")


PERSISTENCE_DRIVER = {
    "P5F_RSI_THRESHOLD_DIVERGENCE": "HYSTERESIS",
    "MA_MACD_CONFLUENCE": "PERSISTENCE_CONFIRMATION",
    "RSI_TREND_PULLBACK_MA60": "LONG_LOOKBACK",
    "BIAS_FRACTAL_REVERSAL": "RARE_EXIT_TRIGGER",
    "MACD_CCI_CONFLUENCE": "HYSTERESIS",
    "session_vwap_volume_mean": "OTHER_SESSION_STATE",
    "CCI_AO_CONFLUENCE": "PERSISTENCE_CONFIRMATION",
    "CCI_MA_REVERSAL": "HYSTERESIS",
    "TRIPLE_MA_DONCHIAN": "LONG_LOOKBACK",
    "rsi_turn_candle": "HYSTERESIS",
    "ADX_DUAL_MA": "SLOW_CROSS",
    "MTF_DAILY_4H_CCI": "LONG_LOOKBACK",
    "MTF_TRIPLE_FRACTAL": "RARE_EXIT_TRIGGER",
    "CCI_MA20_PULLBACK": "HYSTERESIS",
}


def source_hashes(paths: dict[str, Path]) -> dict[str, str]:
    return {name: sha256_file(path) for name, path in paths.items()}


def validate_frozen_membership(final: pd.DataFrame) -> None:
    if len(final) != 24:
        raise ValueError(f"frozen shortlist must have 24 rows, found {len(final)}")
    if final.semantic_execution_hash.nunique() != 14:
        raise ValueError("frozen shortlist does not resolve to 14 semantic groups")
    if final[["semantic_execution_hash", "timeframe"]].duplicated().any():
        raise ValueError("duplicate semantic-group × timeframe frozen candidate")
    levels = final.groupby(["timeframe", "shortlist_level"]).size().to_dict()
    expected = {
        ("10m", "LEVEL_A_BROAD_PERSISTENT_ECONOMIC"): 7,
        ("15m", "LEVEL_A_BROAD_PERSISTENT_ECONOMIC"): 5,
        ("10m", "LEVEL_B_MULTI_SYMBOL_PERSISTENT_ECONOMIC"): 4,
        ("15m", "LEVEL_B_MULTI_SYMBOL_PERSISTENT_ECONOMIC"): 8,
    }
    if levels != expected:
        raise ValueError(f"frozen level counts changed: {levels}")


def symbol_detail(metrics: pd.DataFrame, final: pd.DataFrame) -> pd.DataFrame:
    keys = set(zip(final.semantic_execution_hash, final.timeframe, strict=True))
    frame = metrics[
        metrics.apply(lambda row: (row.semantic_execution_hash, row.timeframe) in keys, axis=1)
    ].drop_duplicates(["semantic_execution_hash", "symbol", "timeframe"]).copy()
    if len(frame) != 24 * 9:
        raise ValueError(f"expected 216 frozen symbol rows, found {len(frame)}")
    metadata = final.set_index(["semantic_execution_hash", "timeframe"])
    frame["representative_strategy_id"] = [
        metadata.loc[(h, tf), "representative_strategy_id"]
        for h, tf in zip(frame.semantic_execution_hash, frame.timeframe, strict=True)
    ]
    frame["equivalent_source_ids"] = [
        metadata.loc[(h, tf), "equivalent_source_ids"]
        for h, tf in zip(frame.semantic_execution_hash, frame.timeframe, strict=True)
    ]
    frame["candidate_level"] = [
        metadata.loc[(h, tf), "shortlist_level"]
        for h, tf in zip(frame.semantic_execution_hash, frame.timeframe, strict=True)
    ]
    frame["persistent_flag"] = truthy(frame.directionally_persistent)
    frame["Return_BE_positive"] = (frame.Return > 0) & (frame.BE > 0)
    frame["Return_5bp_positive"] = frame.Return_5bp > 0
    frame["long_short_bias"] = frame.long_fraction_v2 - frame.short_fraction_v2
    frame["abs_long_short_bias"] = frame.long_short_bias.abs()
    frame["directional_bias_class"] = np.select(
        [frame.long_short_bias >= 0.50, frame.long_short_bias <= -0.50],
        ["STRONGLY_LONG_BIASED", "STRONGLY_SHORT_BIASED"],
        default="RELATIVELY_BALANCED",
    )
    columns = [
        "representative_strategy_id", "equivalent_source_ids", "semantic_execution_hash",
        "symbol", "timeframe", "candidate_level", "Return", "Return_5bp", "BE", "MDD",
        "turnover_raw", "turnover_percent", "nonflat_fraction_v2", "long_fraction_v2",
        "short_fraction_v2", "flat_fraction_v2", "median_directional_run_hours",
        "P90_directional_run_hours", "sign_switches_per_day", "sign_switch_count_v2",
        "persistent_flag", "Return_BE_positive", "Return_5bp_positive", "long_short_bias",
        "abs_long_short_bias", "directional_bias_class",
    ]
    return frame[columns].sort_values(
        ["representative_strategy_id", "timeframe", "symbol"]
    ).reset_index(drop=True)


def joined_symbols(group: pd.DataFrame, mask: pd.Series) -> str:
    return ";".join(symbol for symbol in SYMBOLS if symbol in set(group.loc[mask, "symbol"]))


def breadth_table(detail: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for (semantic_hash, timeframe), group in detail.groupby(
        ["semantic_execution_hash", "timeframe"], sort=True
    ):
        persistent = truthy(group.persistent_flag)
        economic = truthy(group.Return_BE_positive)
        five = truthy(group.Return_5bp_positive)
        persistent_economic = persistent & economic
        persistent_five = persistent & five
        worst_return_row = group.loc[group.Return.idxmin()]
        worst_be_row = group.loc[group.BE.idxmin()]
        worst_five_row = group.loc[group.Return_5bp.idxmin()]
        row = {
            "representative_strategy_id": group.representative_strategy_id.iloc[0],
            "equivalent_source_ids": group.equivalent_source_ids.iloc[0],
            "semantic_execution_hash": semantic_hash,
            "timeframe": timeframe,
            "candidate_level": group.candidate_level.iloc[0],
            "persistent_symbols": joined_symbols(group, persistent),
            "persistent_symbol_count": int(persistent.sum()),
            "Return_BE_positive_symbols": joined_symbols(group, economic),
            "Return_BE_positive_symbol_count": int(economic.sum()),
            "persistent_Return_BE_positive_symbols": joined_symbols(group, persistent_economic),
            "persistent_Return_BE_positive_symbol_count": int(persistent_economic.sum()),
            "5bp_positive_symbols": joined_symbols(group, five),
            "5bp_positive_symbol_count": int(five.sum()),
            "persistent_5bp_positive_symbols": joined_symbols(group, persistent_five),
            "persistent_5bp_positive_symbol_count": int(persistent_five.sum()),
            "min_Return": float(group.Return.min()),
            "P25_Return": float(group.Return.quantile(0.25)),
            "median_Return": float(group.Return.median()),
            "max_Return": float(group.Return.max()),
            "min_BE": float(group.BE.min()),
            "P25_BE": float(group.BE.quantile(0.25)),
            "median_BE": float(group.BE.median()),
            "max_BE": float(group.BE.max()),
            "min_5bp_Return": float(group.Return_5bp.min()),
            "P25_5bp_Return": float(group.Return_5bp.quantile(0.25)),
            "median_5bp_Return": float(group.Return_5bp.median()),
            "max_5bp_Return": float(group.Return_5bp.max()),
            "worst_symbol": str(worst_return_row.symbol),
            "worst_Return": float(worst_return_row.Return),
            "worst_BE_at_worst_Return_symbol": float(worst_return_row.BE),
            "worst_5bp_Return_at_worst_Return_symbol": float(worst_return_row.Return_5bp),
            "worst_BE_symbol": str(worst_be_row.symbol),
            "worst_BE": float(worst_be_row.BE),
            "worst_5bp_symbol": str(worst_five_row.symbol),
            "worst_5bp_Return": float(worst_five_row.Return_5bp),
            "median_turnover_pct": float(group.turnover_percent.median()),
            "median_nonflat_fraction": float(group.nonflat_fraction_v2.median()),
            "min_median_run_hours": float(group.median_directional_run_hours.min()),
            "median_median_run_hours": float(group.median_directional_run_hours.median()),
            "max_median_run_hours": float(group.median_directional_run_hours.max()),
            "median_P90_run_hours": float(group.P90_directional_run_hours.median()),
            "median_switches_per_day": float(group.sign_switches_per_day.median()),
            "max_switches_per_day": float(group.sign_switches_per_day.max()),
            "strongly_long_biased_symbol_count": int((group.directional_bias_class == "STRONGLY_LONG_BIASED").sum()),
            "strongly_short_biased_symbol_count": int((group.directional_bias_class == "STRONGLY_SHORT_BIASED").sum()),
            "relatively_balanced_symbol_count": int((group.directional_bias_class == "RELATIVELY_BALANCED").sum()),
        }
        row["BOSS_STRONG_CASE"] = bool(
            row["persistent_symbol_count"] >= 5
            and row["persistent_Return_BE_positive_symbol_count"] >= 5
            and row["persistent_5bp_positive_symbol_count"] >= 3
        )
        row["BOSS_STRONG_5BP_BREADTH"] = bool(
            row["persistent_5bp_positive_symbol_count"] >= 5
        )
        rows.append(row)
    result = pd.DataFrame(rows)
    result["__strong5"] = ~truthy(result.BOSS_STRONG_5BP_BREADTH)
    result["__strong"] = ~truthy(result.BOSS_STRONG_CASE)
    result["__level"] = result.candidate_level.map(
        {"LEVEL_A_BROAD_PERSISTENT_ECONOMIC": 0,
         "LEVEL_B_MULTI_SYMBOL_PERSISTENT_ECONOMIC": 1}
    )
    result = result.sort_values(
        ["__strong5", "__strong", "__level", "persistent_5bp_positive_symbol_count",
         "persistent_Return_BE_positive_symbol_count", "persistent_symbol_count",
         "median_BE", "median_turnover_pct", "representative_strategy_id", "timeframe"],
        ascending=[True, True, True, False, False, False, False, True, True, True],
    ).drop(columns=["__strong5", "__strong", "__level"])
    result.insert(0, "boss_review_order", np.arange(1, len(result) + 1))
    result["sort_contract"] = (
        "strong persistent+5bp breadth; strong case; Level A/B; persistent+5bp breadth; "
        "persistent+Return&BE breadth; persistent breadth; signed BE; turnover; no Return-first score"
    )
    return result.reset_index(drop=True)


def relative_close(a: float, b: float, tolerance: float = 0.10) -> bool:
    scale = max(abs(a), abs(b), 1e-9)
    return abs(a - b) <= tolerance * scale


def timeframe_preference(ten: pd.Series | None, fifteen: pd.Series | None) -> tuple[str, str]:
    if ten is None:
        return "15M_ONLY", "Only 15m is in the frozen candidate set"
    if fifteen is None:
        return "10M_ONLY", "Only 10m is in the frozen candidate set"
    breadth_fields = [
        "persistent_symbol_count", "persistent_Return_BE_positive_symbol_count",
        "persistent_5bp_positive_symbol_count", "median_BE",
    ]
    ops_ten = [
        ten.median_turnover_pct <= fifteen.median_turnover_pct,
        ten.median_median_run_hours >= fifteen.median_median_run_hours,
        ten.median_switches_per_day <= fifteen.median_switches_per_day,
    ]
    ops_fifteen = [
        fifteen.median_turnover_pct <= ten.median_turnover_pct,
        fifteen.median_median_run_hours >= ten.median_median_run_hours,
        fifteen.median_switches_per_day <= ten.median_switches_per_day,
    ]
    ten_no_worse = all(float(ten[field]) >= float(fifteen[field]) for field in breadth_fields)
    fifteen_no_worse = all(float(fifteen[field]) >= float(ten[field]) for field in breadth_fields)
    if (
        all(float(ten[field]) == float(fifteen[field]) for field in breadth_fields[:3])
        and relative_close(ten.median_BE, fifteen.median_BE)
        and relative_close(ten.median_turnover_pct, fifteen.median_turnover_pct)
        and relative_close(ten.median_median_run_hours, fifteen.median_median_run_hours)
        and relative_close(ten.median_switches_per_day, fifteen.median_switches_per_day)
    ):
        status = "BOTH_SIMILAR"
    elif ten_no_worse and sum(ops_ten) >= 2 and (
        any(float(ten[field]) > float(fifteen[field]) for field in breadth_fields) or sum(ops_ten) == 3
    ):
        status = "10M_STRUCTURALLY_BETTER"
    elif fifteen_no_worse and sum(ops_fifteen) >= 2 and (
        any(float(fifteen[field]) > float(ten[field]) for field in breadth_fields) or sum(ops_fifteen) == 3
    ):
        status = "15M_STRUCTURALLY_BETTER"
    else:
        status = "MIXED_TRADEOFF"
    reason = (
        f"10m P/E/5bp={int(ten.persistent_symbol_count)}/"
        f"{int(ten.persistent_Return_BE_positive_symbol_count)}/"
        f"{int(ten.persistent_5bp_positive_symbol_count)}, BE={ten.median_BE:.3f}, "
        f"turnover={ten.median_turnover_pct:.2f}%, run={ten.median_median_run_hours:.2f}h, "
        f"switches/day={ten.median_switches_per_day:.4f}; 15m P/E/5bp="
        f"{int(fifteen.persistent_symbol_count)}/"
        f"{int(fifteen.persistent_Return_BE_positive_symbol_count)}/"
        f"{int(fifteen.persistent_5bp_positive_symbol_count)}, BE={fifteen.median_BE:.3f}, "
        f"turnover={fifteen.median_turnover_pct:.2f}%, run={fifteen.median_median_run_hours:.2f}h, "
        f"switches/day={fifteen.median_switches_per_day:.4f}"
    )
    return status, reason


def strategy_groups(final: pd.DataFrame, breadth: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for semantic_hash, group in final.groupby("semantic_execution_hash", sort=True):
        bgroup = breadth[breadth.semantic_execution_hash.eq(semantic_hash)]
        ten_rows = bgroup[bgroup.timeframe.eq("10m")]
        fifteen_rows = bgroup[bgroup.timeframe.eq("15m")]
        ten = None if ten_rows.empty else ten_rows.iloc[0]
        fifteen = None if fifteen_rows.empty else fifteen_rows.iloc[0]
        status, reason = timeframe_preference(ten, fifteen)
        levels = group.set_index("timeframe").shortlist_level.to_dict()
        rows.append(
            {
                "semantic_group_id": semantic_hash,
                "representative_strategy_id": group.representative_strategy_id.iloc[0],
                "equivalent_source_ids": group.equivalent_source_ids.iloc[0],
                "10m_candidate": "10m" in levels,
                "15m_candidate": "15m" in levels,
                "10m_shortlist_level": levels.get("10m", ""),
                "15m_shortlist_level": levels.get("15m", ""),
                "descriptive_timeframe_status": status,
                "transparent_reason": reason,
                "comparison_contract": (
                    "breadth+signed BE plus turnover/run/switching dominance; mixed evidence retained; "
                    "Return is not the first criterion"
                ),
            }
        )
    return pd.DataFrame(rows).sort_values("representative_strategy_id").reset_index(drop=True)


def decode_rule(config: dict[str, Any]) -> tuple[str, dict[str, Any] | None]:
    params = config["params"]
    encoded = params.get("rule_spec_b64")
    if encoded:
        return str(params.get("family", "")), json.loads(base64.urlsafe_b64decode(encoded).decode())
    return str(params.get("family", "")), None


def walk_nodes(value: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    if isinstance(value, dict):
        result.append(value)
        for child in value.values():
            result.extend(walk_nodes(child))
    elif isinstance(value, list):
        for child in value:
            result.extend(walk_nodes(child))
    return result


def position_mechanism(groups: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for item in groups.itertuples(index=False):
        config_path = PROJECT_ROOT / "strategies" / item.representative_strategy_id / "config.yaml"
        strategy_path = PROJECT_ROOT / "strategies" / item.representative_strategy_id / "strategy.py"
        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        family, rule = decode_rule(config)
        params = config["params"]
        if rule is not None:
            family_name = str(rule.get("family", family))
            actions = rule.get("actions", [])
            action_names = [str(action["action"]) for action in actions]
            nodes = walk_nodes(actions)
            ops = sorted({str(node.get("op")) for node in nodes if node.get("op")})
            entry_nodes = [action for action in actions if str(action["action"]).startswith("ENTER_")]
            entry_requires_flat = any(
                any(node.get("op") == "position_is" and node.get("side") == "flat" for node in walk_nodes(action))
                for action in entry_nodes
            )
            exit_names = sorted({name for name in action_names if name.startswith("EXIT") or name == "FLATTEN"})
            flat_explicit = bool(exit_names)
            reverse_on_opposite = bool(entry_nodes and not entry_requires_flat)
            confirmation = ";".join(op for op in ("consecutive", "turn_up", "turn_down", "pulse", "regular_divergence") if op in ops)
            if not confirmation and len(entry_nodes) and any(node.get("op") == "and" for node in nodes):
                confirmation = "multi-condition confluence"
            neutral = "threshold/range state" if any(op in ops for op in ("gt", "gte", "lt", "lte")) else "none explicit"
            position_mapping = (
                "ENTER_LONG→+fraction; ENTER_SHORT→-fraction; EXIT→0; REDUCE→scale current; "
                "no action→retain prior target"
            )
            exit_type = ";".join(exit_names) + (";partial reduction" if any("REDUCE" in name for name in action_names) else "")
        else:
            family_name = family
            flat_explicit = True
            if family == "session_vwap_volume_mean":
                position_mapping = (
                    "flat-only entry after session VWAP/volume confirmation; ±1 target; one half reduction; "
                    "UTC-session flatten; otherwise retain target"
                )
                exit_type = "UTC session flatten; session boundary flat; partial reduction on volume weakening"
                reverse_on_opposite = False
                confirmation = f"{params.get('consecutive_bars', 2)} completed bars vs session VWAP"
                neutral = "flat outside entry/session contract"
            elif family == "rsi_turn_candle":
                position_mapping = (
                    "RSI turn+candle sets +1/-1; opposite signal can reverse; extremes exit to 0; "
                    "neutral crossing halves current exposure; otherwise retain target"
                )
                exit_type = "RSI upper/lower extreme exit; neutral-threshold partial reduction"
                reverse_on_opposite = True
                confirmation = "RSI slope sign change plus candle direction"
                neutral = f"RSI {params.get('lower_threshold')} / {params.get('neutral_threshold')} / {params.get('upper_threshold')} hysteresis"
            else:
                position_mapping = "custom family target mapping; no action retains prior target"
                exit_type = "custom family exit"
                reverse_on_opposite = False
                confirmation = "custom family contract"
                neutral = "see config"
        rows.append(
            {
                "strategy_id": item.representative_strategy_id,
                "semantic_group_id": item.semantic_group_id,
                "equivalent_source_ids": item.equivalent_source_ids,
                "signal_type": family_name,
                "position_mapping": position_mapping,
                "exit_type": exit_type,
                "flat_state_explicit": bool(flat_explicit),
                "reverse_on_opposite": bool(reverse_on_opposite),
                "state_persistence": "YES — no action preserves the prior target position",
                "neutral_zone": neutral,
                "confirmation_rule": confirmation or "none beyond explicit crossover/state rule",
                "main_persistence_driver": PERSISTENCE_DRIVER.get(family_name, PERSISTENCE_DRIVER.get(family, "OTHER")),
                "contracts_applied": params.get("contracts_applied", ""),
                "config_path": str(config_path.relative_to(PROJECT_ROOT)).replace(os.sep, "/"),
                "strategy_path": str(strategy_path.relative_to(PROJECT_ROOT)).replace(os.sep, "/"),
                "config_sha256": sha256_file(config_path),
                "mechanism_source": "config + typed rule_spec/StrategyIR + WorkbookParametricStrategy target mapping",
            }
        )
    return pd.DataFrame(rows)


def timeframe_comparison(metrics: pd.DataFrame, groups: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for group in groups.itertuples(index=False):
        physical = metrics[metrics.semantic_execution_hash.eq(group.semantic_group_id)].drop_duplicates(
            ["semantic_execution_hash", "symbol", "timeframe"]
        )
        for timeframe in TIMEFRAMES:
            frame = physical[physical.timeframe.eq(timeframe)]
            persistent = truthy(frame.directionally_persistent)
            economic = (frame.Return > 0) & (frame.BE > 0)
            five = frame.Return_5bp > 0
            rows.append(
                {
                    "semantic_group_id": group.semantic_group_id,
                    "representative_strategy_id": group.representative_strategy_id,
                    "equivalent_source_ids": group.equivalent_source_ids,
                    "timeframe": timeframe,
                    "persistent_symbol_count": int(persistent.sum()),
                    "persistent_Return_BE_positive_count": int((persistent & economic).sum()),
                    "persistent_5bp_positive_count": int((persistent & five).sum()),
                    "Return_BE_positive_symbol_count": int(economic.sum()),
                    "5bp_positive_symbol_count": int(five.sum()),
                    "median_Return": float(frame.Return.median()),
                    "median_BE": float(frame.BE.median()),
                    "median_turnover_pct": float(frame.turnover_percent.median()),
                    "median_directional_run_hours": float(frame.median_directional_run_hours.median()),
                    "median_switches_per_day": float(frame.sign_switches_per_day.median()),
                    "denominator": "9 symbols",
                }
            )
    return pd.DataFrame(rows)


def timeframe_aggregate(comparison: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for timeframe in TIMEFRAMES:
        frame = comparison[comparison.timeframe.eq(timeframe)]
        rows.append(
            {
                "timeframe": timeframe,
                "independent_group_count": int(frame.semantic_group_id.nunique()),
                "median_group_turnover_pct": float(frame.median_turnover_pct.median()),
                "median_group_directional_run_hours": float(frame.median_directional_run_hours.median()),
                "median_group_switches_per_day": float(frame.median_switches_per_day.median()),
                "total_persistent_symbol_count": int(frame.persistent_symbol_count.sum()),
                "median_persistent_symbols_per_group": float(frame.persistent_symbol_count.median()),
                "total_persistent_Return_BE_positive_count": int(frame.persistent_Return_BE_positive_count.sum()),
                "median_persistent_Return_BE_positive_per_group": float(frame.persistent_Return_BE_positive_count.median()),
                "total_persistent_5bp_positive_count": int(frame.persistent_5bp_positive_count.sum()),
                "population_contract": "same frozen 14 independent semantic groups × 9 symbols",
            }
        )
    return pd.DataFrame(rows)


def parameter_case(
    sensitivity: pd.DataFrame, final: pd.DataFrame, parameter_examples: pd.DataFrame,
) -> tuple[pd.DataFrame, str, str]:
    hashes = sorted(parameter_examples.semantic_execution_hash.unique())
    if len(hashes) != 1:
        raise ValueError(f"expected one final persistence-improvable group, found {len(hashes)}")
    semantic_hash = hashes[0]
    metadata = final[final.semantic_execution_hash.eq(semantic_hash)].iloc[0]
    frame = sensitivity[sensitivity.semantic_execution_hash.eq(semantic_hash)].drop_duplicates(
        ["semantic_execution_hash", "parameter", "tested_value", "symbol", "timeframe"]
    ).copy()
    frame["representative_strategy_id"] = metadata.representative_strategy_id
    frame["equivalent_source_ids"] = metadata.equivalent_source_ids
    frame["lower_tested_value"] = frame.groupby("parameter").tested_value.transform("min")
    frame["higher_tested_value"] = frame.groupby("parameter").tested_value.transform("max")
    frame["parameter_mechanism"] = frame.parameter.map(
        {
            "lower_threshold": "oversold long-entry prerequisite and short-position exit threshold",
            "upper_threshold": "overbought short-entry prerequisite and long-position exit threshold",
            "neutral_threshold": "partial-reduction crossing threshold for existing positions",
        }
    ).fillna("existing source parameter affects entry/exit state transitions")
    frame["useful_existing_parameter_example"] = (
        frame.parameter.eq("lower_threshold")
        & frame.tested_value.eq(15.0)
        & frame.timeframe.eq("15m")
    )
    frame["PERSISTENCE_PARAMETER_CONCLUSION"] = (
        "EXISTING_PARAMETER_CAN_MATERIALLY_CHANGE_PERSISTENCE_FOR_ONE_GROUP; "
        "MOST_FINAL_CANDIDATES_PERSIST_DUE_TO_STRATEGY_STRUCTURE_NOT_SIMPLE_PARAMETER_TUNING"
    )
    columns = [
        "representative_strategy_id", "equivalent_source_ids", "semantic_execution_hash",
        "parameter", "canonical_value", "lower_tested_value", "higher_tested_value",
        "tested_value", "value_relation", "symbol", "timeframe", "nonflat_fraction",
        "median_directional_run_hours", "P90_directional_run_hours", "sign_switches_per_day",
        "turnover_percent", "Return", "BE", "Return_5bp", "delta_nonflat_fraction",
        "delta_median_directional_run_hours", "delta_sign_switches_per_day", "delta_turnover_raw",
        "parameter_mechanism", "useful_existing_parameter_example",
        "PERSISTENCE_PARAMETER_CONCLUSION", "one_parameter_only",
    ]
    return (
        frame[columns].sort_values(["parameter", "tested_value", "timeframe", "symbol"]),
        str(metadata.representative_strategy_id),
        "lower_threshold 20→15 (15m; longer runs and fewer switches on BTC/ETH/SOL while 5bp Return remains positive)",
    )


def hold_review(
    hold_shortlist: pd.DataFrame, metrics: pd.DataFrame, final_summary: pd.DataFrame,
) -> pd.DataFrame:
    id_to_hash = metrics[["strategy_id", "semantic_execution_hash", "representative_strategy_id"]].drop_duplicates()
    frame = hold_shortlist.merge(id_to_hash, on="strategy_id", how="left", validate="one_to_one")
    if frame.semantic_execution_hash.isna().any():
        raise ValueError("hold review strategy missing semantic hash")
    rows = []
    for semantic_hash, group in frame.groupby("semantic_execution_hash", sort=True):
        ids = sorted(group.strategy_id.unique())
        economic = final_summary[final_summary.semantic_execution_hash.eq(semantic_hash)]
        current_evidence = (
            "not in final 14 groups"
            if economic.empty
            else "; ".join(
                f"{row.timeframe}: persistent={int(row.persistent_symbol_count)}, "
                f"persistent+ReturnBE={int(row.persistent_and_Return_BE_positive_symbols)}"
                for row in economic.itertuples(index=False)
            )
        )
        rows.append(
            {
                "semantic_group": semantic_hash,
                "representative_strategy": sorted(group.representative_strategy_id.unique())[0],
                "source_ids": ";".join(ids),
                "current_flat_reason": ";".join(sorted(set(group.canonical_flat_reason.astype(str)))),
                "directional_state_available": bool(truthy(group.directional_state_available).all()),
                "semantic_change_required": "YES",
                "expected_structural_effect": (
                    "eliminate canonical flat waiting state; increase nonflat fraction and directional-run duration; "
                    "may also change turnover and economics"
                ),
                "current_economic_evidence": current_evidence,
                "experiment_status": "NOT_RUN — separate strategy hypothesis requiring authorization",
            }
        )
    return pd.DataFrame(rows)


def heatmap_figure(group: pd.DataFrame, output: Path) -> None:
    group = group.set_index("symbol").reindex(SYMBOLS)
    matrices = [
        ("Return (1x, %)", group[["Return"]].T.to_numpy() * 100, "RdYlGn", True, ".1f"),
        ("Signed BE (bps)", group[["BE"]].T.to_numpy(), "RdYlGn", True, ".1f"),
        ("5bp Return (%)", group[["Return_5bp"]].T.to_numpy() * 100, "RdYlGn", True, ".1f"),
        ("Persistent", truthy(group.persistent_flag).astype(int).to_numpy()[None, :], "Greens", False, "d"),
    ]
    fig, axes = plt.subplots(4, 1, figsize=(14, 6.8), constrained_layout=True)
    for axis, (label, values, cmap, centered, fmt) in zip(axes, matrices, strict=True):
        if centered:
            low, high = float(np.nanmin(values)), float(np.nanmax(values))
            if low < 0 < high:
                image = axis.imshow(values, cmap=cmap, aspect="auto", norm=TwoSlopeNorm(vcenter=0, vmin=low, vmax=high))
            else:
                image = axis.imshow(values, cmap=cmap, aspect="auto")
        else:
            image = axis.imshow(values, cmap=cmap, aspect="auto", vmin=0, vmax=1)
        axis.set_yticks([0], [label])
        axis.set_xticks(range(len(SYMBOLS)), SYMBOLS, rotation=30, ha="right")
        for index, value in enumerate(values[0]):
            text = f"{int(value)}" if fmt == "d" else format(float(value), fmt)
            axis.text(index, 0, text, ha="center", va="center", fontsize=8, color="black")
        fig.colorbar(image, ax=axis, fraction=0.018, pad=0.01)
    first = group.iloc[0]
    fig.suptitle(
        f"{first.representative_strategy_id} | {first.timeframe} | 9-symbol frozen review\n"
        "bar signal → raw tick execution | persistent flag uses executed-position v2",
        fontsize=12,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=220)
    plt.close(fig)


def render_figures(
    root: Path, output: Path, detail: pd.DataFrame, breadth: pd.DataFrame,
) -> tuple[dict[tuple[str, str], str], int]:
    summary_dir = output / "figures" / "nine_symbol_summary"
    regime_dir = output / "figures" / "strong_case_regimes"
    summary_paths: dict[tuple[str, str], str] = {}
    for (semantic_hash, timeframe), group in detail.groupby(
        ["semantic_execution_hash", "timeframe"], sort=True
    ):
        representative = group.representative_strategy_id.iloc[0]
        target = summary_dir / f"{representative}_{timeframe}_nine_symbol.png"
        heatmap_figure(group, target)
        summary_paths[(semantic_hash, timeframe)] = str(target.relative_to(output)).replace(os.sep, "/")
    regime_count = 0
    strong = breadth[truthy(breadth.BOSS_STRONG_CASE) | truthy(breadth.BOSS_STRONG_5BP_BREADTH)]
    for item in strong.itertuples(index=False):
        group = detail[
            detail.semantic_execution_hash.eq(item.semantic_execution_hash)
            & detail.timeframe.eq(item.timeframe)
        ].sort_values("Return")
        indices = sorted(set([0, len(group) // 2, len(group) - 1]))
        for index in indices:
            row = group.iloc[index]
            source = case_review(root, row)
            if not source.is_file():
                raise FileNotFoundError(source)
            target = regime_dir / f"{row.representative_strategy_id}_{row.symbol}_{row.timeframe}.png"
            summary = pd.Series(
                {
                    "strategy_id": row.representative_strategy_id,
                    "symbol": row.symbol,
                    "timeframe": row.timeframe,
                    "persistence_class_v2": f"BOSS_FINAL_FROZEN_REVIEW | MDD={row.MDD:.2%}",
                    "Return": row.Return, "Return_5bp": row.Return_5bp, "BE": row.BE,
                    "turnover_percent": row.turnover_percent,
                    "long_fraction_v2": row.long_fraction_v2,
                    "short_fraction_v2": row.short_fraction_v2,
                    "flat_fraction_v2": row.flat_fraction_v2,
                    "nonflat_fraction_v2": row.nonflat_fraction_v2,
                    "median_directional_run_hours": row.median_directional_run_hours,
                    "P90_directional_run_hours": row.P90_directional_run_hours,
                    "sign_switch_count_v2": row.sign_switch_count_v2,
                    "sign_switches_per_day": row.sign_switches_per_day,
                }
            )
            regime_figure(summary, pd.read_parquet(source), target)
            regime_count += 1
    return summary_paths, regime_count


def top_candidates(
    breadth: pd.DataFrame, mechanism: pd.DataFrame, final: pd.DataFrame,
    summary_paths: dict[tuple[str, str], str], hold_shortlist: pd.DataFrame,
) -> pd.DataFrame:
    strong = breadth[truthy(breadth.BOSS_STRONG_CASE) | truthy(breadth.BOSS_STRONG_5BP_BREADTH)].copy()
    mechanism_index = mechanism.set_index("semantic_group_id")
    parameter_hashes = set(
        final.loc[final.parameter_sensitivity_conclusion.ne("NOT_TESTED"), "semantic_execution_hash"]
    )
    hold_ids = set(hold_shortlist.strategy_id)
    rows = []
    for rank, item in enumerate(strong.itertuples(index=False), start=1):
        mech = mechanism_index.loc[item.semantic_execution_hash]
        rows.append(
            {
                "rank_order": rank,
                "representative_strategy": item.representative_strategy_id,
                "equivalent_IDs": item.equivalent_source_ids,
                "timeframe": item.timeframe,
                "candidate_class": (
                    "BOSS_STRONG_5BP_BREADTH" if item.BOSS_STRONG_5BP_BREADTH else "BOSS_STRONG_CASE"
                ),
                "persistent_symbols": item.persistent_symbol_count,
                "persistent_ReturnBE_symbols": item.persistent_Return_BE_positive_symbol_count,
                "persistent_5bp_symbols": item.persistent_5bp_positive_symbol_count,
                "median_Return": item.median_Return,
                "median_BE": item.median_BE,
                "median_5bp_Return": item.median_5bp_Return,
                "worst_symbol_Return": item.worst_Return,
                "worst_symbol_BE": item.worst_BE,
                "median_turnover_pct": item.median_turnover_pct,
                "median_run_hours": item.median_median_run_hours,
                "switches_per_day": item.median_switches_per_day,
                "persistence_mechanism": mech.main_persistence_driver,
                "persistence_parameter_tunable": item.semantic_execution_hash in parameter_hashes,
                "hold_until_opposite_required": any(
                    source_id in hold_ids for source_id in item.equivalent_source_ids.split(";")
                ),
                "figure_directory": summary_paths.get((item.semantic_execution_hash, item.timeframe), ""),
            }
        )
    return pd.DataFrame(rows)


def key_answers(
    groups: pd.DataFrame, breadth: pd.DataFrame, aggregate: pd.DataFrame,
    parameter_group: str, parameter_example: str, hold_groups: pd.DataFrame,
) -> pd.DataFrame:
    one = aggregate.set_index("timeframe")
    rows = [
        ("How many independent strategy groups remain?", 14, "independent semantic groups"),
        ("How many final 10m candidate rows?", int((breadth.timeframe == "10m").sum()), "semantic-group×timeframe rows"),
        ("How many final 15m candidate rows?", int((breadth.timeframe == "15m").sum()), "semantic-group×timeframe rows"),
        ("How many are persistent on >=5 of 9 symbols?", int((breadth.persistent_symbol_count >= 5).sum()), "semantic-group×timeframe rows"),
        ("How many are persistent+Return/BE positive on >=5 symbols?", int((breadth.persistent_Return_BE_positive_symbol_count >= 5).sum()), "semantic-group×timeframe rows"),
        ("How many are persistent+5bp positive on >=5 symbols?", int((breadth.persistent_5bp_positive_symbol_count >= 5).sum()), "semantic-group×timeframe rows"),
        ("Does 10m reduce turnover vs 1m for the final group set?", bool(one.loc["10m", "median_group_turnover_pct"] < one.loc["1m", "median_group_turnover_pct"]), "median across same 14 independent groups"),
        ("Does 15m reduce turnover vs 1m?", bool(one.loc["15m", "median_group_turnover_pct"] < one.loc["1m", "median_group_turnover_pct"]), "median across same 14 independent groups"),
        ("Does 10m increase directional holding duration?", bool(one.loc["10m", "median_group_directional_run_hours"] > one.loc["1m", "median_group_directional_run_hours"]), "median across same 14 independent groups"),
        ("Does 15m increase directional holding duration?", bool(one.loc["15m", "median_group_directional_run_hours"] > one.loc["1m", "median_group_directional_run_hours"]), "median across same 14 independent groups"),
        ("What is the main structural reason these strategies stay on one side?", "hysteresis/slow cross/long-lookback/confluence rules preserve target until explicit exit", "source/config/typed-rule audit"),
        ("How many final independent groups can materially increase persistence using existing parameters only?", 1, "independent semantic groups"),
        ("Which parameter / strategy is the one useful example?", f"{parameter_group} / {parameter_example}", "completed bounded sensitivity only"),
        ("How many would require HOLD_UNTIL_OPPOSITE semantic changes?", int(len(hold_groups)), "independent semantic groups in 30-ID feasibility shortlist"),
    ]
    return pd.DataFrame(rows, columns=["question", "result", "denominator_or_basis"])


def build(root: Path, output: Path, render: bool = True) -> dict[str, Any]:
    protected_before = config_snapshot()
    paths = {
        "final": root / DISTILLATION / "boss_10m15m_final_shortlist.csv",
        "independent": root / DISTILLATION / "boss_10m15m_independent_candidates.csv",
        "top": root / DISTILLATION / "boss_10m15m_top_candidates.csv",
        "master": root / "boss_multitimeframe_tick_master.csv",
        "metrics": root / "persistent_position_metrics_v2.csv",
        "sensitivity": root / FOLLOWUP / "persistence_parameter_sensitivity_v2.csv",
        "parameter_examples": root / DISTILLATION / "boss_parameter_persistence_examples.csv",
        "hold_shortlist": root / DISTILLATION / "boss_hold_until_opposite_candidates.csv",
    }
    for path in paths.values():
        if not path.is_file():
            raise FileNotFoundError(path)
    hashes_before = source_hashes(paths)
    final = pd.read_csv(paths["final"])
    independent = pd.read_csv(paths["independent"])
    top_frozen = pd.read_csv(paths["top"])
    master = pd.read_csv(paths["master"])
    metrics = pd.read_csv(paths["metrics"])
    sensitivity = pd.read_csv(paths["sensitivity"])
    parameter_examples = pd.read_csv(paths["parameter_examples"])
    hold_shortlist = pd.read_csv(paths["hold_shortlist"])
    validate_frozen_membership(final)
    if len(master) != 9_612 or len(metrics) != 9_612:
        raise ValueError("authoritative matrix/v2 row count changed")
    if set(top_frozen.semantic_execution_hash) - set(final.semantic_execution_hash):
        raise ValueError("frozen top list is not a subset of final shortlist")

    detail = symbol_detail(metrics, final)
    breadth = breadth_table(detail)
    frozen_check = breadth.merge(
        final[
            [
                "semantic_execution_hash", "timeframe", "persistent_symbol_count",
                "persistent_and_Return_BE_positive_symbols",
                "persistent_and_5bp_positive_symbols", "all_Return_BE_positive_symbols",
                "all_5bp_positive_symbols",
            ]
        ],
        on=["semantic_execution_hash", "timeframe"],
        how="left",
        validate="one_to_one",
        suffixes=("", "_frozen"),
    )
    reconciliation_pairs = (
        ("persistent_symbol_count", "persistent_symbol_count_frozen"),
        ("persistent_Return_BE_positive_symbol_count", "persistent_and_Return_BE_positive_symbols"),
        ("persistent_5bp_positive_symbol_count", "persistent_and_5bp_positive_symbols"),
        ("Return_BE_positive_symbol_count", "all_Return_BE_positive_symbols"),
        ("5bp_positive_symbol_count", "all_5bp_positive_symbols"),
    )
    for current, frozen in reconciliation_pairs:
        if not frozen_check[current].equals(frozen_check[frozen]):
            raise ValueError(f"frozen shortlist breadth mismatch: {current} != {frozen}")
    groups = strategy_groups(final, breadth)
    mechanism = position_mechanism(groups)
    comparison = timeframe_comparison(metrics, groups)
    aggregate = timeframe_aggregate(comparison)
    param_case, parameter_group, parameter_example = parameter_case(
        sensitivity, final, parameter_examples
    )
    hold_groups = hold_review(hold_shortlist, metrics, final)

    output.mkdir(parents=True, exist_ok=True)
    summary_paths: dict[tuple[str, str], str] = {}
    regime_count = 0
    if render:
        summary_paths, regime_count = render_figures(root, output, detail, breadth)
    top = top_candidates(breadth, mechanism, final, summary_paths, hold_shortlist)
    answers = key_answers(groups, breadth, aggregate, parameter_group, parameter_example, hold_groups)

    outputs = {
        "boss_final_top_candidates.csv": top,
        "boss_final_14_strategy_groups.csv": groups,
        "boss_final_14_timeframe_comparison.csv": comparison,
        "boss_final_timeframe_aggregate.csv": aggregate,
        "boss_final_candidate_symbol_detail.csv": detail,
        "boss_final_candidate_breadth.csv": breadth,
        "boss_final_position_mechanism.csv": mechanism,
        "boss_final_parameter_case.csv": param_case,
        "boss_final_hold_until_opposite_review.csv": hold_groups,
        "boss_final_key_answers.csv": answers,
    }
    for name, frame in outputs.items():
        atomic_csv(output / name, frame)

    hashes_after = source_hashes(paths)
    protected_after = config_snapshot()
    if hashes_before != hashes_after:
        raise ValueError("authoritative research artifacts changed during final review")
    if protected_before != protected_after:
        raise ValueError("strategy/config sources changed during final review")
    counts = {
        "independent_groups": int(groups.semantic_group_id.nunique()),
        "candidate_strategy_timeframe_rows": int(len(breadth)),
        "final_strong_shortlist_rows": int(len(top)),
        "10m_strong": int((top.timeframe == "10m").sum()) if not top.empty else 0,
        "15m_strong": int((top.timeframe == "15m").sum()) if not top.empty else 0,
        "persistent_ge5": int((breadth.persistent_symbol_count >= 5).sum()),
        "persistent_Return_BE_ge5": int((breadth.persistent_Return_BE_positive_symbol_count >= 5).sum()),
        "persistent_5bp_ge5": int((breadth.persistent_5bp_positive_symbol_count >= 5).sum()),
        "boss_strong_case": int(truthy(breadth.BOSS_STRONG_CASE).sum()),
        "boss_strong_5bp_breadth": int(truthy(breadth.BOSS_STRONG_5BP_BREADTH).sum()),
        "all_5bp_positive_ge5_without_persistence_intersection": int(
            (breadth["5bp_positive_symbol_count"] >= 5).sum()
        ),
        "existing_parameter_persistence_groups": 1,
        "hold_until_opposite_review_independent_groups": int(len(hold_groups)),
        "nine_symbol_summary_figures": len(summary_paths),
        "strong_case_regime_figures": regime_count,
    }
    validation = {
        "status": "PASSED",
        "counts": counts,
        "frozen_membership_preserved": True,
        "frozen_breadth_metrics_reconciled": True,
        "semantic_equivalence_preperformance": True,
        "symbol_detail_rows": len(detail),
        "expected_symbol_detail_rows": 216,
        "master_trace_rows": 9_612,
        "persistence_v2_trace_rows": 9_612,
        "parameter_source_rows": len(sensitivity),
        "protected_source_hashes_unchanged": hashes_before == hashes_after,
        "canonical_config_modifications": 0,
        "canonical_result_modifications": 0,
        "main_matrix_backtests_rerun": 0,
        "tick_index_rebuild": 0,
        "new_parameter_sensitivity_values": 0,
        "hold_until_opposite_runs": 0,
        "new_symbols": 0,
        "new_timeframes": 0,
        "source_hashes": hashes_before,
        "outputs": sorted(outputs),
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
