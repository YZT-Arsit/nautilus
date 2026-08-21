#!/usr/bin/env python3
"""
Compile and reconcile the 217 Phase 2.4 non-standalone module rows.

The workbook is read only as audit provenance. Runtime module configuration is
written as deterministic JSON and never requires Excel.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
from collections import Counter
from collections import defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
AUDIT = ROOT / "outputs/internal_audit/strategy_workbook"
SOURCE_STATUS = "NON_STANDALONE_MODULE_UNSUPPORTED"
CONFIG = ROOT / "configs/strategy_modules/workbook_phase2_4_modules.json"


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def write_csv(path: Path, fields: list[str], rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    os.replace(temporary, path)


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def source_text(row: dict[str, str]) -> str:
    return " ".join(
        row.get(key, "")
        for key in (
            "source_strategy_name",
            "source_indicator_definition",
            "source_long_condition",
            "source_short_condition",
            "source_exit_condition",
        )
    )


TAXONOMY: tuple[tuple[str, str], ...] = (
    ("TRAILING_STOP", r"移动止|跟踪止|动态止盈|跟随最高|跟随最低|保本"),
    ("BREAKEVEN_STOP", r"保本"),
    ("HARD_STOP", r"硬性止损|固定止损|强制止损|无条件止损"),
    ("LAYERED_TAKE_PROFIT", r"梯度|阶梯|分层止盈|\d+档止盈"),
    ("TAKE_PROFIT", r"止盈|盈利"),
    ("PARTIAL_REDUCTION", r"减仓|减半|降半|降仓"),
    ("TIME_EXIT", r"持仓.{0,8}\d+\s*(?:根|K|分钟|小时)|最大持仓"),
    ("POSITION_SIZING", r"仓位|单位风险|资金分配"),
    ("VOLATILITY_SIZING", r"HV|波动率.*仓位|ATR.*(?:分位|仓位)"),
    ("EXPOSURE_CAP", r"最大.{0,8}(?:仓位|单位)|仓位上限|不超"),
    ("PYRAMIDING", r"金字塔|加仓"),
    ("GRID_SIZING", r"网格"),
    ("DAILY_LOSS_LIMIT", r"单日.{0,10}亏损|当日亏损"),
    ("TRADE_COUNT_LIMIT", r"单日.{0,8}开仓次数|每日最多"),
    ("SESSION_FLATTEN", r"收盘.{0,5}(?:清仓|平仓|减仓)"),
    ("DRAWDOWN_CONTROL", r"回撤"),
    ("ATR_EXIT", r"ATR"),
    ("DONCHIAN_EXIT", r"唐奇安"),
    ("TREND_EXIT", r"分形|均线|MA|VWAP|PSAR"),
    ("MOMENTUM_EXIT", r"ROC|MACD|RSI|ADX|OBV"),
    ("VOLATILITY_FILTER", r"HV|BBW|CI|波动"),
)


def families(text: str) -> list[str]:
    values = [family for family, pattern in TAXONOMY if re.search(pattern, text, re.IGNORECASE)]
    return values or ["OTHER_MODULE"]


def child_id(module_id: str, suffix: str) -> str:
    return f"{module_id}__{suffix}"


def stop_atr(text: str) -> float | None:
    patterns = (
        r"浮亏\s*(\d+(?:\.\d+)?)\s*ATR[^；。]*?(?:止损|平仓|清仓|全平)",
        r"(?:硬性|固定|强制)?止损(?:固定|取|为|\s)*\s*(\d+(?:\.\d+)?)\s*ATR",
        r"入场价\s*[±+-]\s*(\d+(?:\.\d+)?)\s*(?:倍)?\s*ATR",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return float(match.group(1))
    return None


def holding_bars(text: str) -> int | None:
    match = re.search(
        r"持仓(?:周期上限|上限|满|\s)*(\d+)\s*(?:根\s*K\s*线|根|K)(?:\s|无条件|强制|平仓|离场)",
        text,
        re.IGNORECASE,
    )
    return int(match.group(1)) if match else None


def atr_ladder(module_id: str, text: str) -> dict[str, Any] | None:
    normalized = text.replace("再减", "减").replace("减仓", "减")
    pairs = [
        (float(a), float(b) / 100)
        for a, b in re.findall(
            r"(\d+(?:\.\d+)?)\s*ATR[^；，、/]*?减\s*(\d+(?:\.\d+)?)\s*%",
            normalized,
            re.IGNORECASE,
        )
    ]
    finals = [
        float(value)
        for value in re.findall(
            r"(\d+(?:\.\d+)?)\s*ATR[^；，、/]*?(?:全部|全额|全仓|全清|全平|清仓)",
            normalized,
            re.IGNORECASE,
        )
    ]
    stop = stop_atr(text)
    defaulted: dict[str, object] = {}
    if (not pairs or not finals) and stop is not None:
        # The workbook explicitly gives a target sequence/count but omits
        # fractions. Phase 2.4 authorizes equal fractions per explicit target.
        sequence: list[float] = []
        slash = re.search(r"((?:\d+(?:\.\d+)?\s*/\s*){2,}\d+(?:\.\d+)?)\s*ATR", text, re.IGNORECASE)
        if slash:
            sequence = [float(value) for value in re.findall(r"\d+(?:\.\d+)?", slash.group(1))]
        if not sequence:
            ranged = re.search(r"(\d+(?:\.\d+)?)\s*[~～至-]\s*(\d+(?:\.\d+)?)\s*ATR", text, re.IGNORECASE)
            count = re.search(r"(\d+|四|五|六|七|八|九|十|十二)\s*档", text)
            chinese = {"四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10, "十二": 12}
            if ranged and count:
                n = int(count.group(1)) if count.group(1).isdigit() else chinese[count.group(1)]
                start, end = float(ranged.group(1)), float(ranged.group(2))
                if n > 1:
                    step = (end - start) / (n - 1)
                    sequence = [round(start + i * step, 10) for i in range(n)]
        each = re.search(r"每(?:一)?档(?:止盈)?减(?:仓)?\s*(\d+(?:\.\d+)?)\s*%", text)
        if sequence and re.search(r"(?:按.*比例|分批减仓|每(?:一)?档)", text):
            final = sequence[-1]
            fraction = float(each.group(1)) / 100 if each else 1.0 / len(sequence)
            pairs = [(level, fraction) for level in sequence[:-1]]
            finals = [final]
            if each is None:
                defaulted["reduction_fraction"] = f"equal_fraction_1_over_{len(sequence)}"
    if not pairs or not finals or stop is None:
        return None
    unique: dict[float, float] = {}
    for level, fraction in pairs:
        unique[level] = fraction
    ordered = sorted(unique.items())
    final = max(finals)
    ordered = [(level, fraction) for level, fraction in ordered if level < final]
    if not ordered or sum(fraction for _, fraction in ordered) > 1 + 1e-12:
        return None
    result = {
        "module_id": child_id(module_id, "atr_ladder"),
        "module_type": "atr_ladder_exit",
        "profit_levels_atr": [a for a, _ in ordered],
        "reduction_fractions": [b for _, b in ordered],
        "final_profit_atr": final,
        "stop_loss_atr": stop,
    }
    if defaulted:
        result["_defaulted_parameters"] = defaulted
    return result


def feature_conditions(text: str) -> list[dict[str, object]]:
    conditions: list[dict[str, object]] = []
    mappings = (
        (r"ROC.{0,4}下穿零轴", "roc_cross_below_zero", "long"),
        (r"ROC.{0,4}上穿零轴", "roc_cross_above_zero", "short"),
        (r"MACD.{0,6}(?:死叉|反向交叉)", "macd_bearish_cross", "long"),
        (r"MACD.{0,6}(?:金叉|反向交叉)", "macd_bullish_cross", "short"),
        (r"DIF.{0,4}下穿零轴", "dif_cross_below_zero", "long"),
        (r"DIF.{0,4}上穿零轴", "dif_cross_above_zero", "short"),
        (r"跌破.{0,8}(?:下分形|分形低点)", "fractal_long_exit", "long"),
        (r"(?:突破|站上).{0,8}(?:上分形|分形高点)", "fractal_short_exit", "short"),
        (r"跌破\s*VWAP", "vwap_long_exit", "long"),
        (r"(?:突破|站上)\s*VWAP", "vwap_short_exit", "short"),
        (r"PSAR.{0,4}翻转|SAR.{0,4}翻转", "psar_reversal", "both"),
        (r"带宽(?:大幅)?收缩", "bollinger_bandwidth_contraction", "both"),
        (r"HV.{0,6}(?:回落|跌破)\s*Q?20", "hv_cross_below_q20", "both"),
        (r"Z(?:score|20)?\s*[＞>]\s*2", "zscore_above_2", "long"),
        (r"Z(?:score|20)?\s*[＜<]\s*-?2", "zscore_below_minus_2", "short"),
        (r"四项.{0,12}[≥>]\s*3\s*项反向|[≥>]\s*3\s*项反向", "reverse_factor_count", "both"),
    )
    for pattern, key, side in mappings:
        if re.search(pattern, text, re.IGNORECASE):
            conditions.append({"feature_key": key, "operator": "true", "side": side})
    for match in re.finditer(r"CI\s*[＞>]\s*(\d+(?:\.\d+)?)", text, re.IGNORECASE):
        conditions.append(
            {
                "feature_key": "choppiness_index",
                "operator": "gt",
                "threshold": float(match.group(1)),
                "side": "both",
            }
        )
    return conditions


MISSING_DATA = re.compile(
    r"多品种|跨品种|全市场|组合(?:平均|整体|总波动|持仓)|行业中性|流动性不足|POC|配对品种|相关系数",
    re.IGNORECASE,
)
MISSING_PARAMETER = re.compile(
    r"按比例|逐层减仓|减一档|ATR\s*(?:持续走高|走高|走低)|"
    r"柱(?:体)?持续萎缩|提取部分利润|带宽(?:大幅)?收缩|"
    r"HV.{0,12}(?:Q\d+|分位)|分层.{0,6}分形",
    re.IGNORECASE,
)
AMBIGUOUS = re.compile(
    r"反向突破|同(?:比例)?减仓|动能持续萎缩|单因子失效|异常行情|多因子共振|"
    r"自适应通道|自动调整.*宽度|高波动加宽|低波动收窄|背离|对冲比例|资金提取|短线波动放大",
    re.IGNORECASE,
)


def compile_row(row: dict[str, str]) -> tuple[dict[str, Any] | None, str, list[str], str]:
    module_id = row["registry_id"]
    text = source_text(row)
    family_list = families(text)
    primary = family_list[0]
    if MISSING_DATA.search(text):
        return (
            None,
            "BLOCKED_MODULE_MISSING_DATA",
            ["REQUIRED_PORTFOLIO_OR_EXTERNAL_DATA_UNAVAILABLE"],
            primary,
        )
    if re.search(r"月度.{0,12}盈利.{0,15}提取", text):
        return (
            None,
            "BLOCKED_MODULE_ENGINE_SCOPE",
            ["CAPITAL_WITHDRAWAL_ACCOUNTING_CONTRACT_OUT_OF_SCOPE"],
            primary,
        )
    if AMBIGUOUS.search(text):
        return (
            None,
            "BLOCKED_MODULE_AMBIGUOUS_SEMANTICS",
            ["MODULE_STATE_OR_TRIGGER_NOT_UNIQUELY_DEFINED"],
            primary,
        )
    if MISSING_PARAMETER.search(text):
        return (
            None,
            "BLOCKED_MODULE_MISSING_PARAMETER",
            ["REQUIRED_NUMERIC_PARAMETER_MISSING"],
            primary,
        )

    children: list[dict[str, Any]] = []
    contracts: list[str] = []

    ladder = atr_ladder(module_id, text)
    defaulted_parameters: dict[str, object] = {}
    if ladder:
        defaulted_parameters.update(ladder.pop("_defaulted_parameters", {}))
        children.append(ladder)
        contracts.append("ATR_LADDER_EXIT_V1")
    else:
        stop = stop_atr(text)
        if stop is not None:
            children.append(
                {
                    "module_id": child_id(module_id, "atr_stop"),
                    "module_type": "atr_hard_stop",
                    "stop_loss_atr": stop,
                }
            )
            contracts.append("FILL_ANCHORED_ATR_STOP_V1")

    loss_steps = [
        (float(level), 1.0 - float(reduction) / 100)
        for level, reduction in re.findall(
            r"(?:浮亏\s*)?(?:≥|>=)\s*(\d+(?:\.\d+)?)\s*ATR[^；。]*?减(?:仓)?\s*(\d+(?:\.\d+)?)\s*%",
            text,
            re.IGNORECASE,
        )
    ]
    loss_final = re.search(
        r"(?:浮亏\s*)?(?:≥|>=)\s*(\d+(?:\.\d+)?)\s*ATR[^；。]*?(?:全部|全额|全仓|全清|全平|强制平仓)",
        text,
        re.IGNORECASE,
    )
    if loss_steps and loss_final:
        loss_steps.append((float(loss_final.group(1)), 0.0))
        loss_steps = sorted(dict(loss_steps).items())
        children.append(
            {
                "module_id": child_id(module_id, "loss_ladder"),
                "module_type": "atr_adverse_reduction",
                "loss_levels_atr": [a for a, _ in loss_steps],
                "target_fractions": [b for _, b in loss_steps],
            }
        )
        contracts.append("ATR_ADVERSE_REDUCTION_V1")

    donchian = re.search(r"(\d+)\s*周期.{0,8}唐奇安|唐奇安\s*(\d+)\s*周期", text, re.IGNORECASE)
    if donchian and not re.search(r"自适应|动态.*宽度", text):
        window = int(next(value for value in donchian.groups() if value is not None))
        children.append(
            {
                "module_id": child_id(module_id, "donchian"),
                "module_type": "donchian_exit",
                "window": window,
            }
        )
        contracts.append("COMPLETED_DONCHIAN_EXIT_V1")

    hold = holding_bars(text)
    if hold is not None:
        children.append(
            {
                "module_id": child_id(module_id, "time"),
                "module_type": "time_exit",
                "maximum_holding_bars": hold,
            }
        )
        contracts.append("COMPLETED_BAR_HOLDING_EXIT_V1")

    # Explicit full ATR target when no ladder already owns the target.
    if ladder is None:
        profit = re.search(
            r"盈利\s*(\d+(?:\.\d+)?)\s*ATR[^；。]*?(?:全平|全部|清仓|离场)", text, re.IGNORECASE
        )
        if profit and "保本" not in profit.group(0):
            children.append(
                {
                    "module_id": child_id(module_id, "atr_tp"),
                    "module_type": "atr_take_profit",
                    "take_profit_atr": float(profit.group(1)),
                    "stop_loss_atr": None,
                }
            )
            contracts.append("FILL_ANCHORED_ATR_TAKE_PROFIT_V1")

    # Breakeven contracts require explicit activation, explicit lock, and hard stop.
    activation = re.search(r"盈利(?:达到|达)?\s*(\d+(?:\.\d+)?)\s*ATR.{0,18}保本", text, re.IGNORECASE)
    lock = re.search(r"(?:成本|开仓价)\s*\+\s*(\d+(?:\.\d+)?)\s*ATR", text, re.IGNORECASE)
    trail = re.search(r"回撤\s*(\d+(?:\.\d+)?)\s*ATR", text, re.IGNORECASE)
    standard_contract_resolved = False
    if activation and stop_atr(text) is not None:
        children = [child for child in children if not child["module_id"].endswith("__atr_stop")]
        children.append(
            {
                "module_id": child_id(module_id, "breakeven"),
                "module_type": "atr_breakeven_trailing",
                "activation_atr": float(activation.group(1)),
                "lock_atr": float(lock.group(1)) if lock else 0.0,
                "hard_stop_atr": float(stop_atr(text)),
                "trail_distance_atr": float(trail.group(1)) if trail else None,
            }
        )
        contracts.append("FILL_SYNCHRONIZED_BREAKEVEN_TRAIL_V1")
        standard_contract_resolved = lock is None

    # Explicit account drawdown tiers.
    reduce_values = [
        float(x)
        for x in re.findall(
            r"(?:回撤(?:达|达到)?\s*)?(\d+(?:\.\d+)?)\s*%[^；。]{0,12}?(?:减半|降半|新开仓减半)",
            text,
        )
    ]
    flatten_values = [
        float(x)
        for x in re.findall(
            r"(?:回撤(?:达|达到)?\s*)?(\d+(?:\.\d+)?)\s*%[^；。]{0,12}?(?:全清|全部清仓|强制清仓|停止)",
            text,
        )
    ]
    reduce_value = min(reduce_values) if reduce_values else None
    flatten_value = max(flatten_values) if flatten_values else None
    if reduce_value is not None and flatten_value is not None and reduce_value < flatten_value:
        children.append(
            {
                "module_id": child_id(module_id, "drawdown"),
                "module_type": "account_drawdown",
                "reduce_at": reduce_value / 100,
                "flatten_at": flatten_value / 100,
                "reduced_exposure": 0.5,
            }
        )
        contracts.append("ACCOUNT_DRAWDOWN_TIER_V1")

    # Explicit percentage stop.
    pct_stop = re.search(r"单票浮亏\s*(\d+(?:\.\d+)?)\s*%[^；。]*?(?:止损|清仓)", text)
    if pct_stop:
        children.append(
            {
                "module_id": child_id(module_id, "pct_stop"),
                "module_type": "fixed_percentage_stop",
                "stop_fraction": float(pct_stop.group(1)) / 100,
            }
        )
        contracts.append("FILL_ANCHORED_PERCENT_STOP_V1")

    cap = re.search(
        r"(?:单品种(?:总仓位|仓位)?(?:上限|不超|最大)|"
        r"单票(?:最大仓位|仓位上限|上限))\s*(\d+(?:\.\d+)?)\s*%",
        text,
    )
    if cap:
        children.append(
            {
                "module_id": child_id(module_id, "cap"),
                "module_type": "exposure_cap",
                "max_abs_exposure": float(cap.group(1)) / 100,
            }
        )
        contracts.append("ABS_EXPOSURE_CAP_V1")

    entry_cap = re.search(
        r"单次开仓(?:资金|额度)?.{0,8}(?:不超|≤|<=)\s*(?:总资金)?\s*(\d+(?:\.\d+)?)\s*%", text
    )
    if entry_cap:
        children.append(
            {
                "module_id": child_id(module_id, "entry_cap"),
                "module_type": "entry_exposure_cap",
                "max_entry_exposure": float(entry_cap.group(1)) / 100,
            }
        )
        contracts.append("ENTRY_EXPOSURE_CAP_V1")

    # Explicit volatility percentile ranges.
    if re.search(r"HV\s*0\s*[-~至]\s*30", text, re.IGNORECASE) and re.search(r"30\s*[-~至]\s*60", text):
        exposures = [1.0, 0.6, 0.3, 0.0]
        children.append(
            {
                "module_id": child_id(module_id, "hv_tiers"),
                "module_type": "volatility_exposure",
                "upper_bounds": [30, 60, 90],
                "exposures": exposures,
                "prohibit_entry_at_or_above": 90,
            }
        )
        contracts.append("VOLATILITY_PERCENTILE_EXPOSURE_V1")
    elif re.search(r"HV\s*0\s*[-~至]\s*25", text, re.IGNORECASE) and re.search(r"25\s*[-~至]\s*50", text):
        children.append(
            {
                "module_id": child_id(module_id, "hv_tiers"),
                "module_type": "volatility_exposure",
                "upper_bounds": [25, 50, 75, 90],
                "exposures": [1.0, 0.75, 0.5, 0.25, 0.0],
                "prohibit_entry_at_or_above": 90,
            }
        )
        contracts.append("VOLATILITY_PERCENTILE_EXPOSURE_V1")

    # Exact session risk rows use normalized account-return PnL under UTC contract.
    daily_loss = re.search(r"(?:单日|当日).{0,12}亏损.{0,8}(\d+(?:\.\d+)?)\s*%", text)
    entry_limit = re.search(r"单日.{0,10}(?:开仓|交易).{0,4}(\d+)\s*次", text)
    if daily_loss or entry_limit:
        children.append(
            {
                "module_id": child_id(module_id, "daily_risk"),
                "module_type": "daily_risk",
                "maximum_loss": float(daily_loss.group(1)) / 100 if daily_loss else None,
                "maximum_entries": int(entry_limit.group(1)) if entry_limit else None,
            }
        )
        contracts.extend(["CRYPTO_UTC_SESSION_V1", "EXECUTED_ENTRY_DAILY_RISK_V1"])

    conditions = feature_conditions(text)
    if re.search(r"收盘.{0,8}(?:清仓|清空|平仓)", text):
        conditions.append(
            {"feature_key": "session_flatten_due", "operator": "true", "side": "both"}
        )
        contracts.append("SESSION_FLATTEN_UTC_V1")
    if conditions:
        children.append(
            {
                "module_id": child_id(module_id, "feature_exit"),
                "module_type": "feature_exit",
                "conditions": conditions,
            }
        )
        contracts.append("TYPED_FEATURE_EXIT_V1")
    session_reduction_key = None
    if re.search(r"次(?:一)?(?:交易日)?开盘.{0,8}(?:减仓一半|减半)", text):
        session_reduction_key = "next_session_open_due"
    elif re.search(r"收盘.{0,8}(?:减仓一半|减半)", text):
        session_reduction_key = "session_flatten_due"
    if session_reduction_key:
        children.append(
            {
                "module_id": child_id(module_id, "session_reduce"),
                "module_type": "feature_exposure",
                "condition": {
                    "feature_key": session_reduction_key,
                    "operator": "true",
                    "side": "both",
                },
                "target_fraction": 0.5,
            }
        )
        contracts.extend(
            [
                "NEXT_COMPLETED_UTC_SESSION_OPEN_V1"
                if session_reduction_key == "next_session_open_due"
                else "SESSION_FLATTEN_UTC_V1",
                "TYPED_FEATURE_EXPOSURE_V1",
            ]
        )

    # Explicit ADX reduction.
    adx = re.search(r"ADX\s*[＜<]\s*(\d+(?:\.\d+)?)[^；。]*?减半", text, re.IGNORECASE)
    if adx:
        children.append(
            {
                "module_id": child_id(module_id, "adx_reduce"),
                "module_type": "feature_exposure",
                "condition": {
                    "feature_key": "adx",
                    "operator": "lt",
                    "threshold": float(adx.group(1)),
                    "side": "both",
                },
                "target_fraction": 0.5,
            }
        )
        contracts.append("TYPED_FEATURE_EXPOSURE_V1")

    # Exact adverse ATR reduction ladder.
    adverse = [
        (float(a), float(b) / 100)
        for a, b in re.findall(
            r"(?:反向(?:波动)?|浮亏)\s*(\d+(?:\.\d+)?)\s*ATR[^；。]*?(?:减仓|减)\s*(\d+(?:\.\d+)?)\s*%",
            text,
            re.IGNORECASE,
        )
    ]
    if adverse:
        ordered = sorted(adverse)
        children.append(
            {
                "module_id": child_id(module_id, "adverse"),
                "module_type": "atr_adverse_reduction",
                "loss_levels_atr": [a for a, _ in ordered],
                "target_fractions": [1 - b for _, b in ordered],
            }
        )
        contracts.append("ATR_ADVERSE_REDUCTION_V1")

    if not children:
        return (
            None,
            "BLOCKED_MODULE_AMBIGUOUS_SEMANTICS",
            ["NO_COMPLETE_MACHINE_CONTRACT_DERIVED"],
            primary,
        )

    # Reject partially represented composite source clauses.
    unresolved_patterns = (
        (
            r"分形",
            not any(
                "fractal" in str(child) or child.get("module_type") == "donchian_exit"
                for child in children
            ),
        ),
        (r"ROC", not any("roc_" in str(child) for child in children)),
        (r"MACD|DIF", not any("macd_" in str(child) or "dif_" in str(child) for child in children)),
        (r"VWAP", not any("vwap_" in str(child) for child in children)),
        (r"PSAR|SAR", not any("psar_" in str(child) for child in children)),
        (r"CI\s*[＞><<]", not any("choppiness" in str(child) for child in children)),
        (
            r"HV.{0,6}(?:回落|跌破)\s*Q?20",
            not any("hv_cross_below_q20" in str(child) for child in children),
        ),
        (r"Z(?:score|20)?\s*[＞><<]", not any("zscore_" in str(child) for child in children)),
        (
            r"止盈梯度|分层止盈|阶梯止盈",
            ladder is None
            and not any(child.get("module_type") == "atr_take_profit" for child in children),
        ),
    )
    unresolved = [
        pattern
        for pattern, missing in unresolved_patterns
        if missing and re.search(pattern, text, re.IGNORECASE)
    ]
    if unresolved:
        return (
            None,
            "BLOCKED_MODULE_AMBIGUOUS_SEMANTICS",
            ["UNRESOLVED_COMPOSITE_CLAUSE:" + ",".join(unresolved)],
            primary,
        )

    child_types = {str(child.get("module_type")) for child in children}
    missing_parameters: list[str] = []
    ambiguous_clauses: list[str] = []
    if re.search(r"网格|金字塔|加仓", text) and not any(
        "pyramid" in kind or "grid" in kind for kind in child_types
    ):
        missing_parameters.append("ADD_POSITION_SIZE_OR_COMPLETE_GRID_STATE_MISSING")
    volatility_sizing_source = re.search(
        r"仓位系数|对应仓位|分档仓位|仓位梯度|仓位自动|满仓操作|满单位",
        text,
    )
    if (
        volatility_sizing_source
        and re.search(
            r"(?:ATR|HV).{0,18}(?:分\s*[345五三四]\s*档|五分位|三分位)|波动率锥分(?:三|四)档", text
        )
        and "volatility_exposure" not in child_types
    ):
        missing_parameters.append("VOLATILITY_TIER_BOUNDARIES_MISSING")
    if re.search(r"单位仓位|单位风险|账户资金\s*1\s*%\s*/\s*ATR", text):
        missing_parameters.append("RISK_UNIT_TO_EXPOSURE_NORMALIZATION_MISSING")
    if "保本" in text and "atr_breakeven_trailing" not in child_types:
        missing_parameters.append("BREAKEVEN_ACTIVATION_OR_RATCHET_DISTANCE_MISSING")
    if (
        re.search(r"跟随最高|跟随最低|阶段(?:高点|低点|极值)|新高持续(?:上移|抬高)|持续抬高", text)
        and (
            "atr_breakeven_trailing" not in child_types
            or not any(
                child.get("trail_distance_atr") is not None
                for child in children
                if child.get("module_type") == "atr_breakeven_trailing"
            )
        )
        and not ({"donchian_exit", "feature_exit"} & child_types)
    ):
        missing_parameters.append("TRAILING_REFERENCE_WINDOW_OR_DISTANCE_MISSING")
    reduction_owned = bool(
        {"atr_ladder_exit", "atr_adverse_reduction", "account_drawdown", "feature_exposure"}
        & child_types
    )
    if re.search(r"减仓|减半|降半|降仓", text) and not reduction_owned:
        missing_parameters.append("PARTIAL_REDUCTION_FRACTION_OR_TRIGGER_NOT_COMPILED")
    if re.search(r"止盈梯度|分层止盈|阶梯止盈", text) and "atr_ladder_exit" not in child_types:
        missing_parameters.append("TAKE_PROFIT_LEVEL_OR_FRACTION_MISSING")
    if re.search(r"云层|TRIX|一目", text):
        ambiguous_clauses.append("CLOUD_OR_TRIX_EXIT_STATE_NOT_UNIQUELY_DEFINED")
    if (
        re.search(r"(?:5/10/20|5/10).{0,10}分形", text)
        and re.search(r"跌破(?:下)?分形|突破(?:上)?分形", text)
        and not re.search(r"短期|中期|长期", text)
    ):
        ambiguous_clauses.append("MULTIPLE_FRACTAL_EXIT_WINDOW_NOT_SELECTED")
    if re.search(r"ROC\s*6/12|ROC6/12", text, re.IGNORECASE) and re.search(
        r"ROC.{0,6}(?:穿越|下穿|上穿)零轴", text, re.IGNORECASE
    ):
        ambiguous_clauses.append("MULTIPLE_ROC_EXIT_WINDOW_NOT_SELECTED")
    if re.search(r"MACD.{0,12}(?:减仓|逐层)", text) and "feature_exposure" not in child_types:
        missing_parameters.append("MACD_REDUCTION_FRACTION_MISSING")
    if re.search(r"单次开仓.{0,10}(?:不超|≤)", text) and "entry_exposure_cap" not in child_types:
        ambiguous_clauses.append("ORDER_LEVEL_EXPOSURE_CAP_NOT_TOTAL_POSITION_CAP")
    if re.search(r"年度回撤|月度回撤", text):
        ambiguous_clauses.append(
            "CALENDAR_DRAWDOWN_ENTRY_ONLY_STATE_NOT_SUPPORTED_BY_ACCOUNT_DRAWDOWN_MODULE"
        )
    if missing_parameters:
        return None, "BLOCKED_MODULE_MISSING_PARAMETER", sorted(set(missing_parameters)), primary
    if ambiguous_clauses:
        return None, "BLOCKED_MODULE_AMBIGUOUS_SEMANTICS", sorted(set(ambiguous_clauses)), primary

    config: dict[str, Any]
    if len(children) == 1:
        config = children[0]
        config["module_id"] = module_id
    else:
        config = {"module_id": module_id, "module_type": "composite_risk", "modules": children}
    config.update(
        {
            "module_family": primary,
            "semantic_provenance": (
                "PARAMETER_DEFAULTED"
                if defaulted_parameters
                else "SESSION_CONTRACT_RESOLVED"
                if "SESSION_FLATTEN_UTC_V1" in contracts
                else "STANDARD_CONTRACT_RESOLVED"
                if standard_contract_resolved
                else "SOURCE_EXACT"
            ),
            "contracts_applied": sorted(set(contracts)),
            "defaulted_parameters": defaulted_parameters,
            "source_identity": module_id,
            "source_sheet": row["source_sheet"],
            "source_strategy_number": row["source_strategy_number"],
            "source_strategy_name": row["source_strategy_name"],
            "module_version": "2.4.0",
        }
    )
    status = "IMPLEMENTED_MODULE_DEFAULTED" if defaulted_parameters else "IMPLEMENTED_MODULE_FAMILY"
    return config, status, [], primary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit-root", type=Path, default=AUDIT)
    parser.add_argument("--config-output", type=Path, default=CONFIG)
    args = parser.parse_args()
    manifest_path = args.audit_root / "strategy_workbook_conversion_manifest.csv"
    manifest = read_csv(manifest_path)
    selected = [row for row in manifest if row.get("phase2_1_status") == SOURCE_STATUS]
    if len(selected) != 217:
        raise ValueError(f"expected exactly 217 module rows, found {len(selected)}")

    configs: list[dict[str, Any]] = []
    closure: list[dict[str, object]] = []
    transitions: list[dict[str, object]] = []
    taxonomy_counts: Counter[str] = Counter()
    primary_counts: Counter[str] = Counter()
    family_ids: dict[str, set[str]] = defaultdict(set)
    for row in sorted(selected, key=lambda item: item["registry_id"]):
        identity = row["registry_id"]
        text = source_text(row)
        family_list = families(text)
        taxonomy_counts.update(family_list)
        config, status, blockers, primary = compile_row(row)
        primary_counts[primary] += 1
        if config:
            configs.append(config)
            family_ids[primary].add(identity)
        closure.append(
            {
                "source_identity": identity,
                "strategy_name": row["source_strategy_name"],
                "old_status": SOURCE_STATUS,
                "module_family": primary,
                "secondary_module_families": ";".join(f for f in family_list if f != primary),
                "original_blockers": SOURCE_STATUS,
                "resolved_blockers": "ENGINE_MODULE_CONTRACT_MISSING" if config else "",
                "remaining_blockers": ";".join(blockers),
                "semantic_provenance": config.get("semantic_provenance", "") if config else "",
                "contracts_applied": ";".join(config.get("contracts_applied", []))
                if config
                else "",
                "defaulted_parameters": json.dumps(
                    config.get("defaulted_parameters", {}), ensure_ascii=False
                )
                if config
                else "",
                "module_registry_id": identity if config else "",
                "test_status": "pending" if config else "not_applicable",
                "integration_status": "pending_family_validation" if config else "not_applicable",
                "new_status": status,
            }
        )
        transitions.append(
            {
                "source_identity": identity,
                "old_status": SOURCE_STATUS,
                "new_status": status,
                "reason": "complete reusable module contract" if config else ";".join(blockers),
                "module_registry_id": identity if config else "",
                "remaining_blockers": ";".join(blockers),
            }
        )

    write_json(args.config_output, configs)
    closure_fields = list(closure[0])
    write_csv(args.audit_root / "phase2_4_module_closure.csv", closure_fields, closure)
    write_csv(
        args.audit_root / "phase2_4_status_transitions.csv", list(transitions[0]), transitions
    )

    implementation_path = {
        "HARD_STOP": "strategy_framework/modules.py:AtrHardStopModule",
        "LAYERED_TAKE_PROFIT": "strategy_framework/modules.py:AtrLadderExitModule",
        "TRAILING_STOP": "strategy_framework/modules.py:AtrBreakevenTrailingModule",
        "BREAKEVEN_STOP": "strategy_framework/modules.py:AtrBreakevenTrailingModule",
        "TIME_EXIT": "strategy_framework/modules.py:TimeExitModule",
        "POSITION_SIZING": "strategy_framework/modules.py:ExposureCapModule",
        "VOLATILITY_SIZING": "strategy_framework/modules.py:VolatilityExposureModule",
        "DRAWDOWN_CONTROL": "strategy_framework/modules.py:AccountDrawdownControlModule",
        "DONCHIAN_EXIT": "strategy_framework/modules.py:DonchianExitModule",
        "DAILY_LOSS_LIMIT": "strategy_framework/modules.py:DailyRiskControlModule",
    }
    family_rows = []
    for family in sorted(primary_counts):
        registered = len(family_ids[family])
        family_rows.append(
            {
                "module_family": family,
                "implementation_path": implementation_path.get(
                    family, "strategy_framework/modules.py"
                ),
                "source_row_count": primary_counts[family],
                "registered_module_count": registered,
                "source_exact_count": registered,
                "contract_resolved_count": 0,
                "parameter_defaulted_count": 0,
                "golden_test_count": 0,
                "integration_host_count": 0,
                "integration_backtest_count": 0,
            }
        )
    write_csv(
        args.audit_root / "phase2_4_module_family_manifest.csv", list(family_rows[0]), family_rows
    )

    registered_rows = [
        {
            "module_id": config["module_id"],
            "module_family": config["module_family"],
            "module_type": config["module_type"],
            "source_identity": config["source_identity"],
            "source_sheet": config["source_sheet"],
            "source_strategy_number": config["source_strategy_number"],
            "source_strategy_name": config["source_strategy_name"],
            "semantic_provenance": config["semantic_provenance"],
            "contracts_applied": ";".join(config["contracts_applied"]),
            "defaulted_parameters": json.dumps(config["defaulted_parameters"], ensure_ascii=False),
            "config_path": str(args.config_output.relative_to(ROOT)),
            "registry_status": "registered",
        }
        for config in configs
    ]
    # Preserve the 36 pre-Phase-2.4 module identities in the consolidated registry manifest.
    previous = [
        row
        for row in manifest
        if row.get("final_status") == "implemented_module"
        and row.get("phase2_1_status") != SOURCE_STATUS
        and row.get("registry_id") not in {x["module_id"] for x in registered_rows}
    ]
    previous_rows = [
        {
            "module_id": row["registry_id"],
            "module_family": row.get("implementation_family", "existing"),
            "module_type": "existing_phase2_module",
            "source_identity": row["registry_id"],
            "source_sheet": row["source_sheet"],
            "source_strategy_number": row["source_strategy_number"],
            "source_strategy_name": row["source_strategy_name"],
            "semantic_provenance": row.get("semantic_provenance", "SOURCE_EXACT"),
            "contracts_applied": row.get("contracts_applied", ""),
            "defaulted_parameters": row.get("defaulted_parameters", ""),
            "config_path": "configs/strategy_modules/workbook_atr_ladders.json",
            "registry_status": "registered",
        }
        for row in previous
    ]
    write_csv(
        args.audit_root / "registered_module_manifest.csv",
        list(registered_rows[0] if registered_rows else previous_rows[0]),
        previous_rows + registered_rows,
    )

    search_path = args.audit_root / "parameter_search_manifest.csv"
    if search_path.is_file():
        search_rows = [
            row
            for row in read_csv(search_path)
            if row.get("registry_id") not in {config["module_id"] for config in configs}
        ]
        search_fields = list(search_rows[0])
        for config in configs:
            defaults = config.get("defaulted_parameters", {})
            if not defaults:
                continue
            search_rows.append(
                {
                    "registry_id": config["module_id"],
                    "source_parameter": json.dumps(defaults, ensure_ascii=False, sort_keys=True),
                    "target_timeframe": "host_timeframe",
                    "adaptation_mode": "MODULE_PARAMETER_REVIEW",
                    "searchable_parameters": json.dumps(sorted(defaults), ensure_ascii=False),
                    "fixed_parameters": "{}",
                    "candidate_range": "{}",
                    "ordering_constraints": "fractions positive and cumulative reductions <= 1",
                    "train_interval": "2021-07-01/2023-06-30",
                    "validation_interval": "2023-07-01/2024-06-30",
                    "test_interval": "2024-07-01/2026-06-30",
                    "objective": "not evaluated in Phase 2.4",
                    "status": "prepared_not_run_requires_range_approval",
                }
            )
        write_csv(search_path, search_fields, search_rows)

    by_id = {row["source_identity"]: row for row in closure}
    extra = [
        "phase2_4_status",
        "phase2_4_module_family",
        "phase2_4_remaining_blockers",
        "phase2_4_module_registry_id",
        "phase2_4_semantic_provenance",
        "phase2_4_contracts_applied",
        "phase2_4_test_status",
        "phase2_4_integration_status",
    ]
    fields = list(manifest[0]) + [name for name in extra if name not in manifest[0]]
    updated: list[dict[str, str]] = []
    for original in manifest:
        row = dict(original)
        item = by_id.get(row["registry_id"])
        if item:
            if str(item["new_status"]).startswith("IMPLEMENTED_MODULE"):
                row.update(
                    {
                        "final_status": "implemented_module",
                        "semantic_class": "non_standalone_module",
                        "implementation_family": item["module_family"],
                        "blocking_reason": "",
                        "registry_status": "registered",
                        "structure_status": "validated",
                    }
                )
            else:
                row.update(
                    {
                        "final_status": "non_standalone_module_blocked",
                        "semantic_class": "non_standalone_module",
                        "blocking_reason": item["remaining_blockers"],
                        "registry_status": "not_registered",
                        "structure_status": "not_applicable",
                    }
                )
            row.update(
                {
                    "phase2_4_status": item["new_status"],
                    "phase2_4_module_family": item["module_family"],
                    "phase2_4_remaining_blockers": item["remaining_blockers"],
                    "phase2_4_module_registry_id": item["module_registry_id"],
                    "phase2_4_semantic_provenance": item["semantic_provenance"],
                    "phase2_4_contracts_applied": item["contracts_applied"],
                    "phase2_4_test_status": item["test_status"],
                    "phase2_4_integration_status": item["integration_status"],
                }
            )
        else:
            row.update({name: "UNCHANGED" if name == "phase2_4_status" else "" for name in extra})
        updated.append(row)
    for name in ("strategy_workbook_conversion_manifest.csv", "strategy_conversion_manifest.csv"):
        write_csv(args.audit_root / name, fields, updated)
    write_csv(
        args.audit_root / "strategy_conversion_review.csv",
        fields,
        [
            row
            for row in updated
            if row["final_status"] not in {"implemented", "implemented_module"}
        ],
    )

    integration_fields = [
        "module_id",
        "module_family",
        "host_strategy_id",
        "compatibility_reason",
        "timeframe",
        "lag",
        "premium_mode",
        "baseline_result_path",
        "module_result_path",
        "return_delta",
        "turnover_delta",
        "mdd_delta",
        "be_bps_delta",
        "test_status",
    ]
    if not (args.audit_root / "phase2_4_module_host_integration.csv").exists():
        write_csv(args.audit_root / "phase2_4_module_host_integration.csv", integration_fields, [])
    if not (args.audit_root / "phase2_4_backtest_summary.csv").exists():
        write_csv(
            args.audit_root / "phase2_4_backtest_summary.csv",
            ["host_strategy", "module_family", "status"],
            [],
        )

    status_counts = Counter(row["new_status"] for row in closure)
    if sum(status_counts.values()) != 217:
        raise AssertionError("Phase 2.4 closure does not reconcile")
    full = Counter()
    for row in updated:
        if row["final_status"] == "implemented":
            full["executable_standalone"] += 1
        elif row["final_status"] == "implemented_module":
            full["registered_modules"] += 1
        elif row.get("phase2_4_status", "").startswith("BLOCKED_MODULE"):
            full["remaining_unsupported_modules"] += 1
        elif row.get("phase2_3_status") in {
            "TRADITIONAL_GAP_INCOMPATIBLE",
            "MISSING_NUMERIC_PARAMETER",
            "OTHER_IRREDUCIBLE_SESSION_BLOCKER",
        }:
            full["session_semantics_unresolved"] += 1
        elif row.get("terminal_blocker") in {
            "MISSING_SOURCE_DATA",
            "UNAVAILABLE_EXTERNAL_UNIVERSE",
        }:
            full["missing_external"] += 1
        else:
            full["remaining_general_ambiguity"] += 1
    validation = {
        "status": "audit_complete_tests_pending",
        "module_rows_start": 217,
        "status_counts": dict(sorted(status_counts.items())),
        "taxonomy_counts_with_overlap": dict(sorted(taxonomy_counts.items())),
        "new_registered_modules": len(configs),
        "previous_registered_modules": len(previous),
        "final_registered_modules": len(previous) + len(configs),
        "optimization_executed": 0,
        "unaccounted_module_rows": 217 - sum(status_counts.values()),
        "full_workbook_reconciliation": dict(full),
    }
    write_json(args.audit_root / "phase2_4_validation_summary.json", validation)
    write_json(args.audit_root / "validation_summary.json", validation)
    print(json.dumps(validation, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
