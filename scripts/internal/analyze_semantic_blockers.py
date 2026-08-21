#!/usr/bin/env python3
"""Normalize Phase 2.1 semantic blockers without changing runtime semantics."""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import re
from typing import Iterable


AMBIGUOUS_STATUSES = {
    "AMBIGUOUS_ENTRY_EXIT_LOGIC",
    "AMBIGUOUS_NUMERIC_SEMANTICS",
}
FIELDS = (
    "source_strategy_name",
    "source_indicator_definition",
    "source_long_condition",
    "source_short_condition",
    "source_exit_condition",
)


@dataclass(frozen=True)
class Rule:
    blocker_id: str
    pattern: re.Pattern[str]
    blocker_type: str
    recommended: str
    alternative_1: str
    alternative_2: str
    missing_parameters: str
    confidence: str
    boss: bool
    primitives: str
    note: str = ""


def rule(
    blocker_id: str,
    pattern: str,
    blocker_type: str,
    recommended: str,
    alternative_1: str = "",
    alternative_2: str = "",
    missing_parameters: str = "",
    confidence: str = "medium",
    boss: bool = True,
    primitives: str = "comparison+previous_state",
    note: str = "",
) -> Rule:
    return Rule(
        blocker_id, re.compile(pattern, re.I), blocker_type, recommended,
        alternative_1, alternative_2, missing_parameters, confidence, boss,
        primitives, note,
    )


RULES: tuple[Rule, ...] = (
    rule("STABLE_ABOVE", r"(?:持续)?站稳|稳定站上|连续站上", "CONTRACT_CLEAR_PARAMETER_MISSING",
         "close[t-i] > level[t-i] for every i in 0..N-1",
         "close[t] > level[t]", "minimum(close-level, N bars) > tolerance",
         "N; optional tolerance", "high", True, "comparison+consecutive_state"),
    rule("STABLE_BELOW", r"稳定(?:跌破|处于.*下方)|连续(?:收在|位于).*下方|持续位于.*下方", "CONTRACT_CLEAR_PARAMETER_MISSING",
         "close[t-i] < level[t-i] for every i in 0..N-1",
         "close[t] < level[t]", "maximum(close-level, N bars) < -tolerance",
         "N; optional tolerance", "high", True, "comparison+consecutive_state"),
    rule("STABILIZE_AFTER_DECLINE", r"企稳|止跌", "STRUCTURAL_LOGIC_AMBIGUOUS",
         "after touching the level, require slope(close) to change from <=0 to >0",
         "bullish candle after touch", "N completed closes no lower than the touch low",
         "pivot/touch rule; N", "medium", True, "state_transition+rolling_min+comparison"),
    rule("REJECT_FROM_RESISTANCE", r"承压|滞涨", "STRUCTURAL_LOGIC_AMBIGUOUS",
         "after touching the level, require slope(close) to change from >=0 to <0",
         "bearish candle after touch", "N completed closes no higher than the touch high",
         "pivot/touch rule; N", "medium", True, "state_transition+rolling_max+comparison"),
    rule("TURN_UP", r"拐头向上|向上拐头|拐头上行|(?<!向下)(?<!下行)拐头(?!向下|下行)", "SEMANTIC_TERM_UNDEFINED",
         "x[t-1]-x[t-2] <= 0 and x[t]-x[t-1] > 0",
         "x[t] > x[t-1]", "linear-regression slope over N bars > 0",
         "choose transition versus positive slope; optional N", "medium", True,
         "previous_state+state_transition+slope"),
    rule("TURN_DOWN", r"拐头向下|向下拐头|拐头下行", "SEMANTIC_TERM_UNDEFINED",
         "x[t-1]-x[t-2] >= 0 and x[t]-x[t-1] < 0",
         "x[t] < x[t-1]", "linear-regression slope over N bars < 0",
         "choose transition versus negative slope; optional N", "medium", True,
         "previous_state+state_transition+slope"),
    rule("VOLUME_EXPANSION", r"明显放量|放量", "CONTRACT_CLEAR_PARAMETER_MISSING",
         "volume[t] > k * SMA(volume, N)[t-1]",
         "volume[t] > rolling_quantile(volume, N, q)", "volume[t] > k * volume[t-1]",
         "N; k (or q)", "high", True, "rolling_mean+comparison"),
    rule("VOLUME_CONTRACTION", r"明显缩量|缩量", "CONTRACT_CLEAR_PARAMETER_MISSING",
         "volume[t] < k * SMA(volume, N)[t-1]",
         "volume[t] < rolling_quantile(volume, N, q)", "volume[t] < k * volume[t-1]",
         "N; k (or q)", "high", True, "rolling_mean+comparison"),
    rule("PULLBACK_TO_LEVEL", r"回踩|回测(?:支撑|突破位|均线|通道|VWAP|MA)", "STRUCTURAL_LOGIC_AMBIGUOUS",
         "previously above level; low enters tolerance band; close returns above level",
         "close merely enters tolerance band", "low <= level and close > level",
         "lookback proving prior break; tolerance unit/value", "medium", True,
         "event_state+comparison+rolling_history"),
    rule("REBOUND_TO_LEVEL", r"反弹(?:至|到|触及|回到|靠近)", "STRUCTURAL_LOGIC_AMBIGUOUS",
         "previously below level; high enters tolerance band; close returns below level",
         "close merely enters tolerance band", "high >= level and close < level",
         "lookback proving prior break; tolerance unit/value", "medium", True,
         "event_state+comparison+rolling_history"),
    rule("NEAR_LEVEL", r"附近|接近|靠近", "CONTRACT_CLEAR_PARAMETER_MISSING",
         "abs(price-level) <= tolerance",
         "abs(price-level)/level <= pct", "abs(price-level) <= atr_multiple * ATR",
         "tolerance representation and value", "high", True, "comparison+absolute_distance"),
    rule("VALID_BREAKOUT", r"有效突破|有效跌破|确认突破|确认跌破", "STRUCTURAL_LOGIC_AMBIGUOUS",
         "completed close crosses the level and remains beyond it for N bars",
         "one completed close beyond level", "close beyond level plus volume confirmation",
         "N; tolerance; whether volume is required", "medium", True,
         "cross+consecutive_state+optional_volume_filter"),
    rule("STRONG_BREAKOUT", r"强势突破|强力突破", "CONTRACT_CLEAR_PARAMETER_MISSING",
         "close-level >= k * ATR and close > level",
         "return over breakout bar >= p", "breakout plus volume > k_v*SMA(volume,N)",
         "k or p; optional volume rule", "low", True, "atr+comparison+optional_volume_filter"),
    rule("RECENT_HIGH_WINDOW", r"阶段新高|近期新高|最近新高|前期高点", "NUMERIC_PARAMETER_MISSING",
         "price[t] > max(price[t-N:t]) using completed prior bars",
         "high[t] > max(high[t-N:t])", "close[t] > max(close[t-N:t])",
         "N; price field", "high", True, "rolling_max+cross"),
    rule("RECENT_LOW_WINDOW", r"阶段新低|近期新低|最近新低|前期低点", "NUMERIC_PARAMETER_MISSING",
         "price[t] < min(price[t-N:t]) using completed prior bars",
         "low[t] < min(low[t-N:t])", "close[t] < min(close[t-N:t])",
         "N; price field", "high", True, "rolling_min+cross"),
    rule("CONFLUENCE_COMPOSITION", r"共振|重合(?:支撑|压力|区间)", "STRUCTURAL_LOGIC_AMBIGUOUS",
         "all explicitly named component predicates are true at decision time",
         "at least K of M predicates true", "weighted score >= threshold",
         "AND versus K-of-M; component freshness", "medium", True,
         "boolean_composition+completed_snapshot"),
    rule("SYNCHRONOUS_STATE_TIMING", r"同步", "STRUCTURAL_LOGIC_AMBIGUOUS",
         "all latest completed timeframe predicates are true at decision time",
         "predicates must turn true on the same base bar", "all become true within tolerance duration",
         "latest-state versus same-event; optional tolerance duration", "medium", True,
         "completed_multitimeframe_alignment+boolean_composition"),
    rule("CONFIRMATION_RULE", r"(?:二次|再次)?确认", "STRUCTURAL_LOGIC_AMBIGUOUS",
         "the primary predicate is true on N consecutive completed observations",
         "primary predicate followed by a confirming candle", "primary predicate followed by retest",
         "N and confirmation event type", "low", True, "state_machine+consecutive_state"),
    rule("FALSE_BREAKOUT", r"假突破", "STRUCTURAL_LOGIC_AMBIGUOUS",
         "price crosses level then closes back inside within N completed bars",
         "intrabar break with same-bar close back inside", "breakout fails by tolerance within N bars",
         "N; price field; tolerance", "medium", True, "event_state+cross+time_window"),
    rule("MOMENTUM_STRENGTHEN", r"动能(?:增强|转强)|趋势转强", "SEMANTIC_TERM_UNDEFINED",
         "momentum[t] > momentum[t-1] and momentum[t] > threshold",
         "slope(momentum,N) > 0", "momentum crosses its signal line upward",
         "indicator; threshold; optional N", "low", True, "previous_state+slope+comparison"),
    rule("MOMENTUM_WEAKEN", r"动能(?:减弱|衰减|转弱)|趋势转弱", "SEMANTIC_TERM_UNDEFINED",
         "momentum[t] < momentum[t-1] while retaining its current sign",
         "slope(momentum,N) < 0", "histogram absolute value decreases N bars",
         "indicator; N; sign handling", "low", True, "previous_state+slope+comparison"),
    rule("MOMENTUM_EXHAUSTION", r"动能衰竭|衰竭", "STRUCTURAL_LOGIC_AMBIGUOUS",
         "momentum makes no new extreme while price makes a new extreme over matched pivots",
         "absolute momentum falls for N bars", "momentum crosses a neutral threshold",
         "pivot matching; lookback; N; indicator", "low", True,
         "pivot_state+rolling_extrema+comparison"),
    rule("TREND_STATE_DEFINITION", r"多头区间|空头区间|趋势明显|强趋势|弱趋势|趋势环境", "STRUCTURAL_LOGIC_AMBIGUOUS",
         "use the explicitly named trend indicator and numeric threshold",
         "price above/below trend MA", "positive/negative slope over N bars",
         "indicator/threshold when absent; N for slope", "low", True,
         "comparison+slope+market_regime_state"),
    rule("TREND_REVERSAL_DEFINITION", r"趋势反转|行情反转", "STRUCTURAL_LOGIC_AMBIGUOUS",
         "the strategy's explicit trend predicate changes sign/state",
         "price crosses the trend reference", "trend slope changes sign",
         "which predicate owns trend state", "medium", True, "state_transition+cross"),
    rule("SUPPORT_ZONE_DEFINITION", r"支撑(?:区|位|带|线|区域|止跌|重合)?", "STRUCTURAL_LOGIC_AMBIGUOUS",
         "use the explicitly named level with a defined tolerance band",
         "rolling pivot-low support", "volume-profile support zone",
         "level source; tolerance; pivot rule when unnamed", "low", True,
         "level_provider+distance+rolling_pivot"),
    rule("RESISTANCE_ZONE_DEFINITION", r"压力(?:区|位|带|线|区域|滞涨|重合)?", "STRUCTURAL_LOGIC_AMBIGUOUS",
         "use the explicitly named level with a defined tolerance band",
         "rolling pivot-high resistance", "volume-profile resistance zone",
         "level source; tolerance; pivot rule when unnamed", "low", True,
         "level_provider+distance+rolling_pivot"),
    rule("POSITION_FRACTION_MISSING", r"(?<!减半)减仓|部分止盈|部分平仓", "NUMERIC_PARAMETER_MISSING",
         "target_exposure = current_exposure * (1-fraction)",
         "reduce fixed notional", "reduce fixed quantity",
         "fraction (or notional/quantity)", "high", True, "strategy_module+target_exposure"),
    rule("LAYERED_REDUCTION_SCHEDULE", r"逐层减仓|分层减仓|分批止盈|梯度减仓", "STRUCTURAL_LOGIC_AMBIGUOUS",
         "ordered trigger levels map to explicit target-exposure fractions",
         "equal fraction at each level", "close one unit at each level",
         "trigger levels; fraction per level; reset rule", "medium", True,
         "strategy_module+ordered_state+fill_reconciliation"),
    rule("PYRAMID_ADD_FRACTION", r"逐层加仓|分层加仓|逐档加仓|金字塔加仓|加仓", "NUMERIC_PARAMETER_MISSING",
         "target_exposure increases by explicit fraction at each completed add trigger",
         "equal notional add", "risk-unit-based add",
         "fraction/notional/risk unit; maximum exposure", "medium", True,
         "state_adapter+pyramid_fill_reconciliation"),
    rule("PYRAMID_STEP_DISTANCE", r"逐层加仓|分层加仓|逐档加仓|逐格|网格", "STRUCTURAL_LOGIC_AMBIGUOUS",
         "next add trigger is anchored to the last fill price plus/minus explicit distance",
         "anchor all levels to first fill", "anchor levels to rolling reference price",
         "distance; anchor; reset; maximum layers", "medium", True,
         "state_adapter+fill_price+ordered_state"),
    rule("PERSISTENCE_COUNT_MISSING", r"持续|连续(?!\s*\d)", "NUMERIC_PARAMETER_MISSING",
         "predicate is true for N consecutive completed observations",
         "elapsed clock time >= duration", "current and immediately previous observation true",
         "N or duration", "high", True, "consecutive_state+clock_window"),
    rule("EXTREME_THRESHOLD_MISSING", r"高位|低位|中位|极值|极端|极致|超买区|超卖区", "NUMERIC_PARAMETER_MISSING",
         "indicator compared with an explicit numeric threshold",
         "rolling quantile over N", "z-score threshold",
         "threshold or quantile; N", "medium", True, "comparison+rolling_quantile"),
    rule("NEUTRAL_ZONE_MISSING", r"中性区间|中性区域|回归中性", "NUMERIC_PARAMETER_MISSING",
         "lower <= indicator <= upper",
         "abs(indicator-neutral) <= tolerance", "rolling middle quantile band",
         "lower/upper or tolerance; optional N", "high", True, "comparison+boolean_composition"),
    rule("TOUCH_SEMANTICS", r"触及|碰到|触碰", "SEMANTIC_TERM_UNDEFINED",
         "for price levels: low <= level <= high on the completed bar",
         "close crosses level", "abs(close-level) <= tolerance",
         "price-field rule; tolerance for non-price indicators", "medium", True,
         "ohlc_range_test+cross+distance"),
    rule("MEAN_REVERSION_COMPLETION", r"回归(?!零轴|0轴)", "STRUCTURAL_LOGIC_AMBIGUOUS",
         "value crosses the explicitly named center reference",
         "value enters a tolerance band around reference", "value remains in band N bars",
         "cross versus band; tolerance; N", "medium", True, "cross+distance+consecutive_state"),
    rule("CHANNEL_STATE_DEFINITION", r"通道(?:多头|空头)|通道趋势|通道收敛|通道发散", "STRUCTURAL_LOGIC_AMBIGUOUS",
         "define state from explicit channel slope and price location",
         "upper/lower/middle slopes share sign", "channel width slope determines convergence/divergence",
         "state predicate; N for slope", "low", True, "channel_features+slope+boolean_composition"),
    rule("VOLATILITY_QUANTILE_WINDOW", r"Q\s*\d+|分位", "NUMERIC_PARAMETER_MISSING",
         "rolling_quantile(value, N, q) using completed observations",
         "expanding historical quantile", "fixed calibration-period quantile",
         "N or calibration interval", "high", True, "rolling_quantile"),
    rule("STOP_DISTANCE_MISSING", r"(?<!\d)(?:强制|硬性)?止损", "NUMERIC_PARAMETER_MISSING",
         "exit when adverse move from fill price reaches explicit distance",
         "close-based threshold", "intrabar high/low threshold",
         "distance and unit; close versus intrabar", "medium", True,
         "fill_anchored_price+comparison"),
    rule("ATR_LOOKBACK_MISSING", r"\d+(?:\.\d+)?\s*ATR", "NUMERIC_PARAMETER_MISSING",
         "use canonical ATR(window) with an explicitly confirmed window",
         "Wilder ATR(14)", "strategy-specified ATR window",
         "ATR window", "high", True, "atr_feature"),
    rule("ICHIMOKU_REFERENCE_WINDOW", r"完整位于全部历史|历史\s*K\s*区间|云带.*延迟", "STRUCTURAL_LOGIC_AMBIGUOUS",
         "use standard Ichimoku displacement and compare against a finite aligned reference window",
         "compare only the corresponding displaced candle", "compare against the last N historical candles",
         "reference window and displacement convention", "low", True,
         "completed_bar_alignment+rolling_range+displacement"),
    rule("FRACTAL_SCALE_DEFINITION", r"\d+\s*/\s*\d+\s*(?:周期)?分形|三重(?:周期)?分形", "STRUCTURAL_LOGIC_AMBIGUOUS",
         "define each fractal scale by explicit left/right confirmation widths and composition rule",
         "all scales confirm the same direction", "any scale confirms",
         "left/right widths; AND/OR composition", "low", True,
         "confirmed_fractal+boolean_composition"),
    rule("GRID_LAYER_CONTRACT", r"网格|逐格|逐档", "STRUCTURAL_LOGIC_AMBIGUOUS",
         "finite ordered exposure targets tied to explicit fill-anchored price levels",
         "reference-price-anchored grid", "rolling-center grid",
         "step; layer count; target fractions; anchor/reset", "low", True,
         "state_adapter+pyramid_fill_reconciliation"),
)


DIVERGENCE_RULES: tuple[Rule, ...] = (
    rule("DIVERGENCE_PIVOT_DEFINITION_MISSING", r"背离", "STRUCTURAL_LOGIC_AMBIGUOUS",
         "match two confirmed price pivots with two confirmed indicator pivots",
         "rolling-window extrema without pivots", "slope disagreement over fixed window",
         "pivot left/right width; matching tolerance", "low", True,
         "confirmed_pivot+event_matching"),
    rule("DIVERGENCE_LOOKBACK_MISSING", r"背离", "NUMERIC_PARAMETER_MISSING",
         "both matched pivots must lie within lookback N completed observations",
         "maximum clock duration", "nearest two confirmed pivots",
         "N or duration", "high", True, "rolling_time_window+confirmed_pivot"),
)
DIVERGENCE_TYPE_RULE = rule(
    "DIVERGENCE_TYPE_MISSING", r"背离", "STRUCTURAL_LOGIC_AMBIGUOUS",
    "explicitly select regular/hidden and bullish/bearish divergence",
    "regular divergence only", "regular plus hidden divergence",
    "divergence type and direction", "low", True,
    "enum_contract+confirmed_pivot",
)


FALLBACK_RULE = rule(
    "STRUCTURAL_RULE_UNPARSED", r".+", "STRUCTURAL_LOGIC_AMBIGUOUS",
    "manual decomposition into typed predicates and an explicit state machine",
    "", "", "predicate ownership; event ordering; exit precedence", "low", True,
    "typed_condition_ir+state_machine",
    "No reusable lexical contract safely captured the full rule.",
)
STANDARD_RULE = rule(
    "STANDARD_RULESET_ALREADY_RESOLVABLE", r".+", "STANDARD_SEMANTICS_ALREADY_RESOLVABLE",
    "translate explicit comparisons/crossings/transitions using existing completed-observation operators",
    "", "", "", "high", False,
    "comparison+cross+previous_state+rolling_extrema",
)


RULE_BY_ID = {
    item.blocker_id: item
    for item in (*RULES, *DIVERGENCE_RULES, DIVERGENCE_TYPE_RULE, FALLBACK_RULE, STANDARD_RULE)
}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def write_csv(path: Path, fields: Iterable[str], rows: Iterable[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(fields), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def context_for(text: str, start: int, end: int) -> str:
    nearby = text[max(0, start - 24): min(len(text), end + 36)]
    contexts = []
    for name, pattern in (
        ("moving_average", r"MA\d*|EMA|HMA|均线"),
        ("vwap", r"VWAP"),
        ("channel_or_band", r"布林|BOLL|通道|唐奇安|上轨|下轨"),
        ("breakout_level", r"前高|前低|突破位|分形|新高|新低"),
        ("volume", r"成交量|量能|OBV|MFI|VFI|KVO|CMF"),
        ("momentum", r"MACD|DIF|DEA|RSI|CCI|AO|ROC|TRIX|动能"),
        ("volatility", r"ATR|HV|GV|波动|BBW"),
        ("multi_timeframe", r"多周期|同步|日线|周线|小时|\d+[Hh]"),
        ("position", r"持仓|仓位|加仓|减仓|止盈|止损"),
    ):
        if re.search(pattern, nearby, re.I):
            contexts.append(name)
    return ";".join(contexts) or "generic"


def explicit_fraction_near(text: str, start: int, end: int) -> bool:
    nearby = text[max(0, start - 18): min(len(text), end + 24)]
    return bool(re.search(r"\d+(?:\.\d+)?\s*%|减半|一半|全(?:部|仓|额)", nearby))


def explicit_persistence_near(text: str, start: int, end: int) -> bool:
    nearby = text[max(0, start - 10): min(len(text), end + 16)]
    return bool(re.search(r"(?:连续|持续)\s*[一二三四五六七八九十\d]+\s*(?:根|周期|次|分钟|秒|日)", nearby))


def stop_trigger_is_explicit(text: str, start: int, end: int) -> bool:
    nearby = text[max(0, start - 42): min(len(text), end + 8)]
    return bool(re.search(
        r"\d+(?:\.\d+)?\s*(?:ATR|%|bp|点|倍)|跌破|突破|下穿|上穿|触及|"
        r"浮亏|亏损|止损价|前低|前高|通道|均线|MA|EMA|分形",
        nearby, re.I,
    ))


def extract_row_blockers(row: dict[str, str]) -> list[dict[str, object]]:
    found: list[dict[str, object]] = []
    seen: set[tuple[str, str, int, int]] = set()
    full_text = " ".join(row.get(field, "") for field in FIELDS)
    explicit_atr_window = bool(re.search(
        r"ATR\s*(?:\(|（)?\s*\d+|\d+\s*周期\s*ATR", full_text, re.I,
    ))
    for field in FIELDS:
        text = row.get(field, "")
        if not text:
            continue
        for item in RULES:
            for match in item.pattern.finditer(text):
                if item.blocker_id == "POSITION_FRACTION_MISSING" and explicit_fraction_near(text, *match.span()):
                    continue
                if item.blocker_id == "PERSISTENCE_COUNT_MISSING" and explicit_persistence_near(text, *match.span()):
                    continue
                if item.blocker_id == "STOP_DISTANCE_MISSING" and stop_trigger_is_explicit(text, *match.span()):
                    continue
                if item.blocker_id == "ATR_LOOKBACK_MISSING" and explicit_atr_window:
                    continue
                key = (field, item.blocker_id, *match.span())
                if key in seen:
                    continue
                seen.add(key)
                found.append(relationship(row, field, match.group(0), match.start(), match.end(), item, text))
        for match in re.finditer(r"背离", text):
            for item in DIVERGENCE_RULES:
                key = (field, item.blocker_id, *match.span())
                if key not in seen:
                    seen.add(key)
                    found.append(relationship(row, field, match.group(0), match.start(), match.end(), item, text))
            nearby = text[max(0, match.start() - 6): min(len(text), match.end() + 6)]
            if not re.search(r"顶背离|底背离|看涨背离|看跌背离|多头背离|空头背离", nearby):
                found.append(relationship(
                    row, field, match.group(0), match.start(), match.end(),
                    DIVERGENCE_TYPE_RULE, text,
                ))
    # De-duplicate TURN_UP generic "拐头" when a directional phrase also matched.
    if any(item["normalized_blocker_id"] == "TURN_DOWN" for item in found):
        found = [item for item in found if not (
            item["normalized_blocker_id"] == "TURN_UP" and "向下" in item["original_phrase"]
        )]
    return found


def relationship(
    row: dict[str, str], field: str, phrase: str, start: int, end: int,
    item: Rule, text: str,
) -> dict[str, object]:
    return {
        "source_identity": row["registry_id"],
        "strategy_name": row["source_strategy_name"],
        "source_sheet": row["source_sheet"],
        "strategy_number": row["source_strategy_number"],
        "field": field.removeprefix("source_"),
        "original_phrase": phrase,
        "normalized_blocker_id": item.blocker_id,
        "blocker_type": item.blocker_type,
        "standard_semantics_resolvable": item.blocker_type == "STANDARD_SEMANTICS_ALREADY_RESOLVABLE",
        "numeric_parameter_missing": item.blocker_type in {
            "NUMERIC_PARAMETER_MISSING", "CONTRACT_CLEAR_PARAMETER_MISSING",
        },
        "requires_human_definition": item.boss,
        "context": context_for(text, start, end),
        "notes": item.note,
    }


QUALITATIVE_REMAINDER = re.compile(
    r"站稳|企稳|止跌|承压|滞涨|放量|缩量|回踩|附近|有效|强势|阶段|背离|共振|同步|"
    r"拐头|衰竭|衰减|转强|转弱|假突破|确认|减仓|加仓|网格|逐层|分层|高位|低位|"
    r"中位|极值|支撑|压力|反弹|触及|趋势反转|多头区间|空头区间"
)
DETERMINISTIC_PREDICATE = re.compile(
    r"上穿|下穿|由负转正|由正转负|[＞＜><]=?\s*[+\-]?\d|\d+\s*周期(?:新高|新低|最高|最低)|"
    r"突破\s*\d+\s*周期|跌破\s*\d+\s*周期|收阳|收阴|位于.*(?:上方|下方)|"
    r"零轴|反向交叉|全部(?:平仓|清仓|离场|止盈)"
)
SUPPORTED_FEATURE_TERMS = re.compile(
    r"MA|EMA|HMA|CCI|ADX|DI|AO|AROON|PSAR|SAR|RSI|MACD|DIF|DEA|ATR|"
    r"布林|BOLL|唐奇安|分形|SuperTrend|ST|均线|价格|收盘价|最高价|最低价|成交量|ROC",
    re.I,
)
UNSUPPORTED_FEATURE_TERMS = re.compile(
    r"POC|订单流|筹码|市场宽度|TRIN|NH\s*/\s*NL|期权|链上|FVG|KVO|VFI|VIDYA|"
    r"WVF|PSY|COG|VRS|ADOSC|FI\b|MFI|OBV|CMF|VWAD|资金流向"
)


def standard_resolvable(row: dict[str, str], blockers: list[dict[str, object]]) -> bool:
    if blockers:
        return False
    rules_text = " ".join(row.get(field, "") for field in FIELDS[2:])
    full_text = " ".join(row.get(field, "") for field in FIELDS)
    if QUALITATIVE_REMAINDER.search(rules_text) or UNSUPPORTED_FEATURE_TERMS.search(full_text):
        return False
    if not SUPPORTED_FEATURE_TERMS.search(full_text):
        return False
    for field in FIELDS[2:]:
        text = row.get(field, "").strip()
        if text and text not in {"-", "—", "/", "无开空规则", "不做空", "不做多"}:
            if not DETERMINISTIC_PREDICATE.search(text):
                return False
    return True


def add_synthetic_relationship(row: dict[str, str], item: Rule) -> dict[str, object]:
    text = " | ".join(row.get(field, "") for field in FIELDS[2:] if row.get(field, ""))
    return relationship(row, "source_strategy_name", text[:160], 0, min(1, len(text)), item, text)


def consolidate_row_relationships(items: list[dict[str, object]]) -> list[dict[str, object]]:
    """Return exactly one relationship per strategy and normalized blocker."""
    grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
    for item in items:
        grouped[str(item["normalized_blocker_id"])].append(item)
    result = []
    for blocker_id, group in sorted(grouped.items()):
        merged = dict(group[0])
        merged["field"] = ";".join(sorted({str(item["field"]) for item in group}))
        merged["original_phrase"] = "; ".join(dict.fromkeys(
            str(item["original_phrase"]) for item in group
        ))
        merged["context"] = ";".join(sorted({
            part for item in group for part in str(item["context"]).split(";") if part
        }))
        result.append(merged)
    return result


def strategy_blocker_sets(relationships: list[dict[str, object]]) -> dict[str, set[str]]:
    result: dict[str, set[str]] = defaultdict(set)
    for item in relationships:
        result[str(item["source_identity"])].add(str(item["normalized_blocker_id"]))
    return dict(result)


def blocker_frequency(
    ambiguous: list[dict[str, str]], relationships: list[dict[str, object]],
) -> list[dict[str, object]]:
    by_id: dict[str, list[dict[str, object]]] = defaultdict(list)
    for item in relationships:
        by_id[str(item["normalized_blocker_id"])].append(item)
    sets = strategy_blocker_sets(relationships)
    source = {row["registry_id"]: row for row in ambiguous}
    rows = []
    for blocker_id, items in by_id.items():
        ids = {str(item["source_identity"]) for item in items}
        phrases = Counter(str(item["original_phrase"]) for item in items)
        sole = sum(sets[identity] == {blocker_id} for identity in ids)
        frame_counts = Counter(source[identity]["source_timeframe_semantics"] for identity in ids)
        rows.append({
            "blocker_id": blocker_id,
            "affected_strategy_count": len(ids),
            "sole_blocker_count": sole,
            "co_blocked_count": len(ids) - sole,
            "sheet1_count": sum(source[identity]["source_sheet_index"] == "1" for identity in ids),
            "sheet2_count": sum(source[identity]["source_sheet_index"] == "2" for identity in ids),
            "standalone_count": len(ids),
            "module_count": 0,
            "timeframe_distribution": json.dumps(frame_counts, ensure_ascii=False, sort_keys=True),
            "top_original_phrases": "; ".join(value for value, _ in phrases.most_common(8)),
        })
    rows.sort(key=lambda row: (-int(row["sole_blocker_count"]), -int(row["affected_strategy_count"]), str(row["blocker_id"])))
    return rows


def cooccurrence(sets: dict[str, set[str]]) -> list[dict[str, object]]:
    counts: Counter[tuple[str, str]] = Counter()
    for blockers in sets.values():
        ordered = sorted(blockers)
        for index, left in enumerate(ordered):
            for right in ordered[index + 1:]:
                counts[(left, right)] += 1
    return [
        {"blocker_a": pair[0], "blocker_b": pair[1], "strategy_count": count}
        for pair, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    ]


def greedy_contract_priority(
    frequency: list[dict[str, object]], sets: dict[str, set[str]], auto_ids: set[str],
) -> list[dict[str, object]]:
    candidates = {
        str(row["blocker_id"]) for row in frequency
        if str(row["blocker_id"]) not in {
            "STANDARD_RULESET_ALREADY_RESOLVABLE", "STRUCTURAL_RULE_UNPARSED",
        }
    }
    enabled = {"STANDARD_RULESET_ALREADY_RESOLVABLE"}
    unlocked = {identity for identity, blockers in sets.items() if blockers <= enabled}
    ranking = []
    while candidates:
        best = None
        for contract in sorted(candidates):
            trial = enabled | {contract}
            trial_unlocked = {identity for identity, blockers in sets.items() if blockers <= trial}
            immediate = len(trial_unlocked - unlocked)
            affected = sum(contract in blockers for blockers in sets.values())
            score = immediate * 10000 + affected
            item = (score, immediate, affected, contract, trial_unlocked)
            if best is None or item[:4] > best[:4]:
                best = item
        assert best is not None
        _, immediate, affected, contract, trial_unlocked = best
        enabled.add(contract); candidates.remove(contract); unlocked = trial_unlocked
        freq = next(row for row in frequency if row["blocker_id"] == contract)
        ranking.append({
            "priority": len(ranking) + 1,
            "contract_id": contract,
            "affected_count": affected,
            "sole_blocker_count": freq["sole_blocker_count"],
            "expected_immediate_unlock": immediate,
            "expected_unlock_if_top_related_contracts_also_defined": len(unlocked - auto_ids),
            "impact_score": immediate * 10000 + affected,
            "cumulative_projected_unlock": len(unlocked),
        })
    return ranking


def unlock_projection(
    ranking: list[dict[str, object]], sets: dict[str, set[str]], current_executable: int,
) -> list[dict[str, object]]:
    auto_contract = "STANDARD_RULESET_ALREADY_RESOLVABLE"
    scenarios = [
        ("AUTO_RECOVERABLE_ONLY", 0), ("TOP_1_CONTRACT", 1),
        ("TOP_3_CONTRACTS", 3), ("TOP_5_CONTRACTS", 5),
        ("TOP_10_CONTRACTS", 10), ("TOP_20_CONTRACTS", 20),
    ]
    result = []
    for name, count in scenarios:
        contracts = {auto_contract} | {str(row["contract_id"]) for row in ranking[:count]}
        unlocked = {identity for identity, blockers in sets.items() if blockers <= contracts}
        result.append({
            "scenario": name,
            "contracts_enabled": ";".join(sorted(contracts)),
            "newly_unlockable_strategy_count": len(unlocked),
            "projected_total_executable_strategy_count": current_executable + len(unlocked),
            "remaining_ambiguous_count": len(sets) - len(unlocked),
        })
    high = {
        blocker_id for blocker_id, item in RULE_BY_ID.items()
        if item.confidence == "high" and not item.boss
    } | {auto_contract}
    unlocked = {identity for identity, blockers in sets.items() if blockers <= high}
    result.append({
        "scenario": "ALL_PROPOSED_HIGH_CONFIDENCE_CONTRACTS",
        "contracts_enabled": ";".join(sorted(high)),
        "newly_unlockable_strategy_count": len(unlocked),
        "projected_total_executable_strategy_count": current_executable + len(unlocked),
        "remaining_ambiguous_count": len(sets) - len(unlocked),
    })
    return result


def build_contract_rows(
    frequency: list[dict[str, object]], priority: list[dict[str, object]],
) -> list[dict[str, object]]:
    rank = {str(row["contract_id"]): row for row in priority}
    rows = []
    for freq in frequency:
        blocker_id = str(freq["blocker_id"])
        item = RULE_BY_ID.get(blocker_id, FALLBACK_RULE)
        priority_row = rank.get(blocker_id, {})
        rows.append({
            "contract_id": blocker_id,
            "blocker_id": blocker_id,
            "recommended_definition": item.recommended,
            "alternative_definition_1": item.alternative_1,
            "alternative_definition_2": item.alternative_2,
            "missing_parameters": item.missing_parameters,
            "affected_strategy_count": freq["affected_strategy_count"],
            "sole_blocker_count": freq["sole_blocker_count"],
            "expected_immediate_unlock": freq["sole_blocker_count"],
            "semantic_confidence": item.confidence,
            "requires_boss_confirmation": item.boss,
            "implementation_primitives_required": item.primitives,
        })
    rows.sort(key=lambda row: rank.get(str(row["contract_id"]), {"priority": 9999})["priority"])
    return rows


def boss_review_rows(
    contracts: list[dict[str, object]], frequency: list[dict[str, object]],
    priority: list[dict[str, object]], limit: int = 40,
) -> list[dict[str, object]]:
    freq = {str(row["blocker_id"]): row for row in frequency}
    contract = {str(row["contract_id"]): row for row in contracts}
    rows = []
    for item in priority:
        blocker_id = str(item["contract_id"])
        proposal = contract[blocker_id]
        if not proposal["requires_boss_confirmation"]:
            continue
        rows.append({
            "priority": len(rows) + 1,
            "contract_id": blocker_id,
            "Chinese_term_examples": freq[blocker_id]["top_original_phrases"],
            "recommended_machine_definition": proposal["recommended_definition"],
            "alternatives": "; ".join(filter(None, [
                str(proposal["alternative_definition_1"]), str(proposal["alternative_definition_2"]),
            ])),
            "parameters_to_confirm": proposal["missing_parameters"],
            "affected_count": proposal["affected_strategy_count"],
            "immediate_unlock_count": proposal["sole_blocker_count"],
            "marginal_unlock_with_higher_priority_contracts": item["expected_immediate_unlock"],
            "cumulative_projected_unlock": item["cumulative_projected_unlock"],
        })
        if len(rows) >= limit:
            break
    return rows


def write_questions(path: Path, rows: list[dict[str, object]], contracts: dict[str, Rule], limit: int = 20) -> None:
    lines = ["# Phase 2.2A — 最小高影响语义问题集", "", "仅供确认；本阶段未应用任何定义。", ""]
    for index, row in enumerate(rows[:limit], 1):
        item = contracts[str(row["contract_id"])]
        lines.extend([
            f"## Q{index} — {row['contract_id']}", "",
            f"影响策略：{row['affected_count']}",
            f"仅确认本合同可即时解锁：{row['immediate_unlock_count']}",
            f"若更高优先级合同也确认，边际解锁：{row['marginal_unlock_with_higher_priority_contracts']}", "",
            f"常见原文：{row['Chinese_term_examples']}", "",
            "推荐：", f"`{item.recommended}`", "",
        ])
        if item.alternative_1:
            lines.extend(["备选 A：", f"`{item.alternative_1}`", ""])
        if item.alternative_2:
            lines.extend(["备选 B：", f"`{item.alternative_2}`", ""])
        if item.missing_parameters:
            lines.extend([f"需要确认的参数：`{item.missing_parameters}`", ""])
        lines.extend(["- [ ] 推荐", "- [ ] 备选 A", "- [ ] 备选 B", "- [ ] 其他", ""])
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text("\n".join(lines), encoding="utf-8")
    temporary.replace(path)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--current-executable", type=int, default=34)
    args = parser.parse_args()

    all_rows = read_csv(args.manifest)
    ambiguous = [row for row in all_rows if row["phase2_1_status"] in AMBIGUOUS_STATUSES]
    relationships: list[dict[str, object]] = []
    auto_rows: list[dict[str, object]] = []
    for row in ambiguous:
        blockers = consolidate_row_relationships(extract_row_blockers(row))
        if standard_resolvable(row, blockers):
            blockers = [add_synthetic_relationship(row, STANDARD_RULE)]
            auto_rows.append({
                "source_identity": row["registry_id"],
                "strategy_name": row["source_strategy_name"],
                "current_blocker": row["phase2_1_status"],
                "resolved_standard_definition": STANDARD_RULE.recommended,
                "why_unambiguous": "all entry/exit clauses use explicit comparisons, crossings, sign transitions, or explicit N-window extrema",
                "required_existing_operator": STANDARD_RULE.primitives,
                "projected_status": "AUTO_RECOVERABLE_STANDARD_SEMANTICS",
            })
        elif not blockers:
            blockers = [add_synthetic_relationship(row, FALLBACK_RULE)]
        relationships.extend(blockers)

    sets = strategy_blocker_sets(relationships)
    frequency = blocker_frequency(ambiguous, relationships)
    auto_ids = {row["source_identity"] for row in auto_rows}
    priority = greedy_contract_priority(frequency, sets, auto_ids)
    projections = unlock_projection(priority, sets, args.current_executable)
    contracts = build_contract_rows(frequency, priority)
    boss_rows = boss_review_rows(contracts, frequency, priority)

    output = args.output_dir
    output.mkdir(parents=True, exist_ok=True)
    manifest_fields = (
        "source_identity", "strategy_name", "source_sheet", "strategy_number", "field",
        "original_phrase", "normalized_blocker_id", "blocker_type",
        "standard_semantics_resolvable", "numeric_parameter_missing",
        "requires_human_definition", "context", "notes",
    )
    write_csv(output / "semantic_blocker_manifest.csv", manifest_fields, relationships)
    write_csv(output / "semantic_blocker_frequency.csv", (
        "blocker_id", "affected_strategy_count", "sole_blocker_count", "co_blocked_count",
        "sheet1_count", "sheet2_count", "standalone_count", "module_count",
        "timeframe_distribution", "top_original_phrases",
    ), frequency)
    write_csv(output / "semantic_blocker_cooccurrence.csv", (
        "blocker_a", "blocker_b", "strategy_count",
    ), cooccurrence(sets))
    write_csv(output / "semantic_contract_proposals.csv", (
        "contract_id", "blocker_id", "recommended_definition", "alternative_definition_1",
        "alternative_definition_2", "missing_parameters", "affected_strategy_count",
        "sole_blocker_count", "expected_immediate_unlock", "semantic_confidence",
        "requires_boss_confirmation", "implementation_primitives_required",
    ), contracts)
    write_csv(output / "semantic_auto_recoverable.csv", (
        "source_identity", "strategy_name", "current_blocker", "resolved_standard_definition",
        "why_unambiguous", "required_existing_operator", "projected_status",
    ), auto_rows)
    write_csv(output / "semantic_contract_priority.csv", (
        "priority", "contract_id", "affected_count", "sole_blocker_count",
        "expected_immediate_unlock", "expected_unlock_if_top_related_contracts_also_defined",
        "impact_score", "cumulative_projected_unlock",
    ), priority)
    write_csv(output / "semantic_unlock_projection.csv", (
        "scenario", "contracts_enabled", "newly_unlockable_strategy_count",
        "projected_total_executable_strategy_count", "remaining_ambiguous_count",
    ), projections)
    write_csv(output / "semantic_boss_review.csv", (
        "priority", "contract_id", "Chinese_term_examples", "recommended_machine_definition",
        "alternatives", "parameters_to_confirm", "affected_count", "immediate_unlock_count",
        "marginal_unlock_with_higher_priority_contracts", "cumulative_projected_unlock",
    ), boss_rows)
    write_questions(output / "semantic_minimal_questions.md", boss_rows, RULE_BY_ID)

    blocker_types = Counter(
        str(item["blocker_type"]) for item in relationships
    )
    strategy_types: dict[str, set[str]] = defaultdict(set)
    for item in relationships:
        strategy_types[str(item["source_identity"])].add(str(item["blocker_type"]))
    validations = {
        "ambiguous_expected": 1196,
        "ambiguous_analyzed": len(ambiguous),
        "ambiguous_with_blocker": len(sets),
        "every_ambiguous_has_blocker": len(sets) == len(ambiguous),
        "implemented_rows_in_analysis": sum(row["phase2_1_status"] == "IMPLEMENTED_STANDALONE" for row in ambiguous),
        "session_rows_in_analysis": sum(row["phase2_1_status"] == "ECONOMIC_SESSION_DEFINITION_REQUIRED" for row in ambiguous),
        "missing_data_rows_in_analysis": sum(row["phase2_1_status"] in {"MISSING_SOURCE_DATA", "UNAVAILABLE_EXTERNAL_UNIVERSE"} for row in ambiguous),
        "unique_strategy_ids": len({row["registry_id"] for row in ambiguous}),
        "manifest_relationship_rows": len(relationships),
        "set_union_projection_valid": all(
            int(row["newly_unlockable_strategy_count"]) <= len(ambiguous) for row in projections
        ),
        "all_contracts_grounded": all(int(row["affected_strategy_count"]) > 0 for row in contracts),
        "auto_recoverable_all_deterministic": all(
            sets[row["source_identity"]] == {"STANDARD_RULESET_ALREADY_RESOLVABLE"}
            for row in auto_rows
        ),
    }
    validations["passed"] = (
        validations["ambiguous_analyzed"] == validations["ambiguous_expected"]
        and validations["every_ambiguous_has_blocker"]
        and validations["implemented_rows_in_analysis"] == 0
        and validations["session_rows_in_analysis"] == 0
        and validations["missing_data_rows_in_analysis"] == 0
        and validations["set_union_projection_valid"]
        and validations["all_contracts_grounded"]
        and validations["auto_recoverable_all_deterministic"]
    )
    summary = {
        "source_manifest": str(args.manifest.resolve()),
        "source_manifest_sha256": file_sha256(args.manifest),
        "ambiguous_strategy_count": len(ambiguous),
        "status_distribution": dict(Counter(row["phase2_1_status"] for row in ambiguous)),
        "unique_normalized_blocker_families": len(frequency),
        "auto_recoverable_strategy_count": len(auto_rows),
        "relationship_count": len(relationships),
        "blocker_type_relationship_counts": dict(sorted(blocker_types.items())),
        "strategies_with_numeric_parameter_blocker": sum(
            bool(types & {"NUMERIC_PARAMETER_MISSING", "CONTRACT_CLEAR_PARAMETER_MISSING"})
            for types in strategy_types.values()
        ),
        "strategies_with_deep_structural_blocker": sum(
            "STRUCTURAL_LOGIC_AMBIGUOUS" in types for types in strategy_types.values()
        ),
        "high_confidence_no_boss_contract_count": sum(
            row["semantic_confidence"] == "high" and not row["requires_boss_confirmation"]
            for row in contracts
        ),
        "boss_confirmation_contract_count": sum(bool(row["requires_boss_confirmation"]) for row in contracts),
        "numeric_parameter_contract_count": sum(
            RULE_BY_ID.get(str(row["contract_id"]), FALLBACK_RULE).blocker_type
            in {"NUMERIC_PARAMETER_MISSING", "CONTRACT_CLEAR_PARAMETER_MISSING"}
            for row in contracts
        ),
        "deep_structural_contract_count": sum(
            RULE_BY_ID.get(str(row["contract_id"]), FALLBACK_RULE).blocker_type
            == "STRUCTURAL_LOGIC_AMBIGUOUS" for row in contracts
        ),
        "top_20": frequency[:20],
        "unlock_projection": projections,
        "validations": validations,
        "runtime_changes_performed": False,
        "new_backtests_executed": 0,
        "parameter_optimization_executed": 0,
    }
    temporary = output / "semantic_analysis_summary.json.tmp"
    temporary.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(output / "semantic_analysis_summary.json")
    print(json.dumps({
        "ambiguous": len(ambiguous), "families": len(frequency),
        "auto_recoverable": len(auto_rows), "validations_passed": validations["passed"],
    }, ensure_ascii=False))
    return 0 if validations["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
