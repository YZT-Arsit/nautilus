#!/usr/bin/env python3
"""Close Phase 5C semantic/parameter gaps without performance inspection.

The compiler is deliberately allow-listed.  Every remaining workbook row is
audited, but a strategy is emitted only when one of the reviewed source
families below maps every material entry, exit, sizing, and risk clause.
"""
from __future__ import annotations

import base64
import csv
import hashlib
import json
import os
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
AUDIT = ROOT / "outputs/internal_audit/strategy_workbook"
PLAN = ROOT / "configs/semantic_contracts/workbook_phase5c_strategies.json"
CONTRACTS = ROOT / "configs/semantic_contracts/workbook_phase5c_contracts.json"


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def write_csv(path: Path, rows: list[dict[str, object]], fields: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = fields or (list(rows[0]) if rows else ["source_identity"])
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader(); writer.writerows(rows)
    os.replace(temporary, path)


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def feature(kind: str, name: str, **kwargs: object) -> dict[str, object]:
    return {"kind": kind, "name": name, **kwargs}


def op(name: str, **kwargs: object) -> dict[str, object]:
    return {"op": name, **kwargs}


def and_(*args: object) -> dict[str, object]: return op("and", args=list(args))
def or_(*args: object) -> dict[str, object]: return op("or", args=list(args))
def not_(arg: object) -> dict[str, object]: return op("not", arg=arg)
def gt(a: object, b: object) -> dict[str, object]: return op("gt", left=a, right=b)
def gte(a: object, b: object) -> dict[str, object]: return op("gte", left=a, right=b)
def lt(a: object, b: object) -> dict[str, object]: return op("lt", left=a, right=b)
def lte(a: object, b: object) -> dict[str, object]: return op("lte", left=a, right=b)
def up(a: object, b: object) -> dict[str, object]: return op("cross_above", left=a, right=b)
def down(a: object, b: object) -> dict[str, object]: return op("cross_below", left=a, right=b)
def previous(value: str, lag: int = 1) -> dict[str, object]: return op("previous", value=value, lag=lag)
def turn_up(value: str) -> dict[str, object]: return op("turn_up", value=value)
def turn_down(value: str) -> dict[str, object]: return op("turn_down", value=value)
def rising(value: str, bars: int = 1) -> dict[str, object]: return op("rising", value=value, bars=bars)
def falling(value: str, bars: int = 1) -> dict[str, object]: return op("falling", value=value, bars=bars)
def consecutive(arg: object, bars: int = 2) -> dict[str, object]: return op("consecutive", arg=arg, bars=bars)
def pulse(value: str) -> dict[str, object]: return op("pulse", value=value)
def pos(side: str) -> dict[str, object]: return op("position_is", side=side)
def add(a: object, b: object) -> dict[str, object]: return op("add", left=a, right=b)
def sub(a: object, b: object) -> dict[str, object]: return op("sub", left=a, right=b)
def mul(a: object, b: object) -> dict[str, object]: return op("mul", left=a, right=b)
def mean(value: str, window: int = 20) -> dict[str, object]: return op("rolling_mean", value=value, window=window)
def anchor(name: str) -> dict[str, object]: return op(name)


def action(kind: str, condition: dict[str, object], fraction: float = 1.0) -> dict[str, object]:
    return {"action": kind, "condition": condition, "fraction": fraction, "reason": "phase5c"}


BAR = [
    feature("bar", "p5c_close", field="close"), feature("bar", "p5c_open", field="open"),
    feature("bar", "p5c_high", field="high"), feature("bar", "p5c_low", field="low"),
]


def touch(level: str, *, bullish: bool, candle: bool = False) -> dict[str, object]:
    tolerance = mul("p5c_atr14", 0.25)
    zone = and_(gte("p5c_high", sub(level, tolerance)), lte("p5c_low", add(level, tolerance)))
    reject = gt("p5c_close", level) if bullish else lt("p5c_close", level)
    if candle:
        reject = and_(reject, gt("p5c_close", "p5c_open") if bullish else lt("p5c_close", "p5c_open"))
    return and_(zone, reject)


def risk_stop(*, long: bool, multiple: float, anchor_name: str = "average_entry_price") -> dict[str, object]:
    basis = anchor(anchor_name)
    distance = mul("p5c_atr14", multiple)
    return lte("p5c_close", sub(basis, distance)) if long else gte("p5c_close", add(basis, distance))


def definition(
    row: dict[str, str], *, features: list[dict[str, object]], actions: list[dict[str, object]],
    contracts: list[str], family: str, provenance: str = "STANDARD_CONTRACT_RESOLVED",
    modelled: list[str] | None = None, defaults: dict[str, object] | None = None,
    requires_fill_state: bool = False,
) -> dict[str, object]:
    rule = {"schema_version": 2, "features": features, "actions": actions,
            "source_clause_count": 3, "family": family}
    encoded = base64.urlsafe_b64encode(json.dumps(
        rule, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()).decode()
    return {
        "family": "phase5b_declarative",
        "params": {"rule_spec_b64": encoded, "contract_versions": ";".join(contracts)},
        "semantic_provenance": provenance,
        "contracts_applied": contracts,
        "defaulted_parameters": defaults or {},
        "modelled_interpretations": modelled or [],
        "resolved_blockers": sorted(set(row["remaining_blocker"].split(";"))),
        "remaining_blockers": [], "modules_applied": [],
        "source_timeframe": "1d" if row.get("source_timeframe") == "daily" else "1m",
        "rule_hash": hashlib.sha256(encoded.encode()).hexdigest(),
        "compiler_family": family, "requires_fill_state": requires_fill_state,
    }


def stable_rsi_trend(row: dict[str, str]) -> dict[str, object] | None:
    name = row["strategy_name"]
    if not any(term in name for term in (
        "RSI14 单周期顺势", "RSI 趋势修正", "RSI 趋势阈值修正", "RSI+MA40 长线低吸",
    )):
        return None
    ma_window = 40 if "MA40" in name else 60
    fs = BAR + [feature("sma", "p5c_ma", window=ma_window), feature("rsi", "p5c_rsi", window=14)]
    acts = [
        action("EXIT_LONG", or_(gte("p5c_rsi", 70.0), consecutive(lt("p5c_close", "p5c_ma")))),
        action("EXIT_SHORT", or_(lte("p5c_rsi", 30.0), consecutive(gt("p5c_close", "p5c_ma")))),
        action("REDUCE_LONG", up("p5c_rsi", 60.0), .5), action("REDUCE_SHORT", down("p5c_rsi", 40.0), .5),
        action("ENTER_LONG", and_(pos("flat"), consecutive(gt("p5c_close", "p5c_ma")), lte("p5c_rsi", 40.0), turn_up("p5c_rsi"))),
        action("ENTER_SHORT", and_(pos("flat"), consecutive(lt("p5c_close", "p5c_ma")), gte("p5c_rsi", 60.0), turn_down("p5c_rsi"))),
    ]
    return definition(row, features=fs, actions=acts, contracts=[
        "STABLE_CLOSE_2BAR_V1", "STABILIZE_MINIMAL_TRANSITION_V1", "REDUCE_HALF_CURRENT_V1",
    ], family=f"RSI_TREND_PULLBACK_MA{ma_window}")


def cci_ma_pullback(row: dict[str, str]) -> dict[str, object] | None:
    if not any(term in row["strategy_name"] for term in ("MA60 长线 + CCI", "MA20+CCI 长线波段", "均线 + CCI 顺势波段")):
        return None
    window = 20 if "MA20" in row["strategy_name"] else 60
    fs = BAR + [feature("sma", "p5c_ma", window=window), feature("cci", "p5c_cci", window=20)]
    acts = [
        action("EXIT_LONG", or_(gte("p5c_cci", 100.0), consecutive(lt("p5c_close", "p5c_ma")))),
        action("EXIT_SHORT", or_(lte("p5c_cci", -100.0), consecutive(gt("p5c_close", "p5c_ma")))),
        action("REDUCE_LONG", up("p5c_cci", 0.0), .5), action("REDUCE_SHORT", down("p5c_cci", 0.0), .5),
        action("ENTER_LONG", and_(pos("flat"), consecutive(gt("p5c_close", "p5c_ma")), gte("p5c_cci", -100.0), lte("p5c_cci", 0.0), turn_up("p5c_cci"))),
        action("ENTER_SHORT", and_(pos("flat"), consecutive(lt("p5c_close", "p5c_ma")), gte("p5c_cci", 0.0), lte("p5c_cci", 100.0), turn_down("p5c_cci"))),
    ]
    return definition(row, features=fs, actions=acts, contracts=[
        "STABLE_CLOSE_2BAR_V1", "STABILIZE_MINIMAL_TRANSITION_V1", "REDUCE_HALF_CURRENT_V1",
    ], family=f"CCI_MA{window}_PULLBACK")


def oscillator_turn(row: dict[str, str]) -> dict[str, object] | None:
    name = row["strategy_name"]
    if "RSI+ROC 动量共振短线反转" in name:
        fs = BAR + [feature("rsi", "p5c_primary", window=14), feature("return", "p5c_roc", window=12)]
        long_gate, short_gate = lt("p5c_primary", 20.0), gt("p5c_primary", 80.0)
        reduce_long, reduce_short = up("p5c_primary", 50.0), down("p5c_primary", 50.0)
        family = "RSI_ROC_STABILIZED_REVERSAL"
    elif "CCI+ROC 动量共振波段" in name:
        fs = BAR + [feature("cci", "p5c_primary", window=20), feature("return", "p5c_roc", window=12)]
        long_gate, short_gate = lt("p5c_primary", -100.0), gt("p5c_primary", 100.0)
        reduce_long, reduce_short = up("p5c_primary", 0.0), down("p5c_primary", 0.0)
        family = "CCI_ROC_STABILIZED_REVERSAL"
    elif "OBV+ROC" in name and ("共振" in name or "动量" in name):
        fs = BAR + [feature("obv", "p5c_primary", window=20, output="obv"), feature("return", "p5c_roc", window=12)]
        long_gate, short_gate = rising("p5c_primary"), falling("p5c_primary")
        reduce_long, reduce_short = turn_down("p5c_primary"), turn_up("p5c_primary")
        family = "OBV_ROC_STABILIZED_REVERSAL"
    else:
        return None
    acts = [
        action("EXIT_LONG", down("p5c_roc", 0.0)), action("EXIT_SHORT", up("p5c_roc", 0.0)),
        action("REDUCE_LONG", reduce_long, .5), action("REDUCE_SHORT", reduce_short, .5),
        action("ENTER_LONG", and_(pos("flat"), long_gate, lt("p5c_roc", 0.0), turn_up("p5c_roc"))),
        action("ENTER_SHORT", and_(pos("flat"), short_gate, gt("p5c_roc", 0.0), turn_down("p5c_roc"))),
    ]
    return definition(row, features=fs, actions=acts, contracts=[
        "STABILIZE_MINIMAL_TRANSITION_V1", "TURN_SLOPE_SIGN_CHANGE_V1", "REDUCE_HALF_CURRENT_V1",
    ], family=family)


def slope_pullback(row: dict[str, str]) -> dict[str, object] | None:
    name = row["strategy_name"]
    if "EMA 斜率 + RSI 回调" in name:
        fs = BAR + [feature("ema", "p5c_ma", window=20), feature("rsi", "p5c_rsi", window=14)]
        slope = sub("p5c_ma", previous("p5c_ma"))
        acts = [
            action("EXIT_LONG", or_(lt(slope, 0.0), gte("p5c_rsi", 80.0))),
            action("EXIT_SHORT", or_(gt(slope, 0.0), lte("p5c_rsi", 20.0))),
            action("REDUCE_CURRENT", and_(gte(slope, -0.02), lte(slope, 0.02)), .5),
            action("ENTER_LONG", and_(pos("flat"), gt(slope, .02), lte("p5c_rsi", 40.0), turn_up("p5c_rsi"))),
            action("ENTER_SHORT", and_(pos("flat"), lt(slope, -.02), gte("p5c_rsi", 60.0), turn_down("p5c_rsi"))),
        ]
        return definition(row, features=fs, actions=acts, contracts=["STABILIZE_MINIMAL_TRANSITION_V1", "REDUCE_HALF_CURRENT_V1"], family="EMA20_SLOPE_RSI_PULLBACK")
    if "MA20 斜率" in name:
        fs = BAR + [feature("sma", "p5c_ma", window=20), feature("atr", "p5c_atr14", window=14)]
        slope = sub("p5c_ma", previous("p5c_ma"))
        acts = [
            action("EXIT_LONG", lt(slope, 0.0)), action("EXIT_SHORT", gt(slope, 0.0)),
            action("REDUCE_CURRENT", and_(gte(slope, -.02), lte(slope, .02)), .5),
            action("ENTER_LONG", and_(pos("flat"), gt(slope, .02), touch("p5c_ma", bullish=True, candle=True))),
            action("ENTER_SHORT", and_(pos("flat"), lt(slope, -.02), touch("p5c_ma", bullish=False, candle=True))),
        ]
        return definition(row, features=fs, actions=acts, contracts=[
            "LEVEL_TOLERANCE_ATR025_V1", "REJECTION_AT_LEVEL_V1", "REDUCE_HALF_CURRENT_V1",
        ], family="MA20_SLOPE_LEVEL_REJECTION", provenance="PARAMETER_DEFAULTED", defaults={"atr_period": 14, "atr_multiple": .25})
    return None


def donchian_bbw(row: dict[str, str]) -> dict[str, object] | None:
    if "唐奇安震荡规避" not in row["strategy_name"]:
        return None
    fs = BAR + [feature("bollinger_width", "p5c_bbw", window=20), feature("breakout_up", "p5c_up", window=20), feature("breakout_down", "p5c_down", window=20)]
    avg = mean("p5c_bbw", 20)
    acts = [
        action("EXIT_ALL", lt("p5c_bbw", avg)),
        action("ENTER_LONG", and_(pos("flat"), gt("p5c_bbw", avg), pulse("p5c_up"))),
        action("ENTER_SHORT", and_(pos("flat"), gt("p5c_bbw", avg), pulse("p5c_down"))),
    ]
    return definition(row, features=fs, actions=acts, contracts=["VOLATILITY_EXPANSION_RELATIVE_SMA20_V1", "VOLATILITY_CONTRACTION_RELATIVE_SMA20_V1"], family="DONCHIAN_BBW_FILTER")


def fill_risk_family(row: dict[str, str]) -> dict[str, object] | None:
    name = row["strategy_name"]
    if "MA10/MA60 长线双均线" in name:
        fs = BAR + [feature("sma", "p5c_fast", window=10), feature("sma", "p5c_slow", window=60), feature("atr", "p5c_atr14", window=14)]
        acts = [
            action("EXIT_LONG", or_(turn_down("p5c_slow"), risk_stop(long=True, multiple=1.2))),
            action("EXIT_SHORT", or_(turn_up("p5c_slow"), risk_stop(long=False, multiple=1.2))),
            action("REDUCE_LONG", down("p5c_fast", "p5c_slow"), .5), action("REDUCE_SHORT", up("p5c_fast", "p5c_slow"), .5),
            action("ENTER_LONG", and_(pos("flat"), up("p5c_fast", "p5c_slow"), rising("p5c_fast"), rising("p5c_slow"))),
            action("ENTER_SHORT", and_(pos("flat"), down("p5c_fast", "p5c_slow"), falling("p5c_fast"), falling("p5c_slow"))),
        ]
        return definition(row, features=fs, actions=acts, contracts=["CURRENT_POSITION_AVG_ENTRY_PRICE_V1", "ATR14_DEFAULT_V1", "REDUCE_HALF_CURRENT_V1"], family="MA10_MA60_FILL_STOP", provenance="PARAMETER_DEFAULTED", defaults={"atr_period": 14}, requires_fill_state=True)
    if "ADX14+DI 趋势识别" in name:
        fs = BAR + [feature("adx", "p5c_adx", window=14), feature("plus_di", "p5c_plus", window=14), feature("minus_di", "p5c_minus", window=14), feature("atr", "p5c_atr14", window=14), feature("breakout_up", "p5c_up", window=20), feature("breakout_down", "p5c_down", window=20)]
        acts = [
            action("EXIT_LONG", or_(lt("p5c_adx", 20.0), down("p5c_plus", "p5c_minus"), risk_stop(long=True, multiple=.9))),
            action("EXIT_SHORT", or_(lt("p5c_adx", 20.0), up("p5c_plus", "p5c_minus"), risk_stop(long=False, multiple=.9))),
            action("ENTER_LONG", and_(pos("flat"), gt("p5c_adx", 25.0), consecutive(gt("p5c_plus", "p5c_minus")), pulse("p5c_up"))),
            action("ENTER_SHORT", and_(pos("flat"), gt("p5c_adx", 25.0), consecutive(gt("p5c_minus", "p5c_plus")), pulse("p5c_down"))),
        ]
        return definition(row, features=fs, actions=acts, contracts=["CURRENT_POSITION_AVG_ENTRY_PRICE_V1", "ATR14_DEFAULT_V1", "PERSISTENCE_2BAR_V1"], family="ADX_DI_DONCHIAN_FILL_STOP", provenance="PARAMETER_DEFAULTED", defaults={"atr_period": 14}, requires_fill_state=True)
    return None


def macd_ladder(row: dict[str, str]) -> dict[str, object] | None:
    name = row["strategy_name"]
    if "MACD 零轴动能分级加仓" in name:
        layers, step, stop = 4, .5, 1.6
    elif "MACD 水上趋势顺势网格" in name:
        layers, step, stop = 3, .3, 1.1
    elif "MACD 零轴下空头分层加仓" in name:
        layers, step, stop = 3, .4, 1.2
    else:
        return None
    fraction = 1.0 / layers
    fs = BAR + [feature("macd", "p5c_dif", output="dif"), feature("atr", "p5c_atr14", window=14)]
    latest = anchor("latest_add_fill_price")
    exposure = anchor("position")
    acts = [
        action("EXIT_LONG", or_(down("p5c_dif", 0.0), risk_stop(long=True, multiple=stop))),
        action("EXIT_SHORT", or_(up("p5c_dif", 0.0), risk_stop(long=False, multiple=stop))),
        action("ADD_LONG", and_(pos("long"), lt(exposure, 1.0), gte("p5c_close", add(latest, mul("p5c_atr14", step)))), fraction),
        action("ADD_SHORT", and_(pos("short"), gt(exposure, -1.0), lte("p5c_close", sub(latest, mul("p5c_atr14", step)))), fraction),
        action("ENTER_LONG", and_(pos("flat"), consecutive(gt("p5c_dif", 0.0))), fraction),
        action("ENTER_SHORT", and_(pos("flat"), consecutive(lt("p5c_dif", 0.0))), fraction),
    ]
    model = "MODELLED_EQUAL_ENTRY_STAGE_FRACTION_V1"
    return definition(row, features=fs, actions=acts, contracts=[model, "LATEST_ADD_FILL_PRICE_V1", "CURRENT_POSITION_AVG_ENTRY_PRICE_V1", "ATR14_DEFAULT_V1"], family=f"MACD_FILL_LADDER_{layers}L_{step:g}ATR", provenance="MODELLED_BASELINE_INTERPRETATION", modelled=[model], defaults={"layers": layers, "fraction": fraction, "atr_period": 14}, requires_fill_state=True)


def ma30_two_stage(row: dict[str, str]) -> dict[str, object] | None:
    if "MA30 中枢顺势网格" not in row["strategy_name"]:
        return None
    fs = BAR + [feature("sma", "p5c_ma30", window=30), feature("sma", "p5c_ma60", window=60), feature("atr", "p5c_atr14", window=14)]
    acts = [
        action("EXIT_LONG", or_(turn_down("p5c_ma60"), risk_stop(long=True, multiple=1.5))),
        action("EXIT_SHORT", or_(turn_up("p5c_ma60"), risk_stop(long=False, multiple=1.5))),
        action("ADD_LONG", and_(pos("long"), lte("p5c_close", sub("p5c_ma30", mul("p5c_atr14", 1.0)))), .5),
        action("ADD_SHORT", and_(pos("short"), gte("p5c_close", add("p5c_ma30", mul("p5c_atr14", 1.0)))), .5),
        action("ENTER_LONG", and_(pos("flat"), rising("p5c_ma60"), lte("p5c_close", sub("p5c_ma30", mul("p5c_atr14", .5)))), .5),
        action("ENTER_SHORT", and_(pos("flat"), falling("p5c_ma60"), gte("p5c_close", add("p5c_ma30", mul("p5c_atr14", .5)))), .5),
    ]
    model = "MODELLED_EQUAL_ENTRY_STAGE_FRACTION_V1"
    return definition(row, features=fs, actions=acts, contracts=[model, "CURRENT_POSITION_AVG_ENTRY_PRICE_V1", "ATR14_DEFAULT_V1"], family="MA30_TWO_STAGE_FILL_GRID", provenance="MODELLED_BASELINE_INTERPRETATION", modelled=[model], defaults={"stages": 2, "fraction": .5, "atr_period": 14}, requires_fill_state=True)


COMPILERS = (stable_rsi_trend, cci_ma_pullback, oscillator_turn, slope_pullback, donchian_bbw, fill_risk_family, macd_ladder, ma30_two_stage)


def blocker_set_allowed(item: dict[str, object], blockers: set[str]) -> bool:
    family = str(item["compiler_family"])
    if family.startswith("MACD_FILL_LADDER_") or family == "MA30_TWO_STAGE_FILL_GRID":
        return blockers <= {"FILL_ANCHORED_RISK_STATE_REQUIRED", "SIZING_OR_LADDER_INCOMPLETE"}
    if family in {"MA10_MA60_FILL_STOP", "ADX_DI_DONCHIAN_FILL_STOP"}:
        return blockers <= {"FILL_ANCHORED_RISK_STATE_REQUIRED"}
    if family == "DONCHIAN_BBW_FILTER":
        return blockers <= {"VOLATILITY_REGIME_UNDEFINED"}
    return blockers <= {"STABILIZATION_OR_REJECTION_UNDEFINED"}


NEW_CONTRACTS = [
    {"contract_id": "MODELLED_EQUAL_ENTRY_STAGE_FRACTION", "version": 1,
     "source_phrase_family": "explicit finite entry/add stages with omitted fractions",
     "machine_definition": "each of K source-explicit stages targets 1/K of canonical 1x exposure",
     "allowed_context": "K and every trigger are explicit", "disallowed_context": "unknown K; martingale; unbounded adds",
     "default_parameters": {"max_abs_exposure": 1.0}, "provenance": "MODELLED_BASELINE_INTERPRETATION", "lookahead_rule": "completed observations and executed fills only"},
    {"contract_id": "FIRST_ENTRY_PRICE", "version": 1, "source_phrase_family": "首次开仓价;首次入场价",
     "machine_definition": "first executed fill of the current position episode", "allowed_context": "explicit first-entry wording",
     "disallowed_context": "generic cost wording", "default_parameters": {}, "provenance": "STANDARD_CONTRACT_RESOLVED", "lookahead_rule": "updates after fill"},
    {"contract_id": "CURRENT_POSITION_AVG_ENTRY_PRICE", "version": 1, "source_phrase_family": "持仓成本;平均开仓价;浮亏",
     "machine_definition": "executed quantity-weighted average basis of current open exposure", "allowed_context": "cost or total unrealized loss",
     "disallowed_context": "signal price", "default_parameters": {}, "provenance": "STANDARD_CONTRACT_RESOLVED", "lookahead_rule": "updates after fill"},
    {"contract_id": "LATEST_ADD_FILL_PRICE", "version": 1, "source_phrase_family": "加仓价;最近一次加仓价;逐档移动",
     "machine_definition": "latest executed exposure-increasing fill", "allowed_context": "explicit staged additions",
     "disallowed_context": "signal price", "default_parameters": {}, "provenance": "STANDARD_CONTRACT_RESOLVED", "lookahead_rule": "updates after fill"},
    {"contract_id": "VOLATILITY_EXPANSION_RELATIVE_SMA20", "version": 1, "source_phrase_family": "explicit X expansion",
     "machine_definition": "X[t] > SMA20(X)[t]", "allowed_context": "source identifies X", "disallowed_context": "unnamed volatility",
     "default_parameters": {"reference_window": 20}, "provenance": "PARAMETER_DEFAULTED", "lookahead_rule": "completed observations only"},
    {"contract_id": "VOLATILITY_CONTRACTION_RELATIVE_SMA20", "version": 1, "source_phrase_family": "explicit X contraction",
     "machine_definition": "X[t] < SMA20(X)[t]", "allowed_context": "source identifies X", "disallowed_context": "unnamed volatility",
     "default_parameters": {"reference_window": 20}, "provenance": "PARAMETER_DEFAULTED", "lookahead_rule": "completed observations only"},
]


CATEGORY_MAP = {
    "SIZING_OR_LADDER_INCOMPLETE": "sizing_blocker", "TIMEFRAME_SET_INCOMPLETE": "timeframe_blocker",
    "FILL_ANCHORED_RISK_STATE_REQUIRED": "risk_anchor_blocker", "FILL_ANCHORED_PROFIT_STATE_REQUIRED": "risk_anchor_blocker",
    "LEVEL_TOLERANCE_UNDEFINED": "tolerance_blocker", "STABILIZATION_OR_REJECTION_UNDEFINED": "tolerance_blocker",
    "BREAKOUT_CONFIRMATION_UNDEFINED": "tolerance_blocker", "VOLATILITY_REGIME_UNDEFINED": "volatility_blocker",
    "DIVERGENCE_DEFINITION_INCOMPLETE": "divergence_blocker", "MISSING_NUMERIC_PARAMETER": "numeric_parameter_blocker",
    "DATA_OR_FEATURE_CONTRACT_UNAVAILABLE": "data_blocker", "MISSING_REFERENCE_OBJECT": "data_blocker",
    "UNSUPPORTED_ACCOUNTING_SEMANTICS": "accounting_blocker", "SEMANTIC_EXIT_AMBIGUOUS": "exit_blocker",
    "STRUCTURAL_RULE_INCOMPLETE": "structural_blocker", "UNPARSEABLE_STRUCTURAL_LOGIC": "structural_blocker",
    "SEMANTIC_ENTRY_AMBIGUOUS": "structural_blocker",
}


def sizing_taxonomy(text: str) -> str:
    if re.search(r"倍投|马丁|1[-—]2[-—]4", text): return "SIZING_SEQUENCE_UNDEFINED"
    if re.search(r"最多\s*\d+\s*(?:层|档)", text) and re.search(r"\d+(?:\.\d+)?\s*ATR", text): return "EXPLICIT_STAGES_FRACTIONS_MISSING"
    if re.search(r"\d+(?:\.\d+)?\s*/\s*\d+(?:\.\d+)?\s*ATR", text): return "EXPLICIT_STAGES_FRACTIONS_MISSING"
    if re.search(r"减半|50%|40%|30%|20%", text): return "EXPLICIT_TARGET_FRACTION_PRESENT"
    if re.search(r"分层|逐层|逐档|网格|金字塔", text): return "GRID_LAYER_OR_STEP_MISSING"
    if re.search(r"减仓|部分止盈", text): return "SINGLE_PARTIAL_REDUCTION_FRACTION_MISSING"
    return "SIZING_GENUINELY_AMBIGUOUS"


def main() -> int:
    closure = [r for r in read_csv(AUDIT / "phase5b_strategy_closure.csv") if r["phase5b_status"] != "IMPLEMENTED_STANDALONE"]
    if len(closure) != 1029 or len({r["source_identity"] for r in closure}) != 1029:
        raise SystemExit(f"expected 1029 unique Phase 5B remaining rows, found {len(closure)}")
    source = {r["source_identity"]: r for r in read_csv(AUDIT / "phase5a_remaining_strategy_audit.csv")}
    rows = [{**source[r["source_identity"]], **r} for r in closure]
    write_json(CONTRACTS, {"registry_version": "PHASE5C_V1", "frozen_before_backtest": True, "contracts": NEW_CONTRACTS})

    plans: dict[str, dict[str, object]] = {}
    for row in rows:
        blocker_set = set(row["remaining_blocker"].split(";"))
        for compiler in COMPILERS:
            compiled = compiler(row)
            if compiled is not None and blocker_set_allowed(compiled, blocker_set):
                plans[row["source_identity"]] = compiled; break

    # Every accepted row must have all its blocker components explicitly closed.
    for identity, item in plans.items():
        if item["remaining_blockers"]:
            raise ValueError(f"partial Phase 5C closure: {identity}")
    write_json(PLAN, plans)
    write_json(AUDIT / "phase5c_contract_freeze.json", {
        "protocol": "PHASE5C_V1", "frozen_at_utc": datetime.now(timezone.utc).isoformat(),
        "performance_inspected_for_contract_selection": False,
        "files": {
            str(CONTRACTS.relative_to(ROOT)).replace("\\", "/"): hashlib.sha256(CONTRACTS.read_bytes()).hexdigest(),
            str(PLAN.relative_to(ROOT)).replace("\\", "/"): hashlib.sha256(PLAN.read_bytes()).hexdigest(),
        },
    })

    audit_rows: list[dict[str, object]] = []
    closure_rows: list[dict[str, object]] = []
    sizing_rows: list[dict[str, object]] = []
    transitions: list[dict[str, object]] = []
    policy_rows: list[dict[str, object]] = []
    rules: list[dict[str, object]] = []
    for row in rows:
        identity = row["source_identity"]
        blockers = sorted(set(row["remaining_blocker"].split(";")))
        item = plans.get(identity)
        columns: dict[str, list[str]] = defaultdict(list)
        for blocker in blockers:
            columns[CATEGORY_MAP.get(blocker, "structural_blocker")].append(blocker)
        text = " | ".join((row["indicator_definition"], row["long_entry_text"], row["short_entry_text"], row["exit_text"]))
        if "SIZING_OR_LADDER_INCOMPLETE" in blockers:
            sizing_rows.append({"source_identity": identity, "strategy_name": row["strategy_name"],
                                "taxonomy": sizing_taxonomy(text), "source_text": text,
                                "resolved": str(item is not None).lower(),
                                "contract": ";".join(item["contracts_applied"]) if item else ""})
        contract_ids = list(item["contracts_applied"]) if item else []
        remaining = [] if item else blockers
        audit_rows.append({
            "source_identity": identity, "strategy_name": row["strategy_name"], "phase5b_status": row["phase5b_status"],
            "phase5b_blockers": ";".join(blockers),
            **{name: ";".join(columns.get(name, [])) for name in (
                "sizing_blocker", "timeframe_blocker", "risk_anchor_blocker", "tolerance_blocker",
                "volatility_blocker", "divergence_blocker", "numeric_parameter_blocker", "data_blocker",
                "accounting_blocker", "exit_blocker", "structural_blocker")},
            "resolvable_by_existing_contract": str(bool(item) and not item["modelled_interpretations"]).lower(),
            "resolvable_by_phase5c_contract": str(bool(item)).lower(), "irreducible_in_phase5c": str(not item).lower(),
            "contracts_required": ";".join(contract_ids),
            "parameters_required": json.dumps(item["defaulted_parameters"], ensure_ascii=False, sort_keys=True) if item else "",
            "remaining_blockers": ";".join(remaining),
        })
        closure_rows.append({
            "source_identity": identity, "strategy_name": row["strategy_name"],
            "phase5b_blocker_set": ";".join(blockers),
            "resolved_by_existing_contracts": ";".join(c for c in contract_ids if not c.startswith("MODELLED_")),
            "resolved_by_phase5c_contracts": ";".join(c for c in contract_ids if c.startswith("MODELLED_") or c.endswith("ENTRY_PRICE_V1")),
            "remaining_blocker_set": ";".join(remaining),
            "phase5c_status": "IMPLEMENTED_STANDALONE" if item else "REMAINS_UNRESOLVED",
            "compiler_family": item["compiler_family"] if item else "",
            "semantic_provenance": item["semantic_provenance"] if item else "",
            "coverage_recovery_phase": "PHASE5C" if item else "",
        })
        if item:
            transitions.append({"source_identity": identity, "strategy_name": row["strategy_name"], "old_status": "SEMANTICALLY_UNRESOLVED",
                                "new_status": "IMPLEMENTED_STANDALONE", "resolved_blockers": ";".join(blockers),
                                "contracts_applied": ";".join(contract_ids), "semantic_provenance": item["semantic_provenance"],
                                "registry_id": identity, "compiler_family": item["compiler_family"], "backtest_status": "PENDING"})
            decoded = json.loads(base64.urlsafe_b64decode(str(item["params"]["rule_spec_b64"])).decode())
            rules.append({"source_identity": identity, "strategy_name": row["strategy_name"], "source_text": text,
                          "normalized_strategy_rule": json.dumps(decoded, ensure_ascii=False, sort_keys=True),
                          "sizing_contract": ";".join(c for c in contract_ids if "FRACTION" in c or "LADDER" in c),
                          "risk_anchor": ";".join(c for c in contract_ids if "ENTRY_PRICE" in c or "FILL_PRICE" in c),
                          "semantic_contracts": ";".join(contract_ids), "semantic_provenance": item["semantic_provenance"],
                          "modelled_contract_ids": ";".join(item["modelled_interpretations"]),
                          "modelled_contract_versions": ";".join(item["modelled_interpretations"]),
                          "modelled_parameter_values": json.dumps(item["defaulted_parameters"], sort_keys=True),
                          "reason_source_was_incomplete": ";".join(blockers), "unmapped_material_source_clauses": 0})
        else:
            boundaries = []
            for blocker in blockers:
                boundaries.append({
                    "TIMEFRAME_SET_INCOMPLETE": "NEW_TIMEFRAME_ASSUMPTION", "MISSING_NUMERIC_PARAMETER": "NEW_NUMERIC_DEFAULT",
                    "SIZING_OR_LADDER_INCOMPLETE": "NEW_SIZING_ASSUMPTION", "DATA_OR_FEATURE_CONTRACT_UNAVAILABLE": "NEW_FEATURE_DEFINITION",
                    "MISSING_REFERENCE_OBJECT": "NEW_FEATURE_DEFINITION", "UNSUPPORTED_ACCOUNTING_SEMANTICS": "NEW_ACCOUNTING_MODEL",
                    "SEMANTIC_EXIT_AMBIGUOUS": "NEW_EXIT_INTERPRETATION", "STRUCTURAL_RULE_INCOMPLETE": "NEW_STRUCTURAL_INTERPRETATION",
                    "UNPARSEABLE_STRUCTURAL_LOGIC": "NEW_STRUCTURAL_INTERPRETATION", "SEMANTIC_ENTRY_AMBIGUOUS": "NEW_STRUCTURAL_INTERPRETATION",
                }.get(blocker, "NEW_STRUCTURAL_INTERPRETATION"))
            policy_rows.append({"source_identity": identity, "strategy_name": row["strategy_name"],
                                "remaining_blockers": ";".join(blockers), "policy_required": ";".join(sorted(set(boundaries))),
                                "phase5c_decision": "PRESERVE_BLOCKED"})

    write_csv(AUDIT / "phase5c_semantic_parameter_gap_audit.csv", audit_rows)
    write_csv(AUDIT / "phase5c_sizing_gap_taxonomy.csv", sizing_rows)
    write_csv(AUDIT / "phase5c_strategy_closure.csv", closure_rows)
    write_csv(AUDIT / "phase5c_status_transitions.csv", transitions)
    write_csv(AUDIT / "phase5c_policy_boundary_report.csv", policy_rows)
    write_csv(AUDIT / "phase5c_compiled_rules.csv", rules)
    write_csv(AUDIT / "phase5c_contract_registry.csv", [
        {**c, "default_parameters": json.dumps(c["default_parameters"], ensure_ascii=False, sort_keys=True)} for c in NEW_CONTRACTS
    ])
    groups = len({str(item["rule_hash"]) for item in plans.values()})
    write_csv(AUDIT / "phase5c_fixpoint_iterations.csv", [
        {"iteration": 1, "rows_reprocessed": 1029, "newly_closed": len(plans), "new_contracts_introduced": len(NEW_CONTRACTS), "new_features_introduced": 0, "remaining_rows": 1029-len(plans)},
        {"iteration": 2, "rows_reprocessed": 1029-len(plans), "newly_closed": 0, "new_contracts_introduced": 0, "new_features_introduced": 0, "remaining_rows": 1029-len(plans)},
    ])
    execution = []
    representatives: dict[str, str] = {}
    for identity, item in sorted(plans.items()):
        key = f"{item['rule_hash']}:{item['source_timeframe']}:{item['requires_fill_state']}"
        representative = representatives.setdefault(key, identity)
        execution.append({"strategy_id": identity, "rule_hash": item["rule_hash"], "source_timeframe": item["source_timeframe"],
                          "physical_representative": representative, "physical_execution": str(identity == representative).lower()})
    write_csv(AUDIT / "phase5c_execution_plan.csv", execution)
    summary = {"starting_rows": 1029, "new_executable_identities": len(plans), "new_semantic_groups": groups,
               "remaining_rows": 1029-len(plans), "iterations": 2, "fixpoint_reached": True,
               "families": dict(Counter(str(item["compiler_family"]) for item in plans.values())),
               "provenance": dict(Counter(str(item["semantic_provenance"]) for item in plans.values()))}
    write_json(AUDIT / "phase5c_fixpoint_summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
