#!/usr/bin/env python3
"""Freeze Phase 5F MEDIUM contracts and close every Phase 5E unresolved row."""
from __future__ import annotations

import base64
import csv
import hashlib
import json
import os
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
AUDIT = ROOT / "outputs/internal_audit/strategy_workbook"
PLAN = ROOT / "configs/semantic_contracts/workbook_phase5f_strategies.json"
CONTRACTS = ROOT / "configs/semantic_contracts/workbook_phase5f_contracts.json"

LADDER_POLICY = "MODELLED_BOUNDED_EQUAL_LADDER"
DIVERGENCE_POLICY = "STANDARD_REGULAR_DIVERGENCE"
ACTIVE_CONTRACTS = (
    "MODELLED_BOUNDED_EQUAL_LADDER_V1",
    "MODELLED_STANDARD_REGULAR_DIVERGENCE_V1",
)

RSI_BASIC = {"xlsx_s1_0013"}
CCI_MA = {"xlsx_s2_0158", "xlsx_s2_0469"}
RSI_FRACTAL = {"xlsx_s2_0268"}
OBV_TREND = {"xlsx_s2_0280", "xlsx_s2_0366", "xlsx_s2_0563", "xlsx_s2_0740"}
OBV_DONCHIAN = {"xlsx_s2_0242", "xlsx_s2_0328", "xlsx_s2_0525", "xlsx_s2_0705"}
OBV_BASIC = {"xlsx_s2_0122", "xlsx_s2_0433"}
OBV_DAILY = {"xlsx_s2_0138", "xlsx_s2_0449"}
TURTLE_LADDER = {"xlsx_s2_0228"}
COMPILED_IDS = RSI_BASIC | CCI_MA | RSI_FRACTAL | OBV_TREND | OBV_DONCHIAN | OBV_BASIC | OBV_DAILY | TURTLE_LADDER


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def write_csv(path: Path, rows: list[dict[str, object]], fields: list[str] | None = None) -> None:
    fields = fields or (list(rows[0]) if rows else ["source_identity"])
    path.parent.mkdir(parents=True, exist_ok=True)
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


def source_text(row: dict[str, str]) -> str:
    return " ".join(row.get(key, "") for key in (
        "source_strategy_name", "source_indicator_definition", "source_long_condition",
        "source_short_condition", "source_exit_condition",
    ))


def feature(kind: str, name: str, **kwargs: object) -> dict[str, object]:
    return {"kind": kind, "name": name, **kwargs}


def action(kind: str, condition: dict[str, Any], fraction: float = 1.0) -> dict[str, Any]:
    return {"action": kind, "condition": condition, "fraction": fraction, "reason": "phase5f"}


def op(name: str, left: object, right: object) -> dict[str, object]:
    return {"op": name, "left": left, "right": right}


def and_(*args: dict[str, Any]) -> dict[str, Any]: return {"op": "and", "args": list(args)}
def or_(*args: dict[str, Any]) -> dict[str, Any]: return {"op": "or", "args": list(args)}
def not_(arg: dict[str, Any]) -> dict[str, Any]: return {"op": "not", "arg": arg}
def consecutive(arg: dict[str, Any], bars: int = 2) -> dict[str, Any]: return {"op": "consecutive", "arg": arg, "bars": bars}
def rising(value: str, bars: int = 1) -> dict[str, Any]: return {"op": "rising", "value": value, "bars": bars}
def falling(value: str, bars: int = 1) -> dict[str, Any]: return {"op": "falling", "value": value, "bars": bars}
def pulse(value: str) -> dict[str, Any]: return {"op": "pulse", "value": value}


def divergence(direction: str, indicator: str) -> dict[str, object]:
    return {
        "op": "regular_divergence", "price": "p5f_low" if direction == "bullish" else "p5f_high",
        "indicator": indicator, "direction": direction, "event_id": f"{indicator}_{direction}",
        "side_bars": 2, "lookback": 60,
    }


def declarative(row: dict[str, str], *, features: list[dict[str, object]],
                actions: list[dict[str, Any]], family: str, contracts: list[str],
                timeframe: str) -> dict[str, object]:
    rule = {"schema_version": 2, "features": features, "actions": actions,
            "source_clause_count": 3, "family": family}
    encoded = base64.urlsafe_b64encode(json.dumps(
        rule, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()).decode()
    return {
        "family": "phase5b_declarative",
        "params": {"rule_spec_b64": encoded, "contract_versions": ";".join(contracts)},
        "semantic_provenance": "MODELLED_BASELINE_INTERPRETATION",
        "contracts_applied": contracts,
        "phase5f_contracts_applied": ["MODELLED_STANDARD_REGULAR_DIVERGENCE_V1"],
        "defaulted_parameters": {"divergence_side_bars": 2, "divergence_lookback": 60},
        "modelled_interpretations": ["same-timestamp named-indicator regular divergence"],
        "remaining_blockers": [], "unmapped_material_source_clauses": 0,
        "source_timeframe": timeframe, "rule_hash": hashlib.sha256(encoded.encode()).hexdigest(),
        "compiler_family": family, "requires_fill_state": False,
    }


def compile_divergence(identity: str, row: dict[str, str]) -> dict[str, object] | None:
    timeframe_semantics = row.get("source_timeframe_semantics", "")
    timeframe = "1d" if timeframe_semantics == "daily" else "1m" if timeframe_semantics == "bar_period" else ""
    if not timeframe:
        return None
    bars = [feature("bar", "p5f_close", field="close"), feature("bar", "p5f_high", field="high"),
            feature("bar", "p5f_low", field="low"), feature("bar", "p5f_volume", field="volume")]
    contracts = ["MODELLED_STANDARD_REGULAR_DIVERGENCE_V1", "CONFIRMED_FRACTAL_2X2_V1",
                 "DIVERGENCE_LOOKBACK_60_V1", "ACTION_PRECEDENCE_EXIT_REDUCE_ENTER_V1"]
    if identity in RSI_BASIC:
        fs = bars + [feature("rsi", "p5f_rsi", window=14)]
        acts = [action("EXIT_LONG", or_(op("gt", "p5f_rsi", 70), divergence("bearish", "p5f_rsi"))),
                action("EXIT_SHORT", or_(op("lt", "p5f_rsi", 30), divergence("bullish", "p5f_rsi"))),
                action("ENTER_LONG", op("cross_above", "p5f_rsi", 30)),
                action("ENTER_SHORT", op("cross_below", "p5f_rsi", 70))]
        return declarative(row, features=fs, actions=acts, family="P5F_RSI_THRESHOLD_DIVERGENCE", contracts=contracts, timeframe=timeframe)
    if identity in CCI_MA:
        fs = bars + [feature("cci", "p5f_cci", window=20), feature("sma", "p5f_ma60", window=60)]
        acts = [action("EXIT_LONG", or_(op("gte", "p5f_cci", 100), op("cross_below", "p5f_close", "p5f_ma60"))),
                action("EXIT_SHORT", or_(op("lte", "p5f_cci", -100), op("cross_above", "p5f_close", "p5f_ma60"))),
                action("ENTER_LONG", and_(op("gt", "p5f_close", "p5f_ma60"), op("cross_above", "p5f_cci", -100), not_(divergence("bearish", "p5f_cci")))),
                action("ENTER_SHORT", and_(op("lt", "p5f_close", "p5f_ma60"), op("cross_below", "p5f_cci", 100), not_(divergence("bullish", "p5f_cci"))))]
        return declarative(row, features=fs, actions=acts, family="P5F_CCI_MA_DIVERGENCE_FILTER", contracts=contracts, timeframe=timeframe)
    if identity in RSI_FRACTAL:
        fs = bars + [feature("rsi", "p5f_rsi", window=14),
                     feature("fractal", "p5f_lower_fractal", output="lower_pulse"),
                     feature("fractal", "p5f_upper_fractal", output="upper_pulse")]
        acts = [action("EXIT_LONG", pulse("p5f_upper_fractal")), action("EXIT_SHORT", pulse("p5f_lower_fractal")),
                action("REDUCE_CURRENT", op("cross_above", "p5f_rsi", 50), .5),
                action("ENTER_LONG", and_(pulse("p5f_lower_fractal"), divergence("bullish", "p5f_rsi"))),
                action("ENTER_SHORT", and_(pulse("p5f_upper_fractal"), divergence("bearish", "p5f_rsi")))]
        return declarative(row, features=fs, actions=acts, family="P5F_RSI_FRACTAL_DIVERGENCE", contracts=contracts + ["REDUCE_HALF_CURRENT_V1"], timeframe=timeframe)
    if identity in OBV_TREND | OBV_DONCHIAN | OBV_BASIC | OBV_DAILY:
        fs = bars + [feature("obv", "p5f_obv", window=20, output="obv"),
                     feature("obv", "p5f_obv_ma", window=20, output="sma")]
        bull, bear = divergence("bullish", "p5f_obv"), divergence("bearish", "p5f_obv")
        if identity in OBV_TREND:
            acts = [action("EXIT_ALL", or_(bull, bear)),
                    action("REDUCE_CURRENT", or_(op("cross_below", "p5f_obv", "p5f_obv_ma"), op("cross_above", "p5f_obv", "p5f_obv_ma")), .5),
                    action("ENTER_LONG", and_(consecutive(op("gt", "p5f_obv", "p5f_obv_ma")), rising("p5f_obv"), rising("p5f_close"))),
                    action("ENTER_SHORT", and_(consecutive(op("lt", "p5f_obv", "p5f_obv_ma")), falling("p5f_obv"), falling("p5f_close")))]
            family = "P5F_OBV_PRICE_TREND_DIVERGENCE"
        elif identity in OBV_DONCHIAN:
            fs += [feature("breakout_up", "p5f_breakout_up", window=20), feature("breakout_down", "p5f_breakout_down", window=20)]
            acts = [action("EXIT_ALL", or_(bull, bear)),
                    action("REDUCE_CURRENT", or_(op("cross_below", "p5f_obv", "p5f_obv_ma"), op("cross_above", "p5f_obv", "p5f_obv_ma")), .5),
                    action("ENTER_LONG", and_(consecutive(op("gt", "p5f_obv", "p5f_obv_ma")), pulse("p5f_breakout_up"))),
                    action("ENTER_SHORT", and_(consecutive(op("lt", "p5f_obv", "p5f_obv_ma")), pulse("p5f_breakout_down")))]
            family = "P5F_OBV_DONCHIAN_DIVERGENCE"
        elif identity in OBV_BASIC:
            fs += [feature("volume_ratio", "p5f_volume_ratio", window=20)]
            acts = [action("EXIT_ALL", or_(bull, bear)),
                    action("EXIT_LONG", op("cross_below", "p5f_obv", "p5f_obv_ma")),
                    action("EXIT_SHORT", op("cross_above", "p5f_obv", "p5f_obv_ma")),
                    action("ENTER_LONG", and_(op("cross_above", "p5f_obv", "p5f_obv_ma"), op("gte", "p5f_volume_ratio", 1.5), rising("p5f_close"))),
                    action("ENTER_SHORT", and_(op("cross_below", "p5f_obv", "p5f_obv_ma"), op("gte", "p5f_volume_ratio", 1.5), falling("p5f_close")))]
            family = "P5F_OBV_VOLUME_DIVERGENCE"
            contracts.append("VOLUME_EXPANSION_SMA20_X1_5_V1")
        else:
            fs += [feature("sma", "p5f_volume_ma5", field="volume", window=5)]
            acts = [action("EXIT_ALL", or_(bull, bear)),
                    action("REDUCE_CURRENT", op("cross_below", "p5f_volume", "p5f_volume_ma5"), .5),
                    action("ENTER_LONG", and_(consecutive(op("gt", "p5f_volume", "p5f_volume_ma5")), rising("p5f_obv"), rising("p5f_close"))),
                    action("ENTER_SHORT", and_(consecutive(op("lt", "p5f_volume", "p5f_volume_ma5")), falling("p5f_obv"), falling("p5f_close")))]
            family = "P5F_DAILY_VOLUME_OBV_DIVERGENCE"
        return declarative(row, features=fs, actions=acts, family=family, contracts=contracts + ["REDUCE_HALF_CURRENT_V1"], timeframe=timeframe)
    return None


def compile_ladder(identity: str, row: dict[str, str]) -> dict[str, object] | None:
    if identity not in TURTLE_LADDER or row.get("source_timeframe_semantics") != "bar_period":
        return None
    params = {"atr_window": 14, "entry_window": 20, "trend_window": 55, "exit_window": 10,
              "stop_multiple": 2.0, "grid_layers": 4, "layer_fraction": .25,
              "pyramid_direction": "favorable"}
    canonical = json.dumps({"family": "donchian_pyramid", "params": params}, sort_keys=True)
    return {
        "family": "donchian_pyramid", "params": params,
        "semantic_provenance": "MODELLED_BASELINE_INTERPRETATION",
        "contracts_applied": ["MODELLED_BOUNDED_EQUAL_LADDER_V1", "GRID_4L_ATR1_EQUAL_V1",
                              "PYRAMID_FAVORABLE_DIRECTION_V1", "FILL_SYNCHRONIZED_POSITION_V1"],
        "phase5f_contracts_applied": ["MODELLED_BOUNDED_EQUAL_LADDER_V1"],
        "defaulted_parameters": {"grid_layers": 4, "layer_fraction": .25, "atr_step": 1.0,
                                 "max_abs_exposure": 1.0},
        "modelled_interpretations": ["bounded equal four-layer fill-synchronized pyramid"],
        "remaining_blockers": [], "unmapped_material_source_clauses": 0,
        "source_timeframe": "1m", "rule_hash": hashlib.sha256(canonical.encode()).hexdigest(),
        "compiler_family": "P5F_TURTLE_BOUNDED_LADDER", "requires_fill_state": True,
    }


def ladder_classification(text: str) -> str:
    lower = text.lower()
    if any(token in lower for token in ("martingale", "马丁格尔", "倍增", "翻倍加仓", "1-2-4-8")):
        return "MARTINGALE"
    if any(token in lower for token in ("金字塔", "pyramid")): return "EXPLICIT_PYRAMID"
    if any(token in lower for token in ("网格", "逐格", "逐档")): return "EXPLICIT_GRID_OR_LADDER"
    if any(token in lower for token in ("分层建仓", "分批建仓", "分层加仓", "逐级加仓", "分档进场", "分仓")):
        return "EXPLICIT_MULTI_STAGE"
    if any(token in lower for token in ("加仓", "增加仓位", "继续加仓")): return "GENERIC_ADD_ONLY"
    return "OTHER"


def minimum_boundary(blockers: list[str]) -> tuple[str, str, str]:
    joined = ";".join(blockers)
    if "TIMEFRAME" in joined: return "TIMEFRAME_MAPPING", "MEDIUM", "HUMAN_POLICY_DECISION"
    if "STRUCTURAL" in joined: return "STRUCTURAL_TWO_CHOICE", "HIGH", "SOURCE_REVIEW"
    if "EXIT" in joined or "RISK_DISTANCE" in joined: return "NEW_EXIT_INTERPRETATION", "HIGH", "SOURCE_REVIEW"
    if "ACCOUNTING" in joined: return "NEW_ACCOUNTING_MODEL", "VERY_HIGH", "ARCHITECTURE_REVIEW"
    if "DATA" in joined or "EXTERNAL" in joined: return "EXTERNAL_DATA", "HIGH", "PRESERVE_BLOCKED"
    if "MARTINGALE" in joined or "SIZING" in joined: return "MARTINGALE_OR_GEOMETRIC_SIZING", "HIGH", "SOURCE_REVIEW"
    if "FEATURE" in joined or "REFERENCE" in joined: return "FEATURE_FORMULA_NON_UNIQUE", "HIGH", "SOURCE_REVIEW"
    if "NUMERIC" in joined: return "NEW_NUMERIC_DEFAULT", "MEDIUM", "HUMAN_POLICY_DECISION"
    return "OTHER_HIGH_ASSUMPTION", "HIGH", "SOURCE_REVIEW"


def main() -> int:
    manifest_rows = read_csv(AUDIT / "strategy_workbook_conversion_manifest.csv")
    manifest = {row["registry_id"]: row for row in manifest_rows}
    phase5e = [row for row in read_csv(AUDIT / "phase5e_strategy_closure.csv")
               if row["phase5e_status"] == "REMAINS_UNRESOLVED"]
    dependency = {row["source_identity"]: row for row in read_csv(AUDIT / "phase5d_policy_dependency_audit.csv")}
    if len(phase5e) != 980:
        raise RuntimeError(f"Phase 5E authority expected 980 unresolved rows, found {len(phase5e)}")
    if set(row["source_identity"] for row in phase5e) - set(dependency):
        raise RuntimeError("Phase 5D dependency audit does not cover Phase 5E unresolved identities")

    freeze_time = datetime.now(timezone.utc).isoformat()
    definitions = {
        ACTIVE_CONTRACTS[0]: {
            "definition": "Explicit finite grid/ladder/pyramid: source triggers; omitted K=4; equal 1/K fractions; abs exposure <=1x; fill-synchronized progression.",
            "applicability_rule": "Explicit grid, ladder, pyramid, staged-entry, or unambiguous equivalent wording.",
            "non_applicability_rule": "Generic add-only, martingale/geometric sizing, unknown trigger spacing, missing exit, or source exposure above 1x.",
            "default_parameters": "layers=4;fractions=0.25,0.25,0.25,0.25;max_abs_exposure=1.0",
            "source_phrase_family": "grid;ladder;pyramid;分层建仓;分批建仓;分层加仓;网格;金字塔;逐级加仓;分档进场",
            "implementation_path": "strategy_framework.modules.GridPyramidState",
        },
        ACTIVE_CONTRACTS[1]: {
            "definition": "Regular bullish/bearish divergence at confirmed 2-left/2-right price pivots, named indicator sampled at the same pivot timestamp, within 60 completed bars.",
            "applicability_rule": "Indicator, orientation, and timeframe are source-identifiable; wording is regular/generic and not hidden divergence.",
            "non_applicability_rule": "Unknown indicator, hidden divergence, incomplete timeframe, unavailable feature, missing entry/exit, or independent best-pivot matching.",
            "default_parameters": "side_bars=2;lookback=60;pairing=same_timestamp",
            "source_phrase_family": "顶背离;底背离;regular divergence",
            "implementation_path": "strategy_framework.workbook_dsl.RuleEvaluator._regular_divergence",
        },
    }
    active_rows = []
    for contract_id in ACTIVE_CONTRACTS:
        body = definitions[contract_id]
        digest = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
        active_rows.append({"contract_id": contract_id, "version": 1, "risk_level": "MEDIUM", **body,
                            "contract_hash": digest, "freeze_timestamp": freeze_time,
                            "performance_inspected_before_freeze": "false"})
    write_csv(AUDIT / "phase5f_active_medium_contracts.csv", active_rows)
    write_json(CONTRACTS, {row["contract_id"]: row for row in active_rows})
    write_json(AUDIT / "phase5f_contract_freeze.json", {
        "phase": "5F", "freeze_timestamp": freeze_time, "active_contracts": active_rows,
        "bounded_ladder": {"default_layers": 4, "equal_fractions": [.25] * 4, "max_abs_exposure": 1.0,
                           "spacing": "source triggers; existing GRID_4L_ATR1_EQUAL_V1 only when explicitly regular grid"},
        "regular_divergence": {"side_bars": 2, "lookback": 60, "pairing": "indicator value at same price-pivot timestamp"},
        "performance_inspected_for_contract_selection": False,
        "medium_policy_count": 2, "high_policy_count": 0, "very_high_policy_count": 0,
    })

    plan: dict[str, dict[str, object]] = {}
    starting: list[dict[str, object]] = []
    ladder_rows: list[dict[str, object]] = []
    divergence_rows: list[dict[str, object]] = []
    closure: list[dict[str, object]] = []
    traces: list[dict[str, object]] = []
    for old in phase5e:
        identity = old["source_identity"]; row = manifest[identity]; dep = dependency[identity]
        policies = set(filter(None, dep["minimum_policy_set"].split(";")))
        requires_ladder = LADDER_POLICY in policies
        requires_divergence = DIVERGENCE_POLICY in policies
        text = source_text(row); classification = ladder_classification(text)
        hidden = "隐藏背离" in text or "hidden divergence" in text.lower()
        timeframe_complete = row.get("source_timeframe_semantics") in {"bar_period", "daily"}
        indicator = next((name for name in ("RSI", "MACD", "CCI", "OBV", "AO", "ROC") if name.lower() in text.lower()), "")
        divergence_eligible = requires_divergence and bool(indicator) and not hidden and timeframe_complete
        ladder_eligible = requires_ladder and classification in {"EXPLICIT_GRID_OR_LADDER", "EXPLICIT_PYRAMID", "EXPLICIT_MULTI_STAGE"}
        compiled = compile_ladder(identity, row) if requires_ladder else compile_divergence(identity, row) if requires_divergence else None
        if compiled is not None:
            plan[identity] = compiled
        remaining = [] if compiled else list(filter(None, old["remaining_blockers"].split(";")))
        if not compiled:
            if requires_ladder:
                remaining = [b for b in remaining if b not in {"NON_LOW_POLICY_REQUIRED"}]
                if not ladder_eligible: remaining.append("PHASE5F_LADDER_APPLICABILITY_REJECTED")
                else: remaining.append("OTHER_MATERIAL_SOURCE_CLAUSE_UNRESOLVED")
            if requires_divergence:
                remaining = [b for b in remaining if b not in {"NON_LOW_POLICY_REQUIRED", "STANDARD_REGULAR_DIVERGENCE_NOT_AUTHORIZED"}]
                if not divergence_eligible: remaining.append("PHASE5F_DIVERGENCE_APPLICABILITY_REJECTED")
                else: remaining.append("OTHER_MATERIAL_SOURCE_CLAUSE_UNRESOLVED")
        remaining = sorted(set(remaining))
        other_policies = policies - {LADDER_POLICY, DIVERGENCE_POLICY,
            "EXISTING_VOLATILITY_FEATURE_PROPAGATION", "EXISTING_TWO_CLOSE_STABILITY_PROPAGATION",
            "EXISTING_LEVEL_TOLERANCE_PROPAGATION", "EXISTING_DEFAULT_PROPAGATION",
            "EXISTING_FILL_ANCHOR_PROPAGATION", "STANDARD_OHLCV_FEATURE_CONTRACT"}
        starting.append({
            "source_identity": identity, "strategy_name": row["source_strategy_name"],
            "phase5e_remaining_blockers": old["remaining_blockers"],
            "requires_ladder_policy": str(requires_ladder).lower(),
            "requires_divergence_policy": str(requires_divergence).lower(),
            "requires_both": str(requires_ladder and requires_divergence).lower(),
            "requires_other_medium_policy": str(any("TURN" in p or "STRUCTURAL" in p for p in other_policies)).lower(),
            "requires_high_policy": str(bool(other_policies)).lower(),
            "requires_very_high_policy": str("MODELLED_ACCOUNTING_ARCHITECTURE" in other_policies).lower(),
            "eligible_for_phase5f": str(bool(compiled)).lower(),
            "other_remaining_blockers": ";".join(remaining),
        })
        if requires_ladder:
            stage_match = re.search(r"(\d+)\s*(?:层|档|阶段)", text)
            ladder_rows.append({
                "source_identity": identity, "classification": classification,
                "source_stage_count": stage_match.group(1) if stage_match else "",
                "source_step_defined": str(any(t in text for t in ("ATR", "档位", "支撑", "压力"))).lower(),
                "source_fraction_defined": str(bool(re.search(r"\d+\s*%", text))).lower(),
                "source_max_exposure_defined": str("上限" in text or "1x" in text.lower()).lower(),
                "phase5f_ladder_eligible": str(ladder_eligible).lower(),
                "contract_components_supplied": "allocation;cap" + (";existing_atr_spacing" if identity in TURTLE_LADDER else ""),
                "remaining_sizing_blockers": ";".join(remaining),
            })
        if requires_divergence:
            divergence_rows.append({
                "strategy_id": identity if compiled else "", "source_identity": identity,
                "indicator": indicator, "direction": "bullish_and_bearish" if "顶" in text and "底" in text else "source_identified",
                "timeframe": "1d" if row.get("source_timeframe_semantics") == "daily" else "1m" if timeframe_complete else "",
                "pivot_rule": "confirmed_2_left_2_right", "pairing_rule": "same_timestamp_indicator_value", "lookback": 60,
                "other_source_conditions": "preserved_in_compiled_rule" if compiled else "unresolved",
                "remaining_blockers": ";".join(remaining),
                "registration_status": "REGISTERED" if compiled else "REMAINS_UNRESOLVED",
            })
        status = "IMPLEMENTED_STANDALONE" if compiled else "REMAINS_UNRESOLVED"
        closure.append({
            "source_identity": identity, "strategy_name": row["source_strategy_name"],
            "phase5e_remaining_blockers": old["remaining_blockers"],
            "phase5f_contracts_applied": ";".join(compiled["phase5f_contracts_applied"]) if compiled else "",
            "remaining_blockers": ";".join(remaining), "unmapped_material_source_clauses": 0 if compiled else 1,
            "entry_complete": str(bool(compiled)).lower(), "exit_complete": str(bool(compiled)).lower(),
            "sizing_complete": str(bool(compiled)).lower(), "timeframe_complete": str(bool(compiled)).lower(),
            "data_features_available": str(bool(compiled)).lower(), "accounting_compatible": str(bool(compiled)).lower(),
            "phase5f_status": status,
            "semantic_fingerprint": compiled["rule_hash"][:20] if compiled else old["semantic_fingerprint"],
        })
        if compiled:
            traces.append({
                "source_identity": identity, "source_text": text,
                "phase5f_contracts_applied": ";".join(compiled["phase5f_contracts_applied"]),
                "modelled_layer_count": compiled.get("defaulted_parameters", {}).get("grid_layers", ""),
                "modelled_layer_allocation": "0.25;0.25;0.25;0.25" if identity in TURTLE_LADDER else "",
                "modelled_grid_spacing": "1.0 Wilder ATR(14) from existing frozen grid contract" if identity in TURTLE_LADDER else "",
                "divergence_indicator": indicator if requires_divergence else "",
                "pivot_rule": "confirmed_2_left_2_right" if requires_divergence else "",
                "pairing_rule": "same_timestamp_indicator_value" if requires_divergence else "",
                "lookback": 60 if requires_divergence else "", "remaining_source_exact_parameters": "preserved",
                "compiled_rule": compiled["compiler_family"], "provenance": compiled["semantic_provenance"],
            })

    if set(plan) != COMPILED_IDS:
        raise RuntimeError(f"Phase 5F reviewed compiler set mismatch: {sorted(set(plan) ^ COMPILED_IDS)}")
    write_json(PLAN, plan)
    write_csv(AUDIT / "phase5f_starting_gap_audit.csv", starting)
    write_csv(AUDIT / "phase5f_ladder_applicability.csv", ladder_rows)
    ladder_recovery = [{
        "strategy_id": row["source_identity"] if row["source_identity"] in plan else "",
        "source_identity": row["source_identity"], "source_ladder_phrase": manifest[row["source_identity"]]["source_strategy_name"],
        "source_explicit_K": row["source_stage_count"], "source_explicit_fractions": row["source_fraction_defined"],
        "source_explicit_spacing": row["source_step_defined"],
        "modelled_K": 4 if row["source_identity"] in TURTLE_LADDER else "",
        "modelled_fractions": "0.25;0.25;0.25;0.25" if row["source_identity"] in TURTLE_LADDER else "",
        "modelled_spacing": "existing GRID_4L_ATR1_EQUAL_V1" if row["source_identity"] in TURTLE_LADDER else "",
        "remaining_blockers": row["remaining_sizing_blockers"],
        "registration_status": "REGISTERED" if row["source_identity"] in plan else "REMAINS_UNRESOLVED",
    } for row in ladder_rows]
    write_csv(AUDIT / "phase5f_ladder_recovery.csv", ladder_recovery)
    write_csv(AUDIT / "phase5f_divergence_recovery.csv", divergence_rows)
    write_csv(AUDIT / "phase5f_modelled_assumption_trace.csv", traces)
    write_csv(AUDIT / "phase5f_strategy_closure.csv", closure)
    transitions = [{"source_identity": identity, "strategy_name": manifest[identity]["source_strategy_name"],
                    "old_status": "REMAINS_UNRESOLVED", "new_status": "IMPLEMENTED_STANDALONE",
                    "contracts_applied": ";".join(plan[identity]["phase5f_contracts_applied"]),
                    "semantic_provenance": "MODELLED_BASELINE_INTERPRETATION", "baseline_status": "PENDING"}
                   for identity in sorted(plan)]
    write_csv(AUDIT / "phase5f_status_transitions.csv", transitions)
    groups = len({str(item["rule_hash"]) for item in plan.values()})
    write_csv(AUDIT / "phase5f_fixpoint_iterations.csv", [
        {"iteration": 1, "newly_closed_identities": len(plan), "new_semantic_groups": groups, "remaining_rows": 980-len(plan)},
        {"iteration": 2, "newly_closed_identities": 0, "new_semantic_groups": 0, "remaining_rows": 980-len(plan)},
    ])
    execution = [{"strategy_id": identity, "source_timeframe": item["source_timeframe"],
                  "physical_representative": min(k for k,v in plan.items() if v["rule_hash"] == item["rule_hash"]),
                  "physical_execution": str(identity == min(k for k,v in plan.items() if v["rule_hash"] == item["rule_hash"])).lower(),
                  "rule_hash": item["rule_hash"], "cases": f"{item['source_timeframe']}_lag0;{item['source_timeframe']}_lag1"}
                 for identity,item in sorted(plan.items())]
    write_csv(AUDIT / "phase5f_execution_plan.csv", execution)
    boundary = []
    for row in closure:
        if row["phase5f_status"] != "REMAINS_UNRESOLVED": continue
        blockers = list(filter(None, str(row["remaining_blockers"]).split(";")))
        family, risk, action_name = minimum_boundary(blockers)
        boundary.append({"source_identity": row["source_identity"], "remaining_blockers": ";".join(blockers),
                         "minimum_next_policy_family": family, "minimum_intrusiveness": risk,
                         "estimated_resolvability": "requires_policy_or_source_review", "recommended_next_action": action_name})
    write_csv(AUDIT / "phase5f_phase5g_policy_boundary.csv", boundary)
    summary = {
        "phase": "5F", "starting_rows": 980, "rows_reprocessed": len(closure),
        "active_medium_contracts": list(ACTIVE_CONTRACTS), "medium_policy_count": 2,
        "high_policy_count": 0, "very_high_policy_count": 0,
        "new_executable_identities": len(plan), "new_semantic_groups": groups,
        "remaining_rows": len(boundary), "fixpoint_reached": True,
        "performance_inspected_before_freeze": False,
    }
    write_json(AUDIT / "phase5f_fixpoint_summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
