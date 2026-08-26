#!/usr/bin/env python3
"""Build the read-only Phase 5D policy-impact audit for 989 unresolved rows."""
from __future__ import annotations

import csv
import hashlib
import html
import json
import os
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[2]
AUDIT = ROOT / "outputs/internal_audit/strategy_workbook"
PHASE5C_DELIVERY = ROOT / "outputs/deliverables/workbook_strategies_phase5c"
DELIVERABLE = ROOT / "outputs/deliverables/workbook_strategies_phase5d"

PERFORMANCE_COLUMNS = {
    "return", "return_1x", "final_return_1x", "be", "signed_be_bps",
    "max_drawdown", "mdd", "turnover", "pnl", "sharpe",
}
LEVEL_WORDS = (
    "ma", "ema", "vwap", "中线", "均线", "前高", "前低", "新高", "新低",
    "支撑", "阻力", "压力", "轨", "通道", "pivot", "轴心", "突破位",
)
EXPLICIT_VOL_WORDS = (
    "atr", "hv", "gv", "bbw", "布林带宽", "波动率锥", "标准差", "方差",
    "真实波幅", "historical volatility",
)
STANDARD_FEATURES = {
    "ROC": ("roc", "变动率"),
    "MOMENTUM": ("momentum", "mom指标", "动量指标momentum"),
    "MFI": ("mfi", "资金流"),
    "BOLLINGER_BANDWIDTH": ("bbw", "布林带宽"),
    "AROON": ("aroon", "阿隆"),
    "CMF": ("cmf", "蔡金资金流"),
    "OBV": ("obv", "能量潮"),
    "CCI": ("cci", "顺势指标"),
    "WILLIAMS_R": ("w%r", "威廉"),
    "STOCHASTIC": ("stoch", "随机指标"),
    "PSAR": ("psar", "抛物线"),
    "SUPERTREND": ("supertrend", "超级趋势"),
    "KELTNER": ("keltner", "kc通道", "kc 通道"),
    "DONCHIAN": ("donchian", "唐奇安"),
    "DPO": ("dpo", "去趋势"),
    "CMO": ("cmo", "钱德动量"),
    "TRIX": ("trix",),
    "KVO": ("kvo", "克林格"),
}
EXTERNAL_WORDS = (
    "上涨家数", "下跌家数", "市场宽度", "trin", "nh/nl", "期权", "隐含波动率",
    "订单簿", "order book", "盘口", "链上", "宏观", "跨品种", "指数成分",
)
INDICATOR_WORDS = (
    "rsi", "macd", "obv", "cci", "roc", "mfi", "ao", "cmo", "dpo",
    "trix", "kvo", "stoch", "w%r", "adx", "价格", "成交量",
)


@dataclass(frozen=True)
class Policy:
    policy_id: str
    family: str
    definition: str
    intrusiveness: str
    rationale: str
    runtime: bool = False
    data: bool = False
    accounting: bool = False
    recommendation: str = "CONSIDER_WITH_EXPLICIT_USER_APPROVAL"
    provenance: str = "MODELLED_BASELINE_INTERPRETATION"


POLICIES = (
    Policy("EXISTING_VOLATILITY_FEATURE_PROPAGATION", "VOLATILITY", "For an explicitly named X, reuse X[t] versus SMA(X,20).", "LOW", "Existing approved contract; only propagation is missing.", recommendation="RECOMMEND_FOR_PHASE5E", provenance="STANDARD_CONTRACT_RESOLVED"),
    Policy("EXISTING_TWO_CLOSE_STABILITY_PROPAGATION", "STABILIZATION", "Reuse two completed closes above/below an identified level.", "LOW", "Existing approved persistence contract.", recommendation="RECOMMEND_FOR_PHASE5E", provenance="STANDARD_CONTRACT_RESOLVED"),
    Policy("EXISTING_LEVEL_TOLERANCE_PROPAGATION", "LEVEL", "Reuse the canonical 0.25 ATR(14) tolerance around an identified level.", "LOW", "Existing approved tolerance contract.", recommendation="RECOMMEND_FOR_PHASE5E", provenance="STANDARD_CONTRACT_RESOLVED"),
    Policy("EXISTING_DEFAULT_PROPAGATION", "NUMERIC", "Reuse only an already-canonical period/default for the explicitly named object.", "LOW", "No new numeric value is introduced.", recommendation="RECOMMEND_FOR_PHASE5E", provenance="PARAMETER_DEFAULTED"),
    Policy("EXISTING_FILL_ANCHOR_PROPAGATION", "RISK_ANCHOR", "Reuse the explicitly identified executed-fill anchor already exposed by Phase 5C.", "LOW", "Execution state exists and source anchor is identifiable.", recommendation="RECOMMEND_FOR_PHASE5E", provenance="STANDARD_CONTRACT_RESOLVED"),
    Policy("STANDARD_OHLCV_FEATURE_CONTRACT", "FEATURE", "Add a named, mathematically unique OHLCV-derived feature through the canonical feature contract.", "LOW", "Source names the feature; no external data or formula choice is needed.", runtime=True, recommendation="RECOMMEND_FOR_PHASE5E", provenance="STANDARD_CONTRACT_RESOLVED"),
    Policy("MODELLED_NEXT_HIGHER_TIMEFRAME", "TIMEFRAME", "Use the next canonical higher timeframe for one explicit higher-cycle reference.", "MEDIUM", "The higher-timeframe concept is explicit but interval choice is modelled."),
    Policy("MODELLED_BASE_PLUS_HIGHER_TF", "TIMEFRAME", "Use source base timeframe plus one next canonical higher timeframe.", "MEDIUM", "Two-scale alignment is explicit; exact higher interval is modelled."),
    Policy("MODELLED_BOUNDED_EQUAL_LADDER", "SIZING", "For explicit staged/grid/pyramid entry with clear trigger, use K=4, 0.25x increments, max 1x.", "MEDIUM", "Bounds an explicit ladder but invents the omitted count."),
    Policy("MODELLED_SINGLE_ADD_50_50", "SIZING", "Treat one explicit add event as two equal 0.5x stages.", "MEDIUM", "Relaxes Phase 5C source strictness for a single add."),
    Policy("STANDARD_REGULAR_DIVERGENCE", "DIVERGENCE", "Use regular divergence with confirmed 2-left/2-right pivots and 60-bar history when indicator/direction are explicit.", "MEDIUM", "Standard bounded proxy, but pivot/history are modelling choices."),
    Policy("MODELLED_ATR_RELATIVE_VOLATILITY", "VOLATILITY", "Use ATR(14)/close versus its SMA20 for generic high/low volatility.", "HIGH", "Materially chooses the volatility object."),
    Policy("MODELLED_TURN_HOLD_STABILIZATION", "STABILIZATION", "After decline/rise, require a turn and one subsequent non-worsening completed close.", "HIGH", "Creates a price-action state machine."),
    Policy("MODELLED_SHORT_MEDIUM_LONG_TRIPLET", "TIMEFRAME", "Choose a short/medium/long timeframe triplet from multiple plausible mappings.", "HIGH", "Multiple non-equivalent mappings remain; audit-only and non-deterministic."),
    Policy("MODELLED_STRUCTURAL_TWO_CHOICE", "STRUCTURAL", "Select one of two materially plausible complete state-machine interpretations.", "HIGH", "The source does not uniquely choose between them."),
    Policy("DEFAULT_RSI_DIVERGENCE", "DIVERGENCE", "Assume RSI14 regular divergence when no indicator is supplied.", "VERY_HIGH", "Invents the indicator and pivot semantics.", recommendation="DO_NOT_AUTO_AUTHORIZE"),
    Policy("MARTINGALE_MULTIPLIER_ASSUMPTION", "SIZING", "Invent a martingale/geometric sizing multiplier.", "VERY_HIGH", "Invents economic risk and exposure path.", recommendation="DO_NOT_AUTO_AUTHORIZE"),
    Policy("EXTERNAL_DATA_PROXY_SUBSTITUTION", "DATA", "Substitute an OHLCV proxy for unavailable external data.", "VERY_HIGH", "Changes the economic variable.", data=True, recommendation="DO_NOT_AUTO_AUTHORIZE"),
    Policy("MODELLED_ACCOUNTING_ARCHITECTURE", "ACCOUNTING", "Introduce the source-required shared-capital, compounding, margin, or portfolio accounting model.", "VERY_HIGH", "Requires accounting architecture and economic assumptions.", accounting=True, recommendation="DO_NOT_AUTO_AUTHORIZE"),
    Policy("UNKNOWN_EXIT_DEFAULT", "EXIT", "Invent an exit/stop/take-profit rule where no unique rule exists.", "VERY_HIGH", "Exit semantics materially determine the strategy.", recommendation="DO_NOT_AUTO_AUTHORIZE"),
    Policy("GENERIC_RISK_DISTANCE", "RISK_ANCHOR", "Invent a generic stop/trailing/risk distance.", "VERY_HIGH", "Anchor alone is insufficient without distance/reset semantics.", recommendation="DO_NOT_AUTO_AUTHORIZE"),
    Policy("ARBITRARY_MTF_TRIPLET", "TIMEFRAME", "Assume an arbitrary multi-timeframe triplet without a canonical mapping.", "VERY_HIGH", "Invents observation horizons.", recommendation="DO_NOT_AUTO_AUTHORIZE"),
)
POLICY_BY_ID = {item.policy_id: item for item in POLICIES}
RANK = {"LOW": 1, "MEDIUM": 2, "HIGH": 3, "VERY_HIGH": 4, "IRREDUCIBLE": 5}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def write_csv(path: Path, rows: Iterable[dict[str, object]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    os.replace(temporary, path)


def write_json(path: Path, payload: dict[str, object]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def canonical_source(row: dict[str, str]) -> str:
    return " | ".join(row.get(key, "") for key in (
        "source_strategy_name", "source_indicator_definition", "source_long_condition",
        "source_short_condition", "source_exit_condition",
    )).lower()


def semantic_fingerprint(row: dict[str, str]) -> str:
    text = "|".join(row.get(key, "") for key in (
        "source_indicator_definition", "source_long_condition", "source_short_condition", "source_exit_condition",
    )).lower()
    normalized = re.sub(r"[\W_]+", "", text, flags=re.UNICODE)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:20]


def sizing_taxonomy(text: str) -> str:
    flags: list[str] = []
    if any(word in text for word in ("马丁", "martingale")): flags.append("MARTINGALE_LIKE")
    if any(word in text for word in ("几何", "倍增", "指数加仓")): flags.append("GEOMETRIC_LIKE")
    count_known = bool(re.search(r"(?:上限|最多|分为|共)?\s*[23456789十]\s*(?:层|档|次|阶段)", text))
    if count_known and any(word in text for word in ("加仓", "建仓", "分层", "分档")): flags.append("STAGE_COUNT_KNOWN_SIZE_UNKNOWN")
    if not count_known and any(word in text for word in ("网格", "逐层", "逐格", "分层", "分档", "金字塔")): flags.append("STAGE_COUNT_UNKNOWN")
    if any(word in text for word in ("逐格", "网格", "每下跌", "每上涨")) and not re.search(r"\d+(?:\.\d+)?\s*(?:atr|%|bp|点)", text): flags.append("STEP_DISTANCE_UNKNOWN")
    if any(word in text for word in ("加仓", "逐层", "网格", "金字塔")) and not any(word in text for word in ("上限", "最多", "最大仓位", "1倍", "1x")): flags.append("MAX_EXPOSURE_UNKNOWN")
    if "加仓" in text and not any(word in text for word in ("逐层", "逐格", "网格", "金字塔")): flags.append("CONDITIONAL_ADD_SIZE_UNKNOWN")
    if any(word in text for word in ("减仓", "部分止盈", "分批止盈")) and not re.search(r"(?:减|平)\s*(?:半|1/2|[0-9]+%)", text): flags.append("CONDITIONAL_REDUCE_SIZE_UNKNOWN")
    if any(word in text for word in ("反复", "每次", "持续加仓")) and not count_known: flags.append("REPEAT_COUNT_UNKNOWN")
    flags = list(dict.fromkeys(flags))
    if len(flags) > 1: return "MULTIPLE_SIZING_GAPS:" + ";".join(flags)
    return flags[0] if flags else "SIZING_SEQUENCE_UNKNOWN"


def timeframe_class(text: str) -> tuple[str, str | None, int]:
    multiple = any(word in text for word in ("多周期", "多时间", "短中长", "三周期", "三个周期", "全部周期", "多重周期"))
    higher = any(word in text for word in ("高周期", "大周期", "更高周期", "higher timeframe"))
    two_scale = any(word in text for word in ("大小周期", "当前周期与高周期", "双周期"))
    if multiple:
        return "SHORT_MEDIUM_LONG_OR_MULTI", "MODELLED_SHORT_MEDIUM_LONG_TRIPLET", 3
    if two_scale:
        return "BASE_PLUS_HIGHER", "MODELLED_BASE_PLUS_HIGHER_TF", 1
    if higher:
        return "ONE_HIGHER_REFERENCE", "MODELLED_NEXT_HIGHER_TIMEFRAME", 1
    return "TIMEFRAME_REFERENCE_INCOMPLETE", None, 2


def explicit_indicator(text: str) -> bool:
    return any(word in text for word in INDICATOR_WORDS)


def identified_level(text: str) -> bool:
    return any(word in text for word in LEVEL_WORDS)


def named_features(text: str) -> list[str]:
    return [name for name, words in STANDARD_FEATURES.items() if any(word in text for word in words)]


def numeric_type(text: str) -> tuple[str, bool, str, int]:
    if "atr" in text:
        if any(word in text for word in ("止损", "止盈", "距离", "浮亏")):
            return "ATR_MULTIPLIER", False, "ATR period 14 exists, but the multiplier/distance is non-unique.", 5
        return "LOOKBACK", True, "ATR period 14 is canonical.", 1
    if "adx" in text: return "LOOKBACK", True, "ADX period 14 is canonical.", 1
    if "分形" in text: return "LOOKBACK", True, "Fractal side bars 2 are canonical.", 1
    if any(word in text for word in ("成交量均", "放量", "量能参考")): return "LOOKBACK", True, "Volume reference 20 is canonical.", 1
    if any(word in text for word in ("阶段新高", "阶段新低", "近期高", "近期低")): return "LOOKBACK", True, "Recent-extreme lookback 20 is canonical.", 1
    if "ma" in text and not re.search(r"ma\s*\d+", text): return "MA_PERIOD", False, "Several MA periods are materially plausible.", 6
    if "ema" in text and not re.search(r"ema\s*\d+", text): return "EMA_PERIOD", False, "Several EMA periods are materially plausible.", 6
    if "rsi" in text and not re.search(r"rsi\s*\d+", text): return "RSI_PERIOD", True, "RSI period 14 is canonical where RSI is explicit.", 1
    if any(word in text for word in ("连续", "持续", "确认")): return "PERSISTENCE_BARS", False, "One, two, and three completed bars are plausible.", 3
    if any(word in text for word in ("减仓", "部分")): return "REDUCTION_FRACTION", False, "Several fractions are plausible.", 4
    if "止损" in text: return "STOP_DISTANCE", False, "No dominant distance is authorized.", 6
    if "止盈" in text: return "TAKE_PROFIT_DISTANCE", False, "No dominant distance is authorized.", 6
    if any(word in text for word in ("阈值", "明显", "显著")): return "THRESHOLD", False, "Threshold is context dependent.", 6
    return "OTHER", False, "Numeric object cannot be uniquely inferred.", 6


def accounting_type(text: str) -> str:
    if any(word in text for word in ("组合", "共享资金", "资金池")): return "PORTFOLIO_SHARED_CAPITAL"
    if any(word in text for word in ("复利", "再投资", "累计净值")): return "COMPOUNDING_DEPENDENT"
    if any(word in text for word in ("回撤", "drawdown")): return "DRAWDOWN_BASED_SIZING"
    if any(word in text for word in ("风险预算", "风险平价", "风险单位")): return "RISK_BUDGETING"
    if any(word in text for word in ("多策略", "策略组合")): return "MULTI_STRATEGY_ACCOUNTING"
    if any(word in text for word in ("保证金", "margin", "杠杆")): return "MARGIN_MODEL"
    return "OTHER"


def structural_plausibility(text: str, blocker: str) -> tuple[str, int]:
    if blocker == "SEMANTIC_EXIT_AMBIGUOUS": return "NO_COMPLETE_INTERPRETATION", 0
    alternatives = sum(text.count(word) for word in ("或", "任一", "否则", "分别", "模式", "/"))
    if alternatives <= 1: return "TWO_PLAUSIBLE_INTERPRETATIONS", 2
    return "THREE_PLUS_PLAUSIBLE_INTERPRETATIONS", 3


def resolve_blocker(blocker: str, text: str) -> tuple[str | None, str]:
    """Return the least-intrusive candidate able to resolve one blocker."""
    if blocker == "TIMEFRAME_SET_INCOMPLETE":
        kind, policy, _ = timeframe_class(text)
        if policy == "MODELLED_SHORT_MEDIUM_LONG_TRIPLET":
            return None, "POLICY_NON_UNIQUE_SHORT_MEDIUM_LONG"
        return policy, kind
    if blocker == "SIZING_OR_LADDER_INCOMPLETE":
        taxonomy = sizing_taxonomy(text)
        if "MARTINGALE" in taxonomy or "GEOMETRIC" in taxonomy:
            return "MARTINGALE_MULTIPLIER_ASSUMPTION", taxonomy
        explicit_ladder = any(word in text for word in ("网格", "逐层", "逐格", "分层", "分档", "金字塔"))
        clear_trigger = any(word in text for word in ("每", "触及", "突破", "回落", "反弹", "下跌", "上涨"))
        if explicit_ladder and clear_trigger: return "MODELLED_BOUNDED_EQUAL_LADDER", taxonomy
        if "加仓" in text and clear_trigger: return "MODELLED_SINGLE_ADD_50_50", taxonomy
        return None, taxonomy
    if blocker == "VOLATILITY_REGIME_UNDEFINED":
        if any(word in text for word in EXPLICIT_VOL_WORDS):
            return "EXISTING_VOLATILITY_FEATURE_PROPAGATION", "VOLATILITY_OBJECT_EXPLICIT_THRESHOLD_MISSING"
        if any(word in text for word in ("波动", "高波", "低波")):
            return "MODELLED_ATR_RELATIVE_VOLATILITY", "GENERIC_HIGH_LOW_VOLATILITY"
        return None, "VOLATILITY_OBJECT_IMPLICIT"
    if blocker == "STABILIZATION_OR_REJECTION_UNDEFINED":
        if identified_level(text) and any(word in text for word in ("站稳", "企稳", "承压", "支撑", "反弹")):
            return "EXISTING_TWO_CLOSE_STABILITY_PROPAGATION", "REFERENCE_LEVEL_KNOWN_CONFIRMATION_UNCLEAR"
        if any(word in text for word in ("止跌", "冲高回落", "拐头", "企稳")):
            return "MODELLED_TURN_HOLD_STABILIZATION", "PRICE_ACTION_PATTERN_UNDEFINED"
        return None, "NO_REFERENCE_LEVEL"
    if blocker == "DIVERGENCE_DEFINITION_INCOMPLETE":
        if explicit_indicator(text): return "STANDARD_REGULAR_DIVERGENCE", "PIVOT_DEFINITION_MISSING"
        return "DEFAULT_RSI_DIVERGENCE", "INDICATOR_MISSING"
    if blocker == "MISSING_NUMERIC_PARAMETER":
        parameter, existing, _, _ = numeric_type(text)
        return ("EXISTING_DEFAULT_PROPAGATION" if existing else None), parameter
    if blocker == "LEVEL_TOLERANCE_UNDEFINED":
        if identified_level(text): return "EXISTING_LEVEL_TOLERANCE_PROPAGATION", "LEVEL_IDENTIFIED_CONTRACT_NOT_PROPAGATED"
        return None, "LEVEL_NOT_IDENTIFIED"
    if blocker in {"FILL_ANCHORED_RISK_STATE_REQUIRED", "FILL_ANCHORED_PROFIT_STATE_REQUIRED"}:
        if any(word in text for word in ("均价", "平均成本", "最新加仓", "首次入场", "入场价", "持仓最高", "持仓最低")) and re.search(r"\d+(?:\.\d+)?\s*(?:atr|%|bp|点)", text):
            return "EXISTING_FILL_ANCHOR_PROPAGATION", "ANCHOR_IDENTIFIED"
        return "GENERIC_RISK_DISTANCE", "ANCHOR_OR_DISTANCE_UNCLEAR"
    if blocker == "DATA_OR_FEATURE_CONTRACT_UNAVAILABLE":
        if any(word in text for word in EXTERNAL_WORDS): return "EXTERNAL_DATA_PROXY_SUBSTITUTION", "EXTERNAL_DATA_REQUIRED"
        if named_features(text): return "STANDARD_OHLCV_FEATURE_CONTRACT", "STANDARD_OHLCV_DERIVABLE"
        return None, "FORMULA_NON_UNIQUE"
    if blocker == "UNSUPPORTED_ACCOUNTING_SEMANTICS":
        return "MODELLED_ACCOUNTING_ARCHITECTURE", accounting_type(text)
    if blocker == "SEMANTIC_EXIT_AMBIGUOUS": return "UNKNOWN_EXIT_DEFAULT", "EXIT_NON_UNIQUE"
    if blocker in {"UNPARSEABLE_STRUCTURAL_LOGIC", "STRUCTURAL_RULE_INCOMPLETE", "SEMANTIC_ENTRY_AMBIGUOUS", "BREAKOUT_CONFIRMATION_UNDEFINED"}:
        kind, count = structural_plausibility(text, blocker)
        if blocker == "BREAKOUT_CONFIRMATION_UNDEFINED" and identified_level(text):
            return "EXISTING_TWO_CLOSE_STABILITY_PROPAGATION", "EXPLICIT_LEVEL_CONFIRMATION_NOT_PROPAGATED"
        return ("MODELLED_STRUCTURAL_TWO_CHOICE" if count == 2 else None), kind
    return None, "IRREDUCIBLE_NO_CANDIDATE"


def max_intrusiveness(policy_ids: Iterable[str], irreducible: bool = False) -> str:
    if irreducible: return "IRREDUCIBLE"
    values = [POLICY_BY_ID[item].intrusiveness for item in policy_ids]
    return max(values, key=RANK.get) if values else "IRREDUCIBLE"


def full_closure(row: dict[str, object], enabled: set[str]) -> bool:
    return not row["irreducible"] and set(row["minimum_policies"]) <= enabled


def unique_groups(rows: Iterable[dict[str, object]]) -> int:
    return len({str(row["semantic_fingerprint"]) for row in rows})


def scenario(name: str, enabled: set[str], rows: list[dict[str, object]]) -> dict[str, object]:
    unlocked = [row for row in rows if full_closure(row, enabled)]
    levels = Counter(POLICY_BY_ID[item].intrusiveness for item in enabled)
    return {
        "policy_set_id": name,
        "policies": ";".join(sorted(enabled)),
        "max_intrusiveness": max_intrusiveness(enabled),
        "rows_fully_unlocked": len(unlocked),
        "new_semantic_groups_estimated": unique_groups(unlocked),
        "rows_remaining": len(rows) - len(unlocked),
        "low_assumptions_count": levels["LOW"],
        "medium_assumptions_count": levels["MEDIUM"],
        "high_assumptions_count": levels["HIGH"],
        "very_high_assumptions_count": levels["VERY_HIGH"],
        "requires_new_data": any(POLICY_BY_ID[item].data for item in enabled),
        "requires_accounting_changes": any(POLICY_BY_ID[item].accounting for item in enabled),
        "requires_new_feature_code": any(POLICY_BY_ID[item].runtime for item in enabled),
    }


def load_inputs() -> tuple[list[dict[str, str]], list[dict[str, str]], dict[str, object]]:
    source_manifest = read_csv(AUDIT / "strategy_workbook_conversion_manifest.csv")
    boundary_path = AUDIT / "phase5c_policy_boundary_report.csv"
    if not boundary_path.exists():
        boundary_path = PHASE5C_DELIVERY / "phase5c_policy_boundary_report.csv"
    boundary = read_csv(boundary_path)
    summary_path = AUDIT / "phase5c_validation_summary.json"
    if not summary_path.exists():
        summary_path = PHASE5C_DELIVERY / "phase5c_validation_summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8-sig"))
    return source_manifest, boundary, summary


def build() -> dict[str, object]:
    source_manifest, boundary, phase5c = load_inputs()
    assert len(boundary) == 989
    assert phase5c["final_standalone"] == 254 and phase5c["final_semantic_groups"] == 177
    source_by_id = {row["registry_id"]: row for row in source_manifest}
    assert len(source_by_id) == 1715

    rows: list[dict[str, object]] = []
    sizing_rows: list[dict[str, object]] = []
    numeric_rows: list[dict[str, object]] = []
    structural_rows: list[dict[str, object]] = []
    timeframe_details: dict[str, dict[str, object]] = {}
    feature_rows: list[dict[str, object]] = []

    for blocked in boundary:
        identity = blocked["source_identity"]
        source = source_by_id[identity]
        text = canonical_source(source)
        blockers = [item for item in blocked["remaining_blockers"].split(";") if item]
        policies: list[str] = []
        unresolved: list[str] = []
        details: dict[str, str] = {}
        for blocker in blockers:
            policy, detail = resolve_blocker(blocker, text)
            details[blocker] = detail
            if policy is None:
                unresolved.append(f"{blocker}:{detail}")
            else:
                policies.append(policy)
        policies = list(dict.fromkeys(policies))
        irreducible = bool(unresolved)
        level = max_intrusiveness(policies, irreducible)
        fingerprint = semantic_fingerprint(source)
        policy_families = {POLICY_BY_ID[item].family for item in policies}

        if "SIZING_OR_LADDER_INCOMPLETE" in blockers:
            taxonomy = sizing_taxonomy(text)
            sizing_rows.append({"source_identity": identity, "strategy_name": blocked["strategy_name"], "taxonomy": taxonomy,
                                "candidate_policy": next((p for p in policies if POLICY_BY_ID[p].family == "SIZING"), ""),
                                "fully_closable_after_policy": not irreducible, "source_text": text})
        if "MISSING_NUMERIC_PARAMETER" in blockers:
            parameter, existing, note, alternatives = numeric_type(text)
            numeric_rows.append({"source_identity": identity, "strategy_name": blocked["strategy_name"], "parameter_type": parameter,
                                 "existing_project_default": existing, "dominant_literature_standard": existing,
                                 "materially_plausible_alternatives": alternatives, "status": "EXISTING_DEFAULT_PROPAGATION" if existing else "POLICY_NON_UNIQUE", "notes": note})
        structural_blockers = [item for item in blockers if item in {"UNPARSEABLE_STRUCTURAL_LOGIC", "STRUCTURAL_RULE_INCOMPLETE", "SEMANTIC_ENTRY_AMBIGUOUS", "SEMANTIC_EXIT_AMBIGUOUS", "BREAKOUT_CONFIRMATION_UNDEFINED"}]
        for item in structural_blockers:
            kind, count = structural_plausibility(text, item)
            structural_rows.append({"source_identity": identity, "strategy_name": blocked["strategy_name"], "blocker": item,
                                    "interpretation_class": kind, "plausible_interpretation_count": count,
                                    "automatic_conversion_recommended": False, "notes": details[item]})
        if "TIMEFRAME_SET_INCOMPLETE" in blockers:
            kind, policy, plausible = timeframe_class(text)
            timeframe_details[identity] = {"kind": kind, "policy": policy, "plausible": plausible, "irreducible": irreducible}
        if "DATA_OR_FEATURE_CONTRACT_UNAVAILABLE" in blockers:
            names = named_features(text)
            feature_rows.append({"source_identity": identity, "strategy_name": blocked["strategy_name"],
                                 "classification": details["DATA_OR_FEATURE_CONTRACT_UNAVAILABLE"], "named_features": ";".join(names),
                                 "fully_closable_after_feature": not irreducible})

        if "DATA_OR_FEATURE_CONTRACT_UNAVAILABLE" in blockers and details.get("DATA_OR_FEATURE_CONTRACT_UNAVAILABLE") == "EXTERNAL_DATA_REQUIRED": dominant = "REQUIRES_EXTERNAL_DATA"
        elif "UNSUPPORTED_ACCOUNTING_SEMANTICS" in blockers: dominant = "REQUIRES_ACCOUNTING_ARCHITECTURE"
        elif "SEMANTIC_EXIT_AMBIGUOUS" in blockers: dominant = "EXIT_NON_UNIQUE"
        elif not irreducible and level == "LOW":
            if all(POLICY_BY_ID[p].provenance == "STANDARD_CONTRACT_RESOLVED" for p in policies): dominant = "RECOVERABLE_WITH_EXISTING_POLICY_PROPAGATION"
            else: dominant = "RECOVERABLE_WITH_LOW_NEW_POLICY"
        elif not irreducible and level == "MEDIUM": dominant = "RECOVERABLE_WITH_MEDIUM_NEW_POLICY"
        elif not irreducible and level in {"HIGH", "VERY_HIGH"}: dominant = "RECOVERABLE_ONLY_WITH_HIGH_INTERPRETATION"
        elif any(item in blockers for item in ("UNPARSEABLE_STRUCTURAL_LOGIC", "STRUCTURAL_RULE_INCOMPLETE", "SEMANTIC_ENTRY_AMBIGUOUS")): dominant = "STRUCTURALLY_NON_UNIQUE"
        else: dominant = "OTHER_IRREDUCIBLE"

        rows.append({
            "source_identity": identity, "strategy_name": blocked["strategy_name"], "phase5c_blockers": ";".join(blockers),
            "minimum_policies": policies, "minimum_policy_set": ";".join(policies), "minimum_intrusiveness": level,
            "irreducible": irreducible, "irreducible_reasons": ";".join(unresolved), "details": details,
            "semantic_fingerprint": fingerprint, "data_available": dominant != "REQUIRES_EXTERNAL_DATA",
            "compiler_capable": not any(item in blockers for item in ("UNSUPPORTED_ACCOUNTING_SEMANTICS", "DATA_OR_FEATURE_CONTRACT_UNAVAILABLE")),
            "dominant_next_action": dominant,
            **{f"requires_{family.lower()}_policy": family in policy_families for family in ("TIMEFRAME", "SIZING", "VOLATILITY", "DIVERGENCE", "NUMERIC", "STABILIZATION", "LEVEL", "FEATURE", "ACCOUNTING", "RISK_ANCHOR", "STRUCTURAL", "EXIT")},
        })

    assert len(rows) == 989 and len({row["source_identity"] for row in rows}) == 989

    low = {item.policy_id for item in POLICIES if item.intrusiveness == "LOW"}
    medium = {item.policy_id for item in POLICIES if item.intrusiveness == "MEDIUM"}
    high = {item.policy_id for item in POLICIES if item.intrusiveness == "HIGH"}
    very_high = {item.policy_id for item in POLICIES if item.intrusiveness == "VERY_HIGH"}
    recommended = {item.policy_id for item in POLICIES if item.recommendation == "RECOMMEND_FOR_PHASE5E"}
    optional = {item.policy_id for item in POLICIES if item.recommendation == "CONSIDER_WITH_EXPLICIT_USER_APPROVAL"}
    recommended_low_medium = recommended | {item for item in optional if RANK[POLICY_BY_ID[item].intrusiveness] <= RANK["MEDIUM"]}

    dependency_output = []
    for row in rows:
        dependency_output.append({
            "source_identity": row["source_identity"], "strategy_name": row["strategy_name"], "phase5c_blockers": row["phase5c_blockers"],
            "requires_timeframe_policy": row["requires_timeframe_policy"], "requires_sizing_policy": row["requires_sizing_policy"],
            "requires_volatility_policy": row["requires_volatility_policy"], "requires_divergence_policy": row["requires_divergence_policy"],
            "requires_numeric_default_policy": row["requires_numeric_policy"], "requires_stabilization_policy": row["requires_stabilization_policy"],
            "requires_level_tolerance_policy": row["requires_level_policy"], "requires_feature_policy": row["requires_feature_policy"],
            "requires_accounting_policy": row["requires_accounting_policy"], "requires_risk_anchor_policy": row["requires_risk_anchor_policy"],
            "requires_structural_policy": row["requires_structural_policy"], "requires_exit_policy": row["requires_exit_policy"],
            "minimum_policy_set": row["minimum_policy_set"], "irreducible_even_with_modelled_policies": row["irreducible"],
            "data_available": row["data_available"], "compiler_capable": row["compiler_capable"],
            "dominant_next_action": row["dominant_next_action"], "semantic_fingerprint": row["semantic_fingerprint"],
        })
    write_csv(AUDIT / "phase5d_policy_dependency_audit.csv", dependency_output, list(dependency_output[0]))

    policy_usage: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        for policy in row["minimum_policies"]: policy_usage[policy].append(row)
    triplet_touched_ids = {identity for identity, item in timeframe_details.items() if item["policy"] == "MODELLED_SHORT_MEDIUM_LONG_TRIPLET"}
    policy_usage["MODELLED_SHORT_MEDIUM_LONG_TRIPLET"] = [row for row in rows if row["source_identity"] in triplet_touched_ids]
    registry_rows = []
    for policy in POLICIES:
        applicable = policy_usage[policy.policy_id]
        registry_rows.append({"policy_id": policy.policy_id, "policy_family": policy.family, "candidate_definition": policy.definition,
                              "semantic_intrusiveness": policy.intrusiveness, "applicable_rows": len(applicable), "non_applicable_rows": 989-len(applicable),
                              "rationale": policy.rationale, "would_require_new_runtime_feature": policy.runtime,
                              "would_require_new_data": policy.data, "would_require_accounting_change": policy.accounting,
                              "future_provenance": policy.provenance, "recommended_action": policy.recommendation})
    write_csv(AUDIT / "phase5d_candidate_policy_registry.csv", registry_rows, list(registry_rows[0]))

    single_rows = []
    for policy in POLICIES:
        touched = policy_usage[policy.policy_id]
        unlocked = [row for row in rows if full_closure(row, {policy.policy_id})]
        partial = [row for row in touched if row not in unlocked]
        single_rows.append({"policy_id": policy.policy_id, "rows_where_required": len(touched), "rows_fully_unlocked_by_policy_alone": len(unlocked),
                            "rows_partially_helped": len(partial), "semantic_groups_estimated": unique_groups(unlocked),
                            "intrusiveness": policy.intrusiveness, "future_provenance": policy.provenance})
    write_csv(AUDIT / "phase5d_single_policy_impact.csv", single_rows, list(single_rows[0]))

    tf_rows = []
    for policy_id in ("MODELLED_NEXT_HIGHER_TIMEFRAME", "MODELLED_BASE_PLUS_HIGHER_TF", "MODELLED_SHORT_MEDIUM_LONG_TRIPLET"):
        touched = policy_usage[policy_id]
        unlocked = [row for row in rows if full_closure(row, {policy_id})]
        multi = sum(timeframe_details.get(str(row["source_identity"]), {}).get("plausible", 0) > 1 for row in touched)
        tf_rows.append({"policy_id": policy_id, "rows_touched": len(touched), "rows_fully_unlocked": len(unlocked),
                        "new_semantic_groups_estimated": unique_groups(unlocked), "rows_still_blocked": len(touched)-len(unlocked),
                        "rows_with_multiple_plausible_mappings": multi, "intrusiveness": POLICY_BY_ID[policy_id].intrusiveness})
    write_csv(AUDIT / "phase5d_timeframe_policy_impact.csv", tf_rows, list(tf_rows[0]))
    write_csv(AUDIT / "phase5d_sizing_policy_audit.csv", sizing_rows, list(sizing_rows[0]))
    write_csv(AUDIT / "phase5d_numeric_default_audit.csv", numeric_rows, list(numeric_rows[0]))
    write_csv(AUDIT / "phase5d_structural_ambiguity_audit.csv", structural_rows, list(structural_rows[0]))

    feature_impact = []
    by_feature: dict[str, list[dict[str, object]]] = defaultdict(list)
    for item in feature_rows:
        for name in str(item["named_features"]).split(";"):
            if name: by_feature[name].append(item)
    for name, items in sorted(by_feature.items(), key=lambda pair: (-len(pair[1]), pair[0])):
        identities = {str(item["source_identity"]) for item in items}
        full = [row for row in rows if row["source_identity"] in identities and full_closure(row, {"STANDARD_OHLCV_FEATURE_CONTRACT"})]
        feature_impact.append({"feature_name": name, "formula_uniqueness": "STANDARD_UNIQUE", "rows_requiring_it": len(identities),
                               "rows_fully_unlocked_if_implemented": len(full), "new_semantic_groups_estimated": unique_groups(full),
                               "lookahead_risk": "LOW_IF_COMPLETED_BARS_ONLY", "implementation_complexity": "LOW_MEDIUM"})
    write_csv(AUDIT / "phase5d_feature_policy_impact.csv", feature_impact, list(feature_impact[0]) if feature_impact else ["feature_name"])

    scenarios = [scenario("LOW_ONLY", low, rows), scenario("RECOMMENDED_PHASE5E", recommended, rows),
                 scenario("LOW_PLUS_MEDIUM", low | medium, rows), scenario("ALL_RECOMMENDED_LOW_MEDIUM", recommended_low_medium, rows)]
    for policy_id in sorted(high): scenarios.append(scenario(f"LOW_MEDIUM_PLUS_{policy_id}", low | medium | {policy_id}, rows))
    scenarios.append(scenario("THEORETICAL_ALL_CANDIDATES", low | medium | high | very_high, rows))
    write_csv(AUDIT / "phase5d_policy_set_impact.csv", scenarios, ["policy_set_id", "policies", "max_intrusiveness", "rows_fully_unlocked", "new_semantic_groups_estimated", "rows_remaining"])
    write_csv(AUDIT / "phase5d_policy_frontier.csv", scenarios, list(scenarios[0]))

    counterfactual = []
    for row in rows:
        counterfactual.append({"source_identity": row["source_identity"], "strategy_name": row["strategy_name"], "current_status": "REMAINS_UNRESOLVED",
                               "unlockable_with_low_only": full_closure(row, low), "unlockable_with_low_medium": full_closure(row, low|medium),
                               "minimum_policy_set": row["minimum_policy_set"], "minimum_intrusiveness": row["minimum_intrusiveness"],
                               "still_irreducible_reason": row["irreducible_reasons"], "dominant_next_action": row["dominant_next_action"],
                               "semantic_fingerprint": row["semantic_fingerprint"]})
    write_csv(AUDIT / "phase5d_counterfactual_strategy_status.csv", counterfactual, list(counterfactual[0]))

    decision_rows = []
    single_by_id = {row["policy_id"]: row for row in single_rows}
    for policy in POLICIES:
        impact = single_by_id[policy.policy_id]
        decision_rows.append({"policy_id": policy.policy_id, "definition": policy.definition, "intrusiveness": policy.intrusiveness,
                              "rows_fully_unlocked": impact["rows_fully_unlocked_by_policy_alone"], "semantic_groups_unlocked": impact["semantic_groups_estimated"],
                              "requires_new_feature": policy.runtime, "requires_new_data": policy.data, "requires_accounting_change": policy.accounting,
                              "main_risk": policy.rationale, "recommended_action": policy.recommendation})
    write_csv(AUDIT / "phase5d_policy_decision_table.csv", decision_rows, list(decision_rows[0]))

    recommended_rows = [{"policy": row["policy_id"], "reason": row["main_risk"], "intrusiveness": row["intrusiveness"],
                         "estimated_identities_unlocked": row["rows_fully_unlocked"], "estimated_semantic_groups_unlocked": row["semantic_groups_unlocked"],
                         "remaining_risks": "Rows with additional blockers remain unresolved."} for row in decision_rows if row["recommended_action"] == "RECOMMEND_FOR_PHASE5E"]
    optional_rows = [{"policy": row["policy_id"], "reason": row["main_risk"], "intrusiveness": row["intrusiveness"],
                      "estimated_identities_unlocked": row["rows_fully_unlocked"], "estimated_semantic_groups_unlocked": row["semantic_groups_unlocked"],
                      "approval_required": True} for row in decision_rows if row["recommended_action"] == "CONSIDER_WITH_EXPLICIT_USER_APPROVAL"]
    rejected_rows = [{"policy": row["policy_id"], "reason": row["main_risk"], "intrusiveness": row["intrusiveness"],
                      "theoretical_identities_unlocked": row["rows_fully_unlocked"], "recommended_action": "DO_NOT_AUTO_AUTHORIZE"} for row in decision_rows if row["recommended_action"] == "DO_NOT_AUTO_AUTHORIZE"]
    write_csv(AUDIT / "phase5d_phase5e_recommended_policies.csv", recommended_rows, list(recommended_rows[0]))
    write_csv(AUDIT / "phase5d_optional_policy_candidates.csv", optional_rows, list(optional_rows[0]))
    write_csv(AUDIT / "phase5d_not_recommended_policies.csv", rejected_rows, list(rejected_rows[0]))

    dominant = Counter(str(row["dominant_next_action"]) for row in rows)
    dependency_count = Counter()
    for row in rows:
        if row["irreducible"] or any(POLICY_BY_ID[p].family in {"DATA", "ACCOUNTING"} for p in row["minimum_policies"]): dependency_count["irreducible_data_accounting"] += 1
        elif len(row["minimum_policies"]) == 1: dependency_count["one_policy"] += 1
        elif len(row["minimum_policies"]) == 2: dependency_count["two_policies"] += 1
        else: dependency_count["three_plus_policies"] += 1

    scenario_by_id = {str(item["policy_set_id"]): item for item in scenarios}
    validation = {
        "phase": "5D", "starting_identities": 254, "starting_semantic_groups": 177,
        "starting_rows": 989, "audited_rows": len(rows), "missing_rows": 0,
        "dependency_structure": dict(dependency_count), "dominant_next_action": dict(dominant),
        "counterfactual": {
            "current_identities": 254, "current_semantic_groups": 177,
            "low_policy_identities": 254 + int(scenario_by_id["LOW_ONLY"]["rows_fully_unlocked"]),
            "low_policy_semantic_groups": 177 + int(scenario_by_id["LOW_ONLY"]["new_semantic_groups_estimated"]),
            "low_medium_identities": 254 + int(scenario_by_id["LOW_PLUS_MEDIUM"]["rows_fully_unlocked"]),
            "low_medium_semantic_groups": 177 + int(scenario_by_id["LOW_PLUS_MEDIUM"]["new_semantic_groups_estimated"]),
            "theoretical_identities": 254 + int(scenario_by_id["THEORETICAL_ALL_CANDIDATES"]["rows_fully_unlocked"]),
            "theoretical_semantic_groups": 177 + int(scenario_by_id["THEORETICAL_ALL_CANDIDATES"]["new_semantic_groups_estimated"]),
        },
        "performance_metrics_used_for_policy_selection": False, "performance_columns_in_decision_outputs": [],
        "new_strategy_registrations": 0, "new_backtests": 0, "parameter_optimization_runs": 0,
        "semantic_contracts_activated": 0, "phase4_runs": 0, "passed": True,
    }
    assert not set(decision_rows[0]) & PERFORMANCE_COLUMNS
    write_json(AUDIT / "phase5d_validation_summary.json", validation)

    html_rows = "".join(f"<tr><td>{html.escape(str(row['policy_id']))}</td><td>{row['intrusiveness']}</td><td>{row['rows_fully_unlocked']}</td><td>{row['semantic_groups_unlocked']}</td><td>{row['recommended_action']}</td></tr>" for row in decision_rows)
    cf = validation["counterfactual"]
    page = f"""<!doctype html><html><head><meta charset='utf-8'><title>Phase 5D Policy Impact</title>
<style>body{{font:14px system-ui;max-width:1200px;margin:32px auto;color:#17202a}}.cards{{display:flex;gap:16px}}.card{{padding:16px;background:#f3f6f8;border-radius:8px;min-width:180px}}table{{border-collapse:collapse;width:100%;margin-top:18px}}th,td{{border:1px solid #ccd6dd;padding:7px;text-align:left}}th{{background:#eaf0f4}}.bar{{height:18px;background:#2878b5;margin:5px 0}}</style></head><body>
<h1>Phase 5D — Policy Impact Review</h1><p>Performance metrics used for policy selection: <b>false</b></p>
<div class='cards'><div class='card'><b>Current identities</b><br>254</div><div class='card'><b>Semantic groups</b><br>177</div><div class='card'><b>Remaining rows</b><br>989</div></div>
<h2>Counterfactual coverage (not implemented)</h2>
<p>LOW: {cf['low_policy_identities']} identities / {cf['low_policy_semantic_groups']} groups</p><div class='bar' style='width:{int(cf['low_policy_identities'])/12:.1f}%'></div>
<p>LOW+MEDIUM: {cf['low_medium_identities']} identities / {cf['low_medium_semantic_groups']} groups</p><div class='bar' style='width:{int(cf['low_medium_identities'])/12:.1f}%'></div>
<p>Theoretical all candidates: {cf['theoretical_identities']} identities / {cf['theoretical_semantic_groups']} groups</p><div class='bar' style='width:{int(cf['theoretical_identities'])/12:.1f}%'></div>
<h2>Policy decisions</h2><table><tr><th>Policy</th><th>Intrusiveness</th><th>Fully unlocked</th><th>Semantic groups</th><th>Recommendation</th></tr>{html_rows}</table>
<h2>Dominant next actions</h2><pre>{html.escape(json.dumps(dict(dominant), ensure_ascii=False, indent=2))}</pre></body></html>"""
    html_path = AUDIT / "phase5d_policy_impact_review.html"
    temporary = html_path.with_suffix(".html.tmp"); temporary.write_text(page, encoding="utf-8"); os.replace(temporary, html_path)

    deliverable_files = [
        "phase5d_policy_dependency_audit.csv", "phase5d_candidate_policy_registry.csv", "phase5d_timeframe_policy_impact.csv",
        "phase5d_sizing_policy_audit.csv", "phase5d_numeric_default_audit.csv", "phase5d_structural_ambiguity_audit.csv",
        "phase5d_feature_policy_impact.csv", "phase5d_single_policy_impact.csv", "phase5d_policy_set_impact.csv",
        "phase5d_policy_frontier.csv", "phase5d_counterfactual_strategy_status.csv", "phase5d_policy_decision_table.csv",
        "phase5d_phase5e_recommended_policies.csv", "phase5d_optional_policy_candidates.csv",
        "phase5d_not_recommended_policies.csv", "phase5d_policy_impact_review.html", "phase5d_validation_summary.json",
    ]
    DELIVERABLE.mkdir(parents=True, exist_ok=True)
    for name in deliverable_files:
        source = AUDIT / name
        target = DELIVERABLE / name
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_bytes(source.read_bytes()); os.replace(temporary, target)
    return validation


def main() -> int:
    validation = build()
    print(json.dumps(validation, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
