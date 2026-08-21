#!/usr/bin/env python3
"""Compile reviewed Phase 2.2B rows into normal strategy-package definitions.

This compiler is deliberately conservative: a row is emitted only when its
entire entry/exit structure matches a reviewed implementation family.  It reads
the workbook audit artifacts, never the workbook at strategy runtime.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]
AUDIT = ROOT / "outputs/internal_audit/strategy_workbook"
DEFAULT_OUTPUT = ROOT / "configs/semantic_contracts/workbook_phase2_2b_strategies.json"
AMBIGUOUS = {"AMBIGUOUS_ENTRY_EXIT_LOGIC", "AMBIGUOUS_NUMERIC_SEMANTICS"}
PHASE2_1_IMPLEMENTED = {
    "xlsx_s1_0002", "xlsx_s1_0003", "xlsx_s1_0004", "xlsx_s1_0005",
    "xlsx_s1_0006", "xlsx_s1_0007", "xlsx_s1_0010", "xlsx_s1_0012",
    "xlsx_s1_0016", "xlsx_s1_0017", "xlsx_s1_0019", "xlsx_s1_0020",
    "xlsx_s1_0024", "xlsx_s1_0025", "xlsx_s1_0026", "xlsx_s1_0027",
    "xlsx_s1_0029", "xlsx_s1_0033", "xlsx_s1_0034", "xlsx_s1_0038",
    "xlsx_s2_0017", "xlsx_s2_0042", "xlsx_s2_0230", "xlsx_s2_0277",
    "xlsx_s2_0316", "xlsx_s2_0363", "xlsx_s2_0432", "xlsx_s2_0513",
    "xlsx_s2_0560", "xlsx_s2_0665", "xlsx_s2_0708", "xlsx_s2_0737",
    "xlsx_s2_0842", "xlsx_s2_0879",
}


def rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def number_before_atr(text: str, keyword: str) -> float | None:
    pattern = rf"(\d+(?:\.\d+)?)\s*ATR[^；;]*{keyword}"
    match = re.search(pattern, text, re.I)
    if match:
        return float(match.group(1))
    # Wording sometimes places the action first: 止损1ATR.
    match = re.search(rf"{keyword}[^；;]*?(\d+(?:\.\d+)?)\s*ATR", text, re.I)
    return float(match.group(1)) if match else None


def ma_cross_slope(row: dict[str, str]) -> dict[str, object] | None:
    long_rule = row["source_long_condition"]
    short_rule = row["source_short_condition"]
    exit_rule = row["source_exit_condition"]
    long_match = re.search(r"(EMA|MA)\s*(\d+)\s*上穿\s*(?:EMA|MA)\s*(\d+)", long_rule, re.I)
    short_match = re.search(r"(EMA|MA)\s*(\d+)\s*下穿\s*(?:EMA|MA)\s*(\d+)", short_rule, re.I)
    if not long_match or not short_match:
        return None
    average_type = "ema" if long_match.group(1).upper() == "EMA" else "sma"
    fast, slow = int(long_match.group(2)), int(long_match.group(3))
    short_average_type = "ema" if short_match.group(1).upper() == "EMA" else "sma"
    if (short_average_type, int(short_match.group(2)), int(short_match.group(3))) != (
        average_type, fast, slow,
    ):
        return None
    if not re.search(r"(?:双线|两条均线|均线).*?(?:同步|同时)?向上|斜率.*?向上", long_rule):
        return None
    if not re.search(r"(?:双线|两条均线|均线).*?(?:同步|同时)?向下|斜率.*?向下", short_rule):
        return None
    # Additional entry filters need another reviewed family.
    if re.search(r"ADX|RSI|CCI|MACD|DIF|DEA|OBV|AO|布林|唐奇安|分形|VWAP|回踩|反弹|0\.015", long_rule + short_rule, re.I):
        return None
    allowed_exit = re.compile(
        r"反向(?:均线|EMA)?交叉|均线反向交叉|反向交叉|全部(?:平仓|离场)|清仓|"
        r"任意(?:一条)?均线斜率翻转|EMA\s*斜率翻转|减半仓?|减仓|"
        r"价格偏离[^；;]*ATR\s*(?:止盈)?|盈利[^；;]*ATR\s*(?:止盈)?|"
        r"浮亏[^；;]*ATR\s*(?:强制)?止损|\d+(?:\.\d+)?\s*ATR\s*(?:硬性|强制)?止损|"
        r"平仓|离场|；|;|、|\s"
    )
    remainder = allowed_exit.sub("", exit_rule)
    if remainder:
        return None
    stop = number_before_atr(exit_rule, "止损")
    profit = number_before_atr(exit_rule, "止盈")
    contracts = ["TURN_SLOPE_SIGN_CHANGE_V1"]
    defaults: dict[str, float | int] = {"atr_window": 14}
    if "减仓" in exit_rule and not re.search(r"\d+(?:\.\d+)?\s*%|减半|一半", exit_rule):
        defaults["reduction_fraction"] = 0.5
        contracts.append("REDUCE_HALF_CURRENT_V1")
    return {
        "family": "ma_cross_slope_atr_exit",
        "params": {
            "average_type": average_type,
            "fast_window": fast,
            "slow_window": slow,
            "atr_window": 14,
            "stop_multiple": stop or 0.0,
            "take_profit_multiple": profit or 0.0,
            "reduction_fraction": 0.5,
        },
        "semantic_provenance": "PARAMETER_DEFAULTED",
        "contracts_applied": contracts + ["ATR14_DEFAULT_V1"],
        "defaulted_parameters": defaults,
    }


def rsi_turn_candle(row: dict[str, str]) -> dict[str, object] | None:
    long_rule = row["source_long_condition"]
    short_rule = row["source_short_condition"]
    exit_rule = row["source_exit_condition"]
    long_match = re.fullmatch(
        r"RSI\s*自\s*(\d+(?:\.\d+)?)\s*下方拐头向上收阳(?:线)?开多", long_rule,
    )
    short_match = re.fullmatch(
        r"RSI\s*自\s*(\d+(?:\.\d+)?)\s*上方拐头向下收阴(?:线)?开空", short_rule,
    )
    if not long_match or not short_match:
        return None
    lower, upper = float(long_match.group(1)), float(short_match.group(1))
    has_opposite_exits = re.search(
        rf"RSI\s*触及\s*{upper:g}\s*平多.*(?:触及\s*)?{lower:g}\s*平空", exit_rule,
    )
    if not has_opposite_exits or not re.search(r"回归\s*50\s*中性.*减半", exit_rule):
        return None
    return {
        "family": "rsi_turn_candle",
        "params": {
            "rsi_window": 14,
            "lower_threshold": lower,
            "upper_threshold": upper,
            "neutral_threshold": 50.0,
            "reduction_fraction": 0.5,
        },
        "semantic_provenance": "STANDARD_CONTRACT_RESOLVED",
        "contracts_applied": ["TURN_SLOPE_SIGN_CHANGE_V1"],
        "defaulted_parameters": {},
    }


def adx_ma_di_confluence(row: dict[str, str]) -> dict[str, object] | None:
    long_rule = row["source_long_condition"]
    short_rule = row["source_short_condition"]
    exit_rule = row["source_exit_condition"]
    long_match = re.fullmatch(
        r"ADX\s*[>＞]\s*(\d+(?:\.\d+)?)，(?:价格|价)(?:站上|站)\s*MA\s*(\d+)，\+DI\s*>\s*-DI",
        long_rule,
    )
    short_match = re.fullmatch(
        r"ADX\s*[>＞]\s*(\d+(?:\.\d+)?)，(?:价格|价)(?:跌破|破)\s*MA\s*(\d+)，-DI\s*>\s*\+DI",
        short_rule,
    )
    if not long_match or not short_match or long_match.groups() != short_match.groups():
        return None
    exit_match = re.search(r"ADX\s*[<＜]\s*(\d+(?:\.\d+)?)", exit_rule)
    if not exit_match or not re.search(r"价格反向击穿\s*MA|反向.*MA.*平仓", exit_rule):
        return None
    return {
        "family": "adx_ma_di_confluence",
        "params": {
            "window": int(long_match.group(2)),
            "adx_window": 14,
            "adx_entry_threshold": float(long_match.group(1)),
            "adx_exit_threshold": float(exit_match.group(1)),
        },
        "semantic_provenance": "STANDARD_CONTRACT_RESOLVED",
        "contracts_applied": ["CONFLUENCE_AND_V1"],
        "defaulted_parameters": {},
    }


def four_ma_stable_layered(row: dict[str, str]) -> dict[str, object] | None:
    long_rule = row["source_long_condition"]
    short_rule = row["source_short_condition"]
    exit_rule = row["source_exit_condition"]
    long_match = re.fullmatch(
        r"MA(\d+)>MA(\d+)>MA(\d+)>MA(\d+)，价格站稳全部均线开多", long_rule,
    )
    short_match = re.fullmatch(
        r"MA(\d+)<MA(\d+)<MA(\d+)<MA(\d+)，价格跌破全部均线开空", short_rule,
    )
    if not long_match or not short_match or long_match.groups() != short_match.groups():
        return None
    if not re.fullmatch(r"均线排列(?:结构)?破坏逐层减仓；90\s*日均线拐头全部平仓", exit_rule):
        return None
    windows = [int(value) for value in long_match.groups()]
    if windows != sorted(windows):
        return None
    return {
        "family": "four_ma_stable_layered",
        "params": {
            "fast_window": windows[0], "middle_window": windows[1],
            "slow_window": windows[2], "filter_window": windows[3],
            "consecutive_bars": 2, "reduction_fraction": 0.5,
        },
        "semantic_provenance": "PARAMETER_DEFAULTED",
        "contracts_applied": [
            "STABLE_CLOSE_2BAR_V1", "TURN_SLOPE_SIGN_CHANGE_V1",
            "LAYERED_REDUCTION_EQUAL_V1",
        ],
        "defaulted_parameters": {"persistence_bars": 2, "reduction_stages": 2},
    }


def psar_ma_stable_reduce(row: dict[str, str]) -> dict[str, object] | None:
    long_rule = row["source_long_condition"]
    short_rule = row["source_short_condition"]
    exit_rule = row["source_exit_condition"]
    long_match = re.fullmatch(
        r"PSAR\s*运行于\s*K\s*线下方，价格同步站稳\s*MA(\d+)\s*开多", long_rule,
    )
    short_match = re.fullmatch(
        r"PSAR\s*运行于\s*K\s*线上方，价格同步跌破\s*MA(\d+)\s*开空", short_rule,
    )
    if not long_match or not short_match or long_match.group(1) != short_match.group(1):
        return None
    distance = re.search(r"远离\s*PSAR\s*超\s*(\d+(?:\.\d+)?)\s*ATR\s*减半", exit_rule)
    if not distance or not re.search(r"PSAR\s*翻转.*反向平仓", exit_rule):
        return None
    return {
        "family": "psar_ma_stable_reduce",
        "params": {
            "window": int(long_match.group(1)), "atr_window": 14,
            "multiplier": float(distance.group(1)), "consecutive_bars": 2,
            "reduction_fraction": 0.5,
        },
        "semantic_provenance": "PARAMETER_DEFAULTED",
        "contracts_applied": [
            "STABLE_CLOSE_2BAR_V1", "ATR14_DEFAULT_V1", "CONFLUENCE_AND_V1",
        ],
        "defaulted_parameters": {"persistence_bars": 2, "atr_window": 14},
    }


def psar_atr_distance_exit(row: dict[str, str]) -> dict[str, object] | None:
    long_rule = row["source_long_condition"]
    short_rule = row["source_short_condition"]
    exit_rule = row["source_exit_condition"]
    long_match = re.fullmatch(
        r"PSAR\s*在\s*K\s*线下(?:方)?，价格(?:与\s*PSAR\s*间距|距离\s*PSAR)\s*[>＞大于]+\s*(\d+(?:\.\d+)?)\s*ATR\s*(?:开多)?",
        long_rule,
    )
    short_match = re.fullmatch(
        r"PSAR\s*在\s*K\s*线上(?:方)?，价格(?:与\s*PSAR\s*间距|距离\s*PSAR)\s*[>＞大于]+\s*(\d+(?:\.\d+)?)\s*ATR\s*(?:开空)?",
        short_rule,
    )
    if not long_match or not short_match or long_match.group(1) != short_match.group(1):
        return None
    stop = number_before_atr(exit_rule, "止损")
    profit = number_before_atr(exit_rule, "分层减仓")
    if stop is None or profit is None or not re.search(r"PSAR\s*(?:切换|翻转).*平仓", exit_rule):
        return None
    return {
        "family": "psar_atr_distance_exit",
        "params": {
            "atr_window": 14, "entry_distance_multiple": float(long_match.group(1)),
            "stop_multiple": stop, "take_profit_multiple": profit,
            "reduction_fraction": 0.5,
        },
        "semantic_provenance": "PARAMETER_DEFAULTED",
        "contracts_applied": [
            "CONFLUENCE_AND_V1", "ATR14_DEFAULT_V1", "LAYERED_REDUCTION_EQUAL_V1",
        ],
        "defaulted_parameters": {"atr_window": 14, "reduction_stages": 2},
    }


def ma_rsi_turn_filter(row: dict[str, str]) -> dict[str, object] | None:
    long_rule = row["source_long_condition"]
    short_rule = row["source_short_condition"]
    exit_rule = row["source_exit_condition"]
    long_match = re.fullmatch(
        r"(?:价格(?:站稳|站上)\s*MA|MA)(\d+)\s*(?:多头)?，RSI(?:\s*回落(?:至)?|\s*<|\s*＜)?\s*(\d+(?:\.\d+)?)\s*(?:低吸|企稳)?拐头向上(?:企稳)?(?:开多)?",
        long_rule,
    )
    short_match = re.fullmatch(
        r"(?:价格跌破\s*MA|MA)(\d+)\s*(?:空头)?，RSI(?:\s*反弹(?:至)?|\s*>|\s*＞)?\s*(\d+(?:\.\d+)?)\s*(?:高空|承压)?拐头向下(?:承压)?(?:开空)?",
        short_rule,
    )
    if not long_match or not short_match or long_match.group(1) != short_match.group(1):
        return None
    if not re.search(r"MA\s*" + long_match.group(1), exit_rule) or not (
        "RSI" in exit_rule and ("平多" in exit_rule or "极值反向离场" in exit_rule)
    ):
        return None
    persistence = 2 if "站稳" in long_rule else 1
    defaults = {"persistence_bars": 2} if persistence == 2 else {}
    provenance = "PARAMETER_DEFAULTED" if defaults else "STANDARD_CONTRACT_RESOLVED"
    return {
        "family": "ma_rsi_turn_filter",
        "params": {
            "window": int(long_match.group(1)), "rsi_window": 14,
            "lower_threshold": float(long_match.group(2)),
            "upper_threshold": float(short_match.group(2)),
            "exit_lower_threshold": 30.0, "exit_upper_threshold": 70.0,
            "consecutive_bars": persistence,
        },
        "semantic_provenance": provenance,
        "contracts_applied": [
            "TURN_SLOPE_SIGN_CHANGE_V1", "STABILIZE_MINIMAL_TRANSITION_V1",
        ] + (["STABLE_CLOSE_2BAR_V1"] if persistence == 2 else []),
        "defaulted_parameters": defaults,
    }


def adx_average_take_profit(row: dict[str, str]) -> dict[str, object] | None:
    long_rule, short_rule, exit_rule = (
        row["source_long_condition"], row["source_short_condition"], row["source_exit_condition"],
    )
    sma_long = re.fullmatch(
        r"ADX\s*[>＞]\s*(\d+(?:\.\d+)?)，MA(\d+)\s*上穿\s*MA(\d+)，价格(?:站|双均线)?(?:双均线)?之上(?:开多)?",
        long_rule,
    )
    sma_short = re.fullmatch(
        r"ADX\s*[>＞]\s*(\d+(?:\.\d+)?)，MA(\d+)\s*下穿\s*MA(\d+)，价格(?:在|双均线)?(?:双均线)?之下(?:开空)?",
        short_rule,
    )
    ema_long = re.fullmatch(
        r"EMA(\d+)\s*上穿\s*EMA(\d+)，ADX\s*[>＞]\s*(\d+(?:\.\d+)?)，\+DI\s*[>＞]\s*-DI(?:开多)?",
        long_rule,
    )
    ema_short = re.fullmatch(
        r"EMA(\d+)\s*下穿\s*EMA(\d+)，ADX\s*[>＞]\s*(\d+(?:\.\d+)?)，-DI\s*[>＞]\s*\+DI(?:开空)?",
        short_rule,
    )
    exit_adx = re.search(r"ADX\s*[<＜]\s*(\d+(?:\.\d+)?)", exit_rule)
    profit = number_before_atr(exit_rule, "止盈")
    if not exit_adx or profit is None or "分层" in exit_rule:
        return None
    if sma_long and sma_short and sma_long.groups() == sma_short.groups():
        family, entry, fast, slow = (
            "adx_sma_take_profit", float(sma_long.group(1)),
            int(sma_long.group(2)), int(sma_long.group(3)),
        )
    elif ema_long and ema_short and ema_long.groups() == ema_short.groups():
        family, entry, fast, slow = (
            "ema_adx_take_profit", float(ema_long.group(3)),
            int(ema_long.group(1)), int(ema_long.group(2)),
        )
    else:
        return None
    return {
        "family": family,
        "params": {
            "fast_window": fast, "slow_window": slow, "adx_window": 14,
            "atr_window": 14, "adx_entry_threshold": entry,
            "adx_exit_threshold": float(exit_adx.group(1)),
            "take_profit_multiple": profit,
        },
        "semantic_provenance": "PARAMETER_DEFAULTED",
        "contracts_applied": ["CONFLUENCE_AND_V1", "ATR14_DEFAULT_V1"],
        "defaulted_parameters": {"atr_window": 14},
    }


def compile_definitions(manifest: list[dict[str, str]]) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {
        "xlsx_s1_0011": {
            "family": "bollinger_width_cross",
            "params": {"fast_window": 5, "slow_window": 50},
            "semantic_provenance": "SOURCE_EXACT",
            "contracts_applied": ["STANDARD_RULESET_ALREADY_RESOLVABLE_V1"],
            "defaulted_parameters": {},
        }
    }
    for row in manifest:
        # Full-family matchers below are the semantic gate.  Do not use the
        # mutable current audit status as an input: after a successful audit
        # rerun a recovered row is (correctly) labelled implemented, and a
        # status-gated compiler would then erase it on its next invocation.
        if row["registry_id"] in result or row["registry_id"] in PHASE2_1_IMPLEMENTED:
            continue
        definition = (
            ma_cross_slope(row)
            or rsi_turn_candle(row)
            or adx_ma_di_confluence(row)
            or four_ma_stable_layered(row)
            or psar_ma_stable_reduce(row)
            or psar_atr_distance_exit(row)
            or ma_rsi_turn_filter(row)
            or adx_average_take_profit(row)
        )
        if definition is not None:
            result[row["registry_id"]] = definition
    return dict(sorted(result.items()))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=AUDIT / "strategy_workbook_conversion_manifest.csv")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    definitions = compile_definitions(rows(args.manifest))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(json.dumps(definitions, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, args.output)
    counts: dict[str, int] = {}
    for item in definitions.values():
        family = str(item["family"])
        counts[family] = counts.get(family, 0) + 1
    print(json.dumps({"compiled": len(definitions), "families": counts}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
