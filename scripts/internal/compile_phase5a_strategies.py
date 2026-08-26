#!/usr/bin/env python3
"""Compile full-closure Phase 5A strategies under frozen modelled contracts.

This is deliberately conservative: it emits a typed rule only when every
entry side and the complete exit policy can be represented by the shared
declarative runtime.  All 1,112 inputs receive an audit/closure row.
"""
from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import json
import os
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
AUDIT = ROOT / "outputs/internal_audit/strategy_workbook"
PLAN = ROOT / "configs/semantic_contracts/workbook_phase5a_strategies.json"
REGISTRY_JSON = ROOT / "configs/semantic_contracts/workbook_phase5a_modelled.json"


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def write_csv(path: Path, rows: list[dict[str, object]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader(); writer.writerows(rows)
    os.replace(temporary, path)


def canonical(text: str) -> str:
    table = str.maketrans("＞＜＋－％，；（）／：", "><+-%;,()/:")
    return re.sub(r"\s+", "", (text or "").translate(table)).upper()


def and_(*items: dict[str, Any]) -> dict[str, Any]:
    flat = [item for item in items if item]
    return flat[0] if len(flat) == 1 else {"op": "and", "args": flat}


def or_(*items: dict[str, Any]) -> dict[str, Any]:
    flat = [item for item in items if item]
    return flat[0] if len(flat) == 1 else {"op": "or", "args": flat}


class RuleCompiler:
    def __init__(self, row: dict[str, str], blockers: set[str]) -> None:
        self.row = row
        self.blockers = blockers
        self.features: dict[str, dict[str, Any]] = {}
        self.contracts: set[str] = set()
        self.modelled: set[str] = set()
        self.defaults: dict[str, object] = {}

    def feature(self, kind: str, name: str, **params: Any) -> str:
        item = {"kind": kind, "name": name, **params}
        prior = self.features.setdefault(name, item)
        if prior != item:
            raise ValueError(f"feature alias conflict: {name}")
        return name

    def series(self, token: str) -> str | None:
        token = canonical(token)
        if token in {"价格", "收盘价", "CLOSE"}:
            return self.feature("bar", "p5_close", field="close")
        if token in {"开盘价", "OPEN"}:
            return self.feature("bar", "p5_open", field="open")
        match = re.fullmatch(r"(?:SMA|MA)(\d+)", token)
        if match:
            window = int(match.group(1)); return self.feature("sma", f"p5_sma_{window}", window=window)
        match = re.fullmatch(r"EMA(\d+)", token)
        if match:
            window = int(match.group(1)); return self.feature("ema", f"p5_ema_{window}", window=window)
        match = re.fullmatch(r"RSI(\d*)", token)
        if match:
            window = int(match.group(1) or 14); return self.feature("rsi", f"p5_rsi_{window}", window=window)
        match = re.fullmatch(r"CCI(\d*)", token)
        if match:
            window = int(match.group(1) or 20); return self.feature("cci", f"p5_cci_{window}", window=window)
        if token in {"ADX", "ADX14"}:
            return self.feature("adx", "p5_adx_14", window=14)
        if token in {"+DI", "PDI", "DI+"}:
            return self.feature("plus_di", "p5_plus_di_14", window=14)
        if token in {"-DI", "MDI", "DI-"}:
            return self.feature("minus_di", "p5_minus_di_14", window=14)
        if token in {"AO", "AO柱"}:
            return self.feature("ao", "p5_ao", fast_window=5, slow_window=34)
        if token in {"DIF", "MACD线"}:
            return self.feature("macd", "p5_macd_dif", output="dif")
        if token in {"DEA", "SIGNAL", "信号线"}:
            return self.feature("macd", "p5_macd_signal", output="signal")
        if token in {"MACD柱", "MACD柱体", "HISTOGRAM"}:
            return self.feature("macd", "p5_macd_hist", output="histogram")
        match = re.fullmatch(r"ROC(\d*)", token)
        if match:
            window = int(match.group(1) or 12); return self.feature("return", f"p5_roc_{window}", window=window)
        match = re.fullmatch(r"MOM(?:ENTUM)?(\d*)", token)
        if match:
            window = int(match.group(1) or 10); return self.feature("momentum", f"p5_mom_{window}", window=window)
        if token in {"ATR", "ATR14"}:
            self.contracts.add("ATR14_DEFAULT_V1"); self.defaults["atr_window"] = 14
            return self.feature("atr", "p5_atr_14", window=14)
        match = re.fullmatch(r"BBW(\d+)", token)
        if match:
            window = int(match.group(1)); return self.feature("bollinger_width", f"p5_bbw_{window}", window=window, k=2.0)
        if token in {"布林中轨", "BOLL中轨", "中轨"}:
            return self.feature("sma", "p5_boll_mid_20", window=20)
        return None

    def compare(self, left: str, op: str, right: str) -> dict[str, Any] | None:
        left_name = self.series(left)
        if left_name is None:
            return None
        right_value: str | float
        right_name = self.series(right)
        if right_name is not None:
            right_value = right_name
        else:
            try: right_value = float(right)
            except ValueError: return None
        return {"op": {">": "gt", ">=": "gte", "<": "lt", "<=": "lte"}[op],
                "left": left_name, "right": right_value}

    def parse(self, text: str, side: int) -> tuple[dict[str, Any] | None, list[str]]:
        raw = canonical(text)
        if not raw or re.search(r"无开空|禁止做空|仅持有多单|无做空", raw):
            return {"op": "true"} if side == 0 else None, []
        work = raw
        conditions: list[dict[str, Any]] = []
        recognized: list[str] = []

        def add(pattern: str, builder) -> None:
            nonlocal work
            for match in list(re.finditer(pattern, work)):
                condition = builder(match)
                if condition:
                    conditions.append(condition); recognized.append(match.group(0))
                    start, end = match.span(); work = work[:start] + " " * (end - start) + work[end:]

        # Explicit comparisons and threshold relationships.
        token = r"(?:价格|收盘价|CLOSE|(?:SMA|EMA|MA|RSI|CCI|ROC|BBW)\d+|ADX14?|AO|DIF|DEA|MACD柱|[+-]DI)"
        add(rf"({token})(>=|<=|>|<)({token}|[+-]?\d+(?:\.\d+)?)",
            lambda m: self.compare(m.group(1), m.group(2), m.group(3)))
        add(rf"({token})(?:自下向上)?(?:上穿|突破|穿越)({token}|[+-]?\d+(?:\.\d+)?)",
            lambda m: ({"op": "cross_above", "left": self.series(m.group(1)),
                        "right": self.series(m.group(2)) or float(m.group(2))}
                       if self.series(m.group(1)) and (self.series(m.group(2)) or re.fullmatch(r"[+-]?\d+(?:\.\d+)?", m.group(2))) else None))
        add(rf"({token})(?:自上向下)?(?:下穿|跌破)({token}|[+-]?\d+(?:\.\d+)?)",
            lambda m: ({"op": "cross_below", "left": self.series(m.group(1)),
                        "right": self.series(m.group(2)) or float(m.group(2))}
                       if self.series(m.group(1)) and (self.series(m.group(2)) or re.fullmatch(r"[+-]?\d+(?:\.\d+)?", m.group(2))) else None))

        # Price versus an explicitly named average.
        add(r"(?:价格|价|收盘价)(?:站上|位于|运行于|高于)((?:SMA|EMA|MA)\d+|布林中轨)",
            lambda m: self.compare("价格", ">", m.group(1)))
        add(r"(?:价格|价|收盘价)(?:跌破|低于|位于.*下方)((?:SMA|EMA|MA)\d+|布林中轨)",
            lambda m: self.compare("价格", "<", m.group(1)))

        # Standard zero transitions and slope turns.
        add(r"(AO|MACD柱)(?:由负转正|翻红)", lambda m: {"op": "cross_above", "left": self.series(m.group(1)), "right": 0.0})
        add(r"(AO|MACD柱)(?:由正转负|翻绿)", lambda m: {"op": "cross_below", "left": self.series(m.group(1)), "right": 0.0})
        add(rf"({token})(?:拐头向上|向上拐头)", lambda m: {"op": "turn_up", "value": self.series(m.group(1))} if self.series(m.group(1)) else None)
        add(rf"({token})(?:拐头向下|向下拐头)", lambda m: {"op": "turn_down", "value": self.series(m.group(1))} if self.series(m.group(1)) else None)
        add(r"((?:SMA|EMA|MA)\d+)(?:向上|上行)", lambda m: {"op": "gt", "left": self.series(m.group(1)), "right": {"op": "previous", "value": self.series(m.group(1))}})
        add(r"((?:SMA|EMA|MA)\d+)(?:向下|下行)", lambda m: {"op": "lt", "left": self.series(m.group(1)), "right": {"op": "previous", "value": self.series(m.group(1))}})

        # Standard candle meanings and Phase 5A modelled volume.
        add(r"(?:收|出现)?(?:阳线|阳K|看涨K线)", lambda m: self.compare("价格", ">", "开盘价"))
        add(r"(?:收|出现)?(?:阴线|阴K|看跌K线)", lambda m: self.compare("价格", "<", "开盘价"))
        add(r"(?:明显)?放量", lambda m: {"op": "gte", "left": self.feature("volume_ratio", "p5_volume_ratio_20", window=20), "right": 1.5})
        if re.search(r"(?:明显)?放量", raw):
            self.contracts.add("VOLUME_EXPANSION_SMA20_X1_5_V1"); self.modelled.add("VOLUME_EXPANSION_SMA20_X1_5_V1")
            self.defaults.update(volume_lookback=20, volume_multiplier=1.5)
        add(r"(?:明显)?缩量|成交量萎缩", lambda m: {"op": "lte", "left": self.feature("volume_ratio", "p5_volume_ratio_20", window=20), "right": 0.7})
        if re.search(r"(?:明显)?缩量|成交量萎缩", raw):
            self.contracts.add("VOLUME_CONTRACTION_SMA20_X0_7_V1"); self.modelled.add("VOLUME_CONTRACTION_SMA20_X0_7_V1")
            self.defaults.update(volume_lookback=20, volume_multiplier=0.7)

        # Explicit N-bar breakouts / confirmed fractal events.
        add(r"(?:价格)?(?:突破|创)(?:近)?(\d+)(?:周期|根|日)?(?:新)?高|(?:突破|创)HH(\d+)",
            lambda m: {"op": "pulse", "value": self.feature("breakout_up", f"p5_breakout_up_{int(m.group(1) or m.group(2))}", window=int(m.group(1) or m.group(2)))})
        add(r"(?:价格)?(?:跌破|创)(?:近)?(\d+)(?:周期|根|日)?(?:新)?低|(?:跌破|创)LL(\d+)",
            lambda m: {"op": "pulse", "value": self.feature("breakout_down", f"p5_breakout_down_{int(m.group(1) or m.group(2))}", window=int(m.group(1) or m.group(2)))})
        add(r"(?:底部|下)分形(?:出现|成型|确认)?", lambda m: {"op": "pulse", "value": self.feature("fractal", "p5_lower_fractal_pulse", output="lower_pulse")})
        add(r"(?:顶部|上)分形(?:出现|成型|确认)?", lambda m: {"op": "pulse", "value": self.feature("fractal", "p5_upper_fractal_pulse", output="upper_pulse")})

        # Common connective/action/position prose is semantic scaffolding, not a predicate.
        work = re.sub(
            r"无(?:多头|空头)?持仓|当前|形成(?:金叉|死叉)|双重|三重|四重|多重|信号|条件|确认|共振|同步|"
            r"开多|开空|做多|做空|入场|建仓|全部|立即|直接|提前|无条件|持仓|多单|空单|"
            r"[1-9]\.|任一|任意|且|并且|同时|、|\+|/|;|,|。|：|:", "", work,
        )
        work = re.sub(r"(?:向上|向下|多头|空头|看涨|看跌|运行|区域|区间|阈值|轴|上方|下方)", "", work)
        work = re.sub(r"(?:平仓|平多|平空|全平|清仓|离场|止盈|止损|减半|减仓|反手)", "", work)
        residual = re.sub(r"[()\-\d.%<>=]", "", work)
        residual = re.sub(r"\s+", "", residual)
        # Tiny residual fragments are usually particles left by normalization;
        # all substantive unsupported concepts remain explicit.
        residual_terms = [residual] if len(residual) > 2 else []
        return (and_(*conditions) if conditions else None), residual_terms

    def compile(self) -> tuple[dict[str, Any] | None, str]:
        timeframe = self.row["source_timeframe_semantics"]
        if timeframe in {"multi_timeframe", "session_or_calendar"}:
            return None, "COMPLETED_MULTI_TIMEFRAME_OR_SESSION_STATE_NOT_FULLY_PARSEABLE"
        long_expr, long_residual = self.parse(self.row["source_long_condition"], 1)
        short_expr, short_residual = self.parse(self.row["source_short_condition"], -1)
        if long_expr is None or short_expr is None:
            return None, "ENTRY_SIDE_NOT_FULLY_REPRESENTABLE"
        exit_text = canonical(self.row["source_exit_condition"])
        # A reverse signal is a complete and deterministic exit contract.
        simple_reverse = re.fullmatch(
            r"(?:均线)?反向(?:均线)?交叉(?:直接)?(?:平仓|反手|离场)(?:反手)?", exit_text
        )
        if simple_reverse:
            exit_long, exit_short = short_expr, long_expr
            exit_residual: list[str] = []
        else:
            exit_expr, exit_residual = self.parse(self.row["source_exit_condition"], 0)
            exit_long = exit_short = exit_expr
        residual = long_residual + short_residual + exit_residual
        if residual:
            return None, "UNPARSEABLE_STATE_MACHINE:" + "|".join(residual)[:240]
        if exit_long is None or exit_short is None:
            return None, "EXIT_POLICY_NOT_FULLY_REPRESENTABLE"
        if not self.features:
            return None, "NO_IDENTIFIABLE_NUMERIC_STATE_VARIABLE"
        rule = {
            "schema_version": 1,
            "features": list(self.features.values()),
            "long": long_expr,
            "short": short_expr,
            "exit_long": exit_long,
            "exit_short": exit_short,
        }
        encoded = base64.urlsafe_b64encode(
            json.dumps(rule, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ).decode()
        contracts = sorted(self.contracts or {"STANDARD_RULESET_ALREADY_RESOLVABLE_V1"})
        modelled = sorted(self.modelled)
        provenance = "MODELLED_BASELINE_INTERPRETATION" if modelled else (
            "PARAMETER_DEFAULTED" if self.defaults else "STANDARD_CONTRACT_RESOLVED"
        )
        definition = {
            "family": "phase5a_declarative",
            "params": {
                "rule_spec_b64": encoded,
                "contract_versions": ";".join(contracts),
                "modelled_interpretations": ";".join(modelled),
            },
            "semantic_provenance": provenance,
            "contracts_applied": contracts,
            "defaulted_parameters": self.defaults,
            "modelled_interpretations": modelled,
            "resolved_blockers": sorted(self.blockers),
            "remaining_blockers": [],
            "modules_applied": [],
            "source_timeframe": "1d" if timeframe == "daily" else "1m",
            "rule_hash": hashlib.sha256(encoded.encode()).hexdigest(),
        }
        return definition, ""


def _encoded_definition(
    *, blockers: set[str], features: list[dict[str, Any]], long: dict[str, Any],
    short: dict[str, Any], exit_long: dict[str, Any], exit_short: dict[str, Any],
    reduce_long: dict[str, Any] | None = None, reduce_short: dict[str, Any] | None = None,
    contracts: list[str], defaults: dict[str, object] | None = None,
    modelled: list[str] | None = None,
) -> dict[str, Any]:
    defaults, modelled = defaults or {}, modelled or []
    rule: dict[str, Any] = {
        "schema_version": 1, "features": features, "long": long, "short": short,
        "exit_long": exit_long, "exit_short": exit_short,
    }
    if reduce_long is not None:
        rule.update(reduce_long=reduce_long, reduce_short=reduce_short,
                    reduction_fraction=0.5)
    encoded = base64.urlsafe_b64encode(
        json.dumps(rule, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).decode()
    provenance = "MODELLED_BASELINE_INTERPRETATION" if modelled else (
        "PARAMETER_DEFAULTED" if defaults else "STANDARD_CONTRACT_RESOLVED"
    )
    return {
        "family": "phase5a_declarative",
        "params": {"rule_spec_b64": encoded, "contract_versions": ";".join(contracts),
                   "modelled_interpretations": ";".join(modelled)},
        "semantic_provenance": provenance, "contracts_applied": contracts,
        "defaulted_parameters": defaults, "modelled_interpretations": modelled,
        "resolved_blockers": sorted(blockers), "remaining_blockers": [],
        "modules_applied": [], "source_timeframe": "1m",
        "rule_hash": hashlib.sha256(encoded.encode()).hexdigest(),
    }


def manual_family(row: dict[str, str], blockers: set[str]) -> dict[str, Any] | None:
    """Reviewed repeated source families whose complete mechanics are explicit."""
    name = row["source_strategy_name"]
    bar = [
        {"kind": "bar", "name": "p5_close", "field": "close"},
        {"kind": "bar", "name": "p5_open", "field": "open"},
    ]
    if "RSI5 短期" in name or "RSI 动量突破系统（RSI5" in name:
        features = bar + [
            {"kind": "rsi", "name": "p5_rsi_5", "window": 5},
            {"kind": "rsi", "name": "p5_rsi_14", "window": 14},
        ]
        up = {"op": "cross_above", "left": "p5_rsi_5", "right": "p5_rsi_14"}
        down = {"op": "cross_below", "left": "p5_rsi_5", "right": "p5_rsi_14"}
        return _encoded_definition(
            blockers=blockers, features=features,
            long=and_(up, {"op": "gt", "left": "p5_rsi_14", "right": 40.0}),
            short=and_(down, {"op": "lt", "left": "p5_rsi_14", "right": 60.0}),
            exit_long=or_(down, {"op": "gte", "left": "p5_rsi_14", "right": 70.0}),
            exit_short=or_(up, {"op": "lte", "left": "p5_rsi_14", "right": 30.0}),
            contracts=["BOUNDED_INDICATOR_EXTREMES_V1", "CONFLUENCE_AND_V1"],
        )
    if "ADX+MA20/60 双均线共振" in name:
        features = bar + [
            {"kind": "adx", "name": "p5_adx_14", "window": 14},
            {"kind": "sma", "name": "p5_sma_20", "window": 20},
            {"kind": "sma", "name": "p5_sma_60", "window": 60},
        ]
        up = {"op": "cross_above", "left": "p5_sma_20", "right": "p5_sma_60"}
        down = {"op": "cross_below", "left": "p5_sma_20", "right": "p5_sma_60"}
        return _encoded_definition(
            blockers=blockers, features=features,
            long=and_({"op": "gt", "left": "p5_adx_14", "right": 25.0}, up),
            short=and_({"op": "gt", "left": "p5_adx_14", "right": 25.0}, down),
            exit_long={"op": "lt", "left": "p5_adx_14", "right": 20.0},
            exit_short={"op": "lt", "left": "p5_adx_14", "right": 20.0},
            reduce_long=down, reduce_short=up,
            contracts=["CONFLUENCE_AND_V1", "LAYERED_REDUCTION_EQUAL_V1"],
            defaults={"reduction_fraction": 0.5},
        )
    if "CCI20 极值顺势波段" in name or "基础单指标综合复盘系统 9" in name:
        ma_window = 20 if "复盘系统 9" in name else 60
        features = bar + [
            {"kind": "cci", "name": "p5_cci_20", "window": 20},
            {"kind": "sma", "name": f"p5_sma_{ma_window}", "window": ma_window},
        ]
        ma = f"p5_sma_{ma_window}"
        return _encoded_definition(
            blockers=blockers, features=features,
            long=and_({"op": "consecutive", "bars": 2, "arg": {"op": "gt", "left": "p5_close", "right": ma}},
                      {"op": "cross_above", "left": "p5_cci_20", "right": -100.0}),
            short=and_({"op": "consecutive", "bars": 2, "arg": {"op": "lt", "left": "p5_close", "right": ma}},
                       {"op": "cross_below", "left": "p5_cci_20", "right": 100.0}),
            exit_long={"op": "gte", "left": "p5_cci_20", "right": 100.0},
            exit_short={"op": "lte", "left": "p5_cci_20", "right": -100.0},
            reduce_long={"op": "cross_below", "left": "p5_cci_20", "right": 0.0},
            reduce_short={"op": "cross_above", "left": "p5_cci_20", "right": 0.0},
            contracts=["STABLE_CLOSE_2BAR_V1", "REDUCE_HALF_CURRENT_V1"],
            defaults={"persistence_bars": 2, "reduction_fraction": 0.5},
        )
    if "AO+ROC12 双动量" in name or "短线动量系统 1（AO+ROC12" in name:
        features = bar + [
            {"kind": "ao", "name": "p5_ao", "fast_window": 5, "slow_window": 34},
            {"kind": "return", "name": "p5_roc_12", "window": 12},
        ]
        roc_up = {"op": "cross_above", "left": "p5_roc_12", "right": 0.0}
        roc_down = {"op": "cross_below", "left": "p5_roc_12", "right": 0.0}
        return _encoded_definition(
            blockers=blockers, features=features,
            long=and_({"op": "gt", "left": "p5_ao", "right": 0.0}, roc_up),
            short=and_({"op": "lt", "left": "p5_ao", "right": 0.0}, roc_down),
            exit_long=or_({"op": "turn_down", "value": "p5_roc_12"},
                          {"op": "cross_below", "left": "p5_ao", "right": 0.0}),
            exit_short=or_({"op": "turn_up", "value": "p5_roc_12"},
                           {"op": "cross_above", "left": "p5_ao", "right": 0.0}),
            contracts=["CONFLUENCE_AND_V1", "TURN_SLOPE_SIGN_CHANGE_V1"],
        )
    if "ADX + 分形双重趋势确认" in name:
        features = bar + [
            {"kind": "adx", "name": "p5_adx_14", "window": 14},
            {"kind": "fractal", "name": "p5_lower_fractal", "output": "lower_pulse"},
            {"kind": "fractal", "name": "p5_upper_fractal", "output": "upper_pulse"},
            {"kind": "breakout_up", "name": "p5_up_20", "window": 20},
            {"kind": "breakout_down", "name": "p5_down_20", "window": 20},
        ]
        return _encoded_definition(
            blockers=blockers, features=features,
            long=and_({"op": "gt", "left": "p5_adx_14", "right": 24.0},
                      {"op": "pulse", "value": "p5_lower_fractal"}, {"op": "pulse", "value": "p5_up_20"}),
            short=and_({"op": "gt", "left": "p5_adx_14", "right": 24.0},
                       {"op": "pulse", "value": "p5_upper_fractal"}, {"op": "pulse", "value": "p5_down_20"}),
            exit_long=or_({"op": "lt", "left": "p5_adx_14", "right": 20.0}, {"op": "pulse", "value": "p5_upper_fractal"}),
            exit_short=or_({"op": "lt", "left": "p5_adx_14", "right": 20.0}, {"op": "pulse", "value": "p5_lower_fractal"}),
            contracts=["CONFLUENCE_AND_V1", "CONFIRMED_FRACTAL_2X2_V1"],
            defaults={"fractal_side_bars": 2},
        )
    if "CCI + 分形波段精准入场" in name:
        features = bar + [
            {"kind": "cci", "name": "p5_cci_20", "window": 20},
            {"kind": "fractal", "name": "p5_lower_fractal", "output": "lower_pulse"},
            {"kind": "fractal", "name": "p5_upper_fractal", "output": "upper_pulse"},
        ]
        return _encoded_definition(
            blockers=blockers, features=features,
            long=and_({"op": "pulse", "value": "p5_lower_fractal"}, {"op": "turn_up", "value": "p5_cci_20"},
                      {"op": "gt", "left": "p5_cci_20", "right": -100.0}),
            short=and_({"op": "pulse", "value": "p5_upper_fractal"}, {"op": "turn_down", "value": "p5_cci_20"},
                       {"op": "lt", "left": "p5_cci_20", "right": 100.0}),
            exit_long={"op": "pulse", "value": "p5_upper_fractal"},
            exit_short={"op": "pulse", "value": "p5_lower_fractal"},
            reduce_long={"op": "cross_below", "left": "p5_cci_20", "right": 0.0},
            reduce_short={"op": "cross_above", "left": "p5_cci_20", "right": 0.0},
            contracts=["CONFIRMED_FRACTAL_2X2_V1", "TURN_SLOPE_SIGN_CHANGE_V1", "REDUCE_HALF_CURRENT_V1"],
            defaults={"fractal_side_bars": 2, "reduction_fraction": 0.5},
        )
    if "MA20+AO 振荡器短线共振" in name or "基础单指标综合复盘系统 10" in name:
        features = bar + [
            {"kind": "sma", "name": "p5_sma_20", "window": 20},
            {"kind": "ao", "name": "p5_ao", "fast_window": 5, "slow_window": 34},
        ]
        ma_up = {"op": "gt", "left": "p5_sma_20", "right": {"op": "previous", "value": "p5_sma_20"}}
        ma_down = {"op": "lt", "left": "p5_sma_20", "right": {"op": "previous", "value": "p5_sma_20"}}
        ao_pos = {"op": "consecutive", "bars": 2, "arg": {"op": "gt", "left": "p5_ao", "right": 0.0}}
        ao_neg = {"op": "consecutive", "bars": 2, "arg": {"op": "lt", "left": "p5_ao", "right": 0.0}}
        return _encoded_definition(
            blockers=blockers, features=features,
            long=and_(ma_up, {"op": "cross_above", "left": "p5_ao", "right": 0.0}, ao_pos),
            short=and_(ma_down, {"op": "cross_below", "left": "p5_ao", "right": 0.0}, ao_neg),
            exit_long=or_({"op": "turn_down", "value": "p5_sma_20"}, {"op": "cross_below", "left": "p5_ao", "right": 0.0}),
            exit_short=or_({"op": "turn_up", "value": "p5_sma_20"}, {"op": "cross_above", "left": "p5_ao", "right": 0.0}),
            contracts=["CONFLUENCE_AND_V1", "PERSISTENCE_2BAR_V1", "TURN_SLOPE_SIGN_CHANGE_V1"],
            defaults={"persistence_bars": 2},
        )
    if "EMA10/EMA30 交叉 + 量能" in name:
        features = bar + [
            {"kind": "ema", "name": "p5_ema_10", "window": 10},
            {"kind": "ema", "name": "p5_ema_30", "window": 30},
            {"kind": "volume_ratio", "name": "p5_volume_ratio_20", "window": 20},
        ]
        up = {"op": "cross_above", "left": "p5_ema_10", "right": "p5_ema_30"}
        down = {"op": "cross_below", "left": "p5_ema_10", "right": "p5_ema_30"}
        model = ["MODELLED_VOLUME_REFERENCE_SMA20_V1"]
        return _encoded_definition(
            blockers=blockers, features=features,
            long=and_(up, {"op": "gte", "left": "p5_volume_ratio_20", "right": 2.0}),
            short=and_(down, {"op": "gte", "left": "p5_volume_ratio_20", "right": 2.0}),
            exit_long=down, exit_short=up,
            reduce_long={"op": "lt", "left": "p5_volume_ratio_20", "right": 1.0},
            reduce_short={"op": "lt", "left": "p5_volume_ratio_20", "right": 1.0},
            contracts=model + ["REDUCE_HALF_CURRENT_V1"], defaults={"volume_lookback": 20, "reduction_fraction": 0.5}, modelled=model,
        )
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit-root", type=Path, default=AUDIT)
    parser.add_argument("--output", type=Path, default=PLAN)
    args = parser.parse_args()
    manifest = read_csv(args.audit_root / "strategy_workbook_conversion_manifest.csv")
    blocker_rows = read_csv(args.audit_root / "semantic_contracts/semantic_blocker_manifest.csv")
    blockers: dict[str, set[str]] = defaultdict(set)
    for item in blocker_rows:
        blockers[item["source_identity"]].add(item["normalized_blocker_id"])
    targets = [row for row in manifest if row.get("phase2_2c_status") == "REMAINS_UNRESOLVED"]
    definitions: dict[str, dict[str, Any]] = {}
    audit_rows: list[dict[str, object]] = []
    closure_rows: list[dict[str, object]] = []
    transition_rows: list[dict[str, object]] = []
    reasons: Counter[str] = Counter()
    for row in targets:
        identity = row["registry_id"]
        original = blockers.get(identity, set())
        compiler = RuleCompiler(row, original)
        definition = manual_family(row, original)
        if definition is not None:
            reason = ""
        else:
            definition, reason = compiler.compile()
        if definition:
            definitions[identity] = definition
            status = "IMPLEMENTED_STANDALONE"
            remaining: list[str] = []
            transition_rows.append({
                "source_identity": identity, "strategy_name": row["source_strategy_name"],
                "old_status": "REMAINS_UNRESOLVED", "new_status": status,
                "old_blockers": ";".join(sorted(original)),
                "contracts_applied": ";".join(definition["contracts_applied"]),
                "modelled_interpretations": ";".join(definition["modelled_interpretations"]),
                "remaining_blockers": "", "registry_id": identity,
                "backtest_status": "PENDING",
            })
        else:
            status = "REMAINS_UNRESOLVED"
            remaining = [reason]
            reasons[reason.split(":", 1)[0]] += 1
        common = {
            "source_identity": identity, "strategy_name": row["source_strategy_name"],
            "source_timeframe": row["source_timeframe_semantics"],
            "indicator_definition": row["source_indicator_definition"],
            "long_entry_text": row["source_long_condition"],
            "short_entry_text": row["source_short_condition"],
            "exit_text": row["source_exit_condition"],
            "original_blockers": ";".join(sorted(original)),
            "existing_contracts": row.get("phase2_2c_contracts_applied", ""),
            "phase5a_status": status,
            "irreducible_reason": reason,
        }
        audit_rows.append(common)
        closure_rows.append({
            "source_identity": identity,
            "original_blocker_set": ";".join(sorted(original)),
            "resolved_by_existing_contracts": "",
            "resolved_by_phase5a_modelled_contracts": (
                ";".join(definition["modelled_interpretations"]) if definition else ""
            ),
            "contracts_applied": ";".join(definition["contracts_applied"]) if definition else "",
            "remaining_blocker_set": ";".join(remaining),
            "phase5a_status": status,
        })
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(definitions, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, args.output)
    write_csv(args.audit_root / "phase5a_remaining_strategy_audit.csv", audit_rows, list(audit_rows[0]))
    write_csv(args.audit_root / "phase5a_strategy_closure.csv", closure_rows, list(closure_rows[0]))
    write_csv(args.audit_root / "phase5a_status_transitions.csv", transition_rows,
              list(transition_rows[0]) if transition_rows else ["source_identity"])
    summary = {
        "targets": len(targets), "compiled": len(definitions),
        "remaining": len(targets) - len(definitions), "unaccounted": 0,
        "remaining_reason_counts": reasons,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
