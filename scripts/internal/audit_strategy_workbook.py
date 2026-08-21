#!/usr/bin/env python3
"""Read-only OOXML audit and reconciliation for ``时序策略.xlsx``.

The workbook is a source specification, never a runtime dependency. Every
non-header row is preserved verbatim in exactly one manifest row. Only rules
listed in ``IMPLEMENTED`` have been reviewed as exact mappings to the existing
feature/strategy contracts; regex classification only explains blockers.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import zipfile
from collections import Counter
from pathlib import Path
from xml.etree import ElementTree as ET


NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
SOURCE_COLUMNS = (
    "source_sheet", "source_sheet_index", "source_row", "source_strategy_number",
    "source_strategy_name", "source_indicator_definition", "source_long_condition",
    "source_short_condition", "source_exit_condition", "registry_id",
)
AUDIT_COLUMNS = (
    "semantic_class", "final_status", "implementation_family", "required_data",
    "source_timeframe_semantics", "adaptation_mode", "automatic_conversion_safe",
    "manual_review_required", "blocking_reason", "package_path", "config_path",
    "registry_status", "structure_status", "smoke_status", "backtest_status",
)
PHASE2_1_COLUMNS = (
    "previous_phase2_status", "phase2_1_status", "unblocked_by_capability",
    "capability_added", "original_strategy_status", "minute_conversion_status",
    "manual_review_subcategory", "terminal_blocker",
)
PHASE2_2B_COLUMNS = (
    "phase2_2b_status", "semantic_provenance", "contracts_applied",
    "defaulted_parameter_count", "defaulted_parameters",
)
HEADERS = SOURCE_COLUMNS + AUDIT_COLUMNS + PHASE2_1_COLUMNS + PHASE2_2B_COLUMNS

# Reviewed mappings only. Parameters are compiled into packages; neither a
# strategy nor a runner loads the workbook at runtime.
IMPLEMENTED: dict[str, dict[str, object]] = {
    "xlsx_s1_0002": {"family": "sma_crossover", "params": {"fast_window": 20, "slow_window": 60, "maximum_holding_bars": 40}},
    "xlsx_s1_0003": {"family": "sma_price_cross", "params": {"window": 60}, "source_timeframe": "1d"},
    "xlsx_s1_0004": {"family": "ema_crossover", "params": {"fast_window": 12, "slow_window": 26}, "source_timeframe": "1d"},
    "xlsx_s1_0005": {"family": "ma_envelope", "params": {"window": 20, "envelope_fraction": 0.02}},
    "xlsx_s1_0006": {"family": "hma_turn", "params": {"window": 20}},
    "xlsx_s1_0007": {"family": "donchian_stop", "params": {"entry_window": 20, "exit_window": 10, "atr_window": 20, "stop_multiple": 2.0}, "source_timeframe": "1d"},
    "xlsx_s1_0010": {"family": "bollinger", "params": {"window": 20, "multiplier": 2.0, "consecutive_bars": 2}},
    "xlsx_s1_0012": {"family": "atr_channel", "params": {"window": 20, "atr_window": 20, "multiplier": 1.5}},
    "xlsx_s1_0016": {"family": "cci_ma", "params": {"window": 20}},
    "xlsx_s1_0025": {"family": "triple_sma", "params": {"fast_window": 5, "middle_window": 10, "slow_window": 30}},
    "xlsx_s1_0026": {"family": "atr_channel_confirmed", "params": {"window": 20, "atr_window": 20, "multiplier": 1.5, "consecutive_bars": 2}},
    "xlsx_s1_0033": {"family": "bollinger", "params": {"window": 30, "multiplier": 1.5, "consecutive_bars": 2}},
    "xlsx_s1_0038": {"family": "hlc_mean_cross_confirmed", "params": {"window": 20, "consecutive_bars": 2}},
    "xlsx_s2_0230": {"family": "adx_donchian", "params": {"window": 20, "exit_window": 20, "adx_window": 14, "adx_entry_threshold": 24.0, "adx_exit_threshold": 20.0}},
    "xlsx_s2_0316": {"family": "adx_donchian", "params": {"window": 20, "exit_window": 20, "adx_window": 14, "adx_entry_threshold": 24.0, "adx_exit_threshold": 20.0}},
    "xlsx_s2_0432": {"family": "adx_di_donchian", "params": {"window": 20, "exit_window": 20, "adx_window": 14, "adx_entry_threshold": 25.0, "adx_exit_threshold": 20.0}},
    "xlsx_s2_0513": {"family": "adx_donchian", "params": {"window": 20, "exit_window": 20, "adx_window": 14, "adx_entry_threshold": 24.0, "adx_exit_threshold": 20.0}},
    "xlsx_s2_0665": {"family": "adx_donchian", "params": {"window": 20, "exit_window": 10, "adx_window": 20, "adx_entry_threshold": 20.0, "adx_exit_threshold": 20.0}},
    "xlsx_s2_0842": {"family": "adx_donchian", "params": {"window": 20, "exit_window": 10, "adx_window": 20, "adx_entry_threshold": 20.0, "adx_exit_threshold": 20.0}},
    "xlsx_s1_0017": {"family": "ao_breakout", "params": {"ao_fast_window": 5, "ao_slow_window": 34, "breakout_window": 20}},
    "xlsx_s1_0019": {"family": "aroon_trend", "params": {"aroon_window": 25}},
    "xlsx_s1_0034": {"family": "aroon_oscillator", "params": {"aroon_window": 25}},
    "xlsx_s1_0020": {"family": "psar_reversal", "params": {"psar_step": 0.02, "psar_maximum": 0.2}},
    "xlsx_s1_0024": {
        "family": "adx_di_cross_donchian",
        # “当日高/低、前收” is the source's standard true-range notation;
        # its rules are explicitly N-bar based and do not impose a daily clock.
        "semantic_timeframe": "bar_period",
        "params": {"window": 20, "exit_window": 20, "adx_window": 14,
                   "adx_entry_threshold": 25.0, "adx_exit_threshold": 20.0},
    },
    "xlsx_s1_0027": {"family": "fractal_ma_breakout", "params": {"window": 20}},
    "xlsx_s1_0029": {"family": "supertrend_stop", "params": {"window": 10, "atr_window": 10, "multiplier": 3.0, "stop_multiple": 2.0}},
    "xlsx_s2_0017": {"family": "cci_ma", "params": {"window": 20}},
    "xlsx_s2_0042": {"family": "fractal_adx", "params": {"window": 20, "adx_window": 14, "adx_entry_threshold": 24.0, "adx_exit_threshold": 20.0}},
    "xlsx_s2_0277": {"family": "adx_di_donchian", "params": {"window": 20, "exit_window": 20, "adx_window": 14, "adx_entry_threshold": 25.0, "adx_exit_threshold": 20.0}},
    "xlsx_s2_0363": {"family": "adx_di_donchian", "params": {"window": 20, "exit_window": 20, "adx_window": 14, "adx_entry_threshold": 25.0, "adx_exit_threshold": 20.0}},
    "xlsx_s2_0560": {"family": "adx_di_donchian", "params": {"window": 20, "exit_window": 20, "adx_window": 14, "adx_entry_threshold": 25.0, "adx_exit_threshold": 20.0}},
    "xlsx_s2_0708": {"family": "sma_donchian_trend", "params": {"trend_window": 60, "entry_window": 20, "exit_window": 10}},
    "xlsx_s2_0737": {"family": "adx_di_donchian", "params": {"window": 20, "exit_window": 20, "adx_window": 14, "adx_entry_threshold": 25.0, "adx_exit_threshold": 20.0}},
    "xlsx_s2_0879": {"family": "sma_donchian_trend", "params": {"trend_window": 40, "entry_window": 20, "exit_window": 10}},
}

# Frozen boundary between the completed Phase 2.1 set and later semantic
# contract compilations.  Audit reruns must not make the Phase 2.2B compiler
# forget which rows were recovered from the original ambiguous population.
PHASE2_1_IMPLEMENTED_IDS = frozenset(IMPLEMENTED)

# Phase 2.2B additions are compiled from the reviewed semantic-contract plan.
# Packages and runtime configs never load the source workbook.
_SEMANTIC_STRATEGY_CONFIG = (
    Path(__file__).resolve().parents[2]
    / "configs/semantic_contracts/workbook_phase2_2b_strategies.json"
)
if _SEMANTIC_STRATEGY_CONFIG.is_file():
    _SEMANTIC_IMPLEMENTED = json.loads(_SEMANTIC_STRATEGY_CONFIG.read_text(encoding="utf-8"))
    overlap = set(IMPLEMENTED).intersection(_SEMANTIC_IMPLEMENTED)
    if overlap:
        raise ValueError(f"duplicate Phase 2.2B strategy IDs: {sorted(overlap)}")
    IMPLEMENTED.update(_SEMANTIC_IMPLEMENTED)
_PHASE2_2C_STRATEGY_CONFIG = (
    Path(__file__).resolve().parents[2]
    / "configs/semantic_contracts/workbook_phase2_2c_strategies.json"
)
if _PHASE2_2C_STRATEGY_CONFIG.is_file():
    _PHASE2_2C_IMPLEMENTED = json.loads(_PHASE2_2C_STRATEGY_CONFIG.read_text(encoding="utf-8"))
    overlap = set(IMPLEMENTED).intersection(_PHASE2_2C_IMPLEMENTED)
    if overlap:
        raise ValueError(f"duplicate Phase 2.2C strategy IDs: {sorted(overlap)}")
    IMPLEMENTED.update(_PHASE2_2C_IMPLEMENTED)
else:
    _PHASE2_2C_IMPLEMENTED = {}
_PHASE2_3_STRATEGY_CONFIG = (
    Path(__file__).resolve().parents[2]
    / "configs/semantic_contracts/workbook_phase2_3_strategies.json"
)
if _PHASE2_3_STRATEGY_CONFIG.is_file():
    _PHASE2_3_IMPLEMENTED = json.loads(_PHASE2_3_STRATEGY_CONFIG.read_text(encoding="utf-8"))
    overlap = set(IMPLEMENTED).intersection(_PHASE2_3_IMPLEMENTED)
    if overlap:
        raise ValueError(f"duplicate Phase 2.3 strategy IDs: {sorted(overlap)}")
    IMPLEMENTED.update(_PHASE2_3_IMPLEMENTED)
else:
    _PHASE2_3_IMPLEMENTED = {}
REGISTERED = {key: str(value["family"]) for key, value in IMPLEMENTED.items()}
PHASE2_IMPLEMENTED_IDS = {
    "xlsx_s1_0002", "xlsx_s1_0005", "xlsx_s1_0006", "xlsx_s1_0010",
    "xlsx_s1_0012", "xlsx_s1_0016", "xlsx_s1_0025", "xlsx_s1_0026",
    "xlsx_s1_0033", "xlsx_s1_0038", "xlsx_s2_0230", "xlsx_s2_0316",
    "xlsx_s2_0432", "xlsx_s2_0513", "xlsx_s2_0665", "xlsx_s2_0842",
}

_MODULE_CONFIG = Path(__file__).resolve().parents[2] / "configs/strategy_modules/workbook_atr_ladders.json"
if _MODULE_CONFIG.is_file():
    _REGISTERED_MODULE_CONFIGS = json.loads(_MODULE_CONFIG.read_text(encoding="utf-8"))
    REGISTERED_MODULE_TYPES = {
        str(item["module_id"]): str(item["module_type"])
        for item in _REGISTERED_MODULE_CONFIGS
    }
    REGISTERED_MODULE_IDS = set(REGISTERED_MODULE_TYPES)
else:
    REGISTERED_MODULE_IDS: set[str] = set()
    REGISTERED_MODULE_TYPES: dict[str, str] = {}

_MODULE_REQUIRED_DATA = {
    "atr_ladder_exit": "fill_entry_price+price+atr",
    "atr_hard_stop": "fill_entry_price+price+atr",
    "donchian_exit": "position+price+rolling_channel",
    "adx_exposure": "position+adx",
}

_CAPABILITY_BY_FAMILY = {
    "ao_breakout": "awesome_oscillator+cross+consecutive_state",
    "aroon_trend": "aroon+cross_state",
    "aroon_oscillator": "aroon+cross_state",
    "psar_reversal": "parabolic_sar+state_transition",
    "fractal_ma_breakout": "confirmed_fractal+previous_state+cross_state",
    "ema_crossover": "ema+cross_state+original_daily_timeframe",
    "sma_price_cross": "cross_state+original_daily_timeframe",
    "cci_ma": "standard_semantics_review",
    "adx_di_cross_donchian": "cross_state+directional_movement+rolling_breakout",
    "fractal_adx": "confirmed_fractal+directional_movement",
    "sma_donchian_trend": "rolling_breakout+slope_state",
    "supertrend_stop": "supertrend+fill_anchored_stop",
    "donchian_stop": "rolling_breakout+fill_anchored_stop+original_daily_timeframe",
    "bollinger_width_cross": "bollinger_width+cross_state+semantic_provenance",
}


def _column(cell_reference: str) -> int:
    letters = re.match(r"[A-Z]+", cell_reference).group(0)
    value = 0
    for char in letters:
        value = value * 26 + ord(char) - 64
    return value - 1


def read_workbook(path: Path) -> list[tuple[str, list[list[str]]]]:
    """Return sheet names and six-column rows without modifying the workbook."""
    with zipfile.ZipFile(path) as archive:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in archive.namelist():
            root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            for item in root.findall("m:si", NS):
                shared.append("".join(node.text or "" for node in item.iterfind(".//m:t", NS)))
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        relations = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        targets = {rel.attrib["Id"]: rel.attrib["Target"] for rel in relations}
        result: list[tuple[str, list[list[str]]]] = []
        for sheet in workbook.find("m:sheets", NS):
            name = sheet.attrib["name"]
            rel_id = sheet.attrib["{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"]
            target = targets[rel_id].lstrip("/")
            member = target if target.startswith("xl/") else f"xl/{target}"
            root = ET.fromstring(archive.read(member))
            rows: list[list[str]] = []
            for xml_row in root.iterfind(".//m:sheetData/m:row", NS):
                values = [""] * 6
                for cell in xml_row.findall("m:c", NS):
                    index = _column(cell.attrib["r"])
                    if index >= len(values):
                        continue
                    value = cell.find("m:v", NS)
                    inline = cell.find("m:is", NS)
                    text = value.text if value is not None and value.text else ""
                    if cell.attrib.get("t") == "s" and text:
                        text = shared[int(text)]
                    elif inline is not None:
                        text = "".join(node.text or "" for node in inline.iterfind(".//m:t", NS))
                    values[index] = text.strip()
                if any(values):
                    rows.append(values)
            result.append((name, rows))
        return result


_NO_ENTRY = {"", "-", "—", "/", "不具备开仓逻辑，仅配套趋势策略出场"}
_EXTERNAL = re.compile(r"上涨家数|下跌家数|市场宽度|市场广度|TRIN|NH\s*/\s*NL|跨品种|横截面|L2|PE\s*分位|期权|持仓量|订单簿|链上", re.I)
_MULTITIMEFRAME = re.compile(r"多周期|多时间框架|跨周期|日线\s*[+＋/与和].*\d+\s*[Hh小时]|周线.*日线|九重周期|八重周期|七重周期|六重周期")
_SESSION = re.compile(r"跳空|隔夜|开盘\s*\d+|开盘区间|尾盘|收盘强制|月末|月度|周末|交易日|ORB|R-Breaker", re.I)
_DAILY = re.compile(r"近?\s*\d+\s*日|日线|当日|每日|前一日|昨日")
_AMBIGUOUS = re.compile(r"有效突破|持续站稳|明显|阶段新高|阶段新低|多重共振|显著|适当|较大|背离|企稳|拐头|同步|共振|确认|附近|低位|中位|高位|极致|逐层|分层|网格|放量|承压|支撑收|反转阳|反转阴")
_KNOWN_MISSING_OPERATOR = re.compile(r"HMA|CCI|RSI|MACD|DIF|DEA|ADX|\+DI|-DI|OBV|PSAR|SAR|STOCH|KDJ|CMO|TSI|AROON|AO|WVAD|ROC|VIDYA|COG|FVG", re.I)


def _source_timeframe(text: str) -> str:
    if _MULTITIMEFRAME.search(text):
        return "multi_timeframe"
    if _SESSION.search(text):
        return "session_or_calendar"
    if _DAILY.search(text):
        return "daily"
    if re.search(r"\d+\s*(?:分钟|分|秒|[mMsS])|日内|分时", text):
        return "intraday"
    return "bar_period"


def classify(row: list[str], *, registry_id: str | None = None) -> dict[str, object]:
    """Classify one source row; only ``IMPLEMENTED`` can yield implemented."""
    _, name, _definition, long_rule, short_rule, _exit_rule = row
    text = " ".join(row[1:])
    timeframe = _source_timeframe(text)
    registry_id = registry_id or ""
    if registry_id in IMPLEMENTED:
        family = str(IMPLEMENTED[registry_id]["family"])
        source_timeframe = str(IMPLEMENTED[registry_id].get("source_timeframe", "1m"))
        timeframe = str(IMPLEMENTED[registry_id].get("semantic_timeframe", timeframe))
        return {
            "semantic_class": "exact_standalone_strategy", "final_status": "implemented",
            "implementation_family": family, "required_data": "single_symbol_ohlcv",
            "source_timeframe_semantics": timeframe,
            "adaptation_mode": "DIRECT_INTRADAY" if source_timeframe == "1m" else "UNSAFE_TO_CONVERT",
            "automatic_conversion_safe": source_timeframe == "1m", "manual_review_required": False,
            "blocking_reason": "", "package_path": f"strategies/{registry_id}",
            "config_path": f"strategies/{registry_id}/config.yaml",
            "registry_status": "planned", "structure_status": "planned",
            "smoke_status": "pending", "backtest_status": "pending",
        }
    if registry_id in REGISTERED_MODULE_IDS:
        module_type = REGISTERED_MODULE_TYPES[registry_id]
        return {
            "semantic_class": "registered_risk_exit_module", "final_status": "implemented_module",
            "implementation_family": module_type,
            "required_data": _MODULE_REQUIRED_DATA.get(module_type, "module_context"),
            "source_timeframe_semantics": timeframe, "adaptation_mode": "NOT_APPLICABLE",
            "automatic_conversion_safe": False, "manual_review_required": False,
            "blocking_reason": "", "package_path": "strategy_framework/modules.py",
            "config_path": "configs/strategy_modules/workbook_atr_ladders.json",
            "registry_status": "registered", "structure_status": "validated",
            "smoke_status": "passed", "backtest_status": "not_standalone",
        }
    no_entry = all(value.strip() in _NO_ENTRY for value in (long_rule, short_rule)) or "模块" in name
    if no_entry:
        status, semantic, reason, data, adaptation = (
            "explicit_other_blocked", "risk_exit_or_position_module",
            "not a standalone strategy: no independent entry contract", "module_context", "NOT_APPLICABLE")
    elif _EXTERNAL.search(text):
        status, semantic, reason, data, adaptation = (
            "blocked_missing_data", "external_or_cross_sectional_strategy",
            "required source is outside the canonical single-symbol OHLCV/trade dataset",
            "external_or_cross_sectional_data", "NOT_APPLICABLE")
    elif timeframe == "multi_timeframe":
        status, semantic, reason, data, adaptation = (
            "blocked_engine_capability", "multi_timeframe_strategy",
            "normal registry has no validated synchronized multi-timeframe snapshot contract",
            "multi_timeframe_ohlcv", "UNSAFE_TO_CONVERT")
    elif timeframe == "session_or_calendar":
        status, semantic, reason, data, adaptation = (
            "unsafe_timeframe_conversion", "daily_or_session_strategy",
            "24/7 BTC session/calendar mapping is not specified by the source row",
            "sessionized_ohlcv", "UNSAFE_TO_CONVERT")
    elif timeframe == "daily":
        status, semantic, reason, data, adaptation = (
            "unsafe_timeframe_conversion", "daily_strategy",
            "daily lookbacks cannot be silently reinterpreted as one-minute bar counts",
            "single_symbol_ohlcv", "SEARCH_ADAPTED")
    elif _AMBIGUOUS.search(text):
        status, semantic, reason, data, adaptation = (
            "ambiguous_manual_review", "ambiguous_natural_language_strategy",
            "one or more decision terms lack a numerical definition in the workbook",
            "single_symbol_ohlcv", "UNSAFE_TO_CONVERT")
    elif _KNOWN_MISSING_OPERATOR.search(text):
        status, semantic, reason, data, adaptation = (
            "blocked_engine_capability", "explicit_unsupported_indicator_strategy",
            "formula is explicit but its complete feature/operator and state contract is not registered",
            "single_symbol_ohlcv", "DIRECT_INTRADAY")
    else:
        status, semantic, reason, data, adaptation = (
            "ambiguous_manual_review", "unmapped_strategy",
            "no reviewed exact family mapping exists; semantics were not invented from the title",
            "single_symbol_ohlcv", "UNSAFE_TO_CONVERT")
    return {
        "semantic_class": semantic, "final_status": status, "implementation_family": "",
        "required_data": data, "source_timeframe_semantics": timeframe,
        "adaptation_mode": adaptation, "automatic_conversion_safe": False,
        "manual_review_required": status == "ambiguous_manual_review", "blocking_reason": reason,
        "package_path": "", "config_path": "", "registry_status": "not_applicable",
        "structure_status": "not_applicable", "smoke_status": "not_applicable",
        "backtest_status": "not_applicable",
    }


def _phase2_status(row: list[str], registry_id: str) -> str:
    """Reproduce the immutable Phase-2 category for transition accounting."""
    if registry_id in PHASE2_IMPLEMENTED_IDS:
        return "implemented"
    _, name, _definition, long_rule, short_rule, _exit_rule = row
    text = " ".join(row[1:])
    timeframe = _source_timeframe(text)
    if all(value.strip() in _NO_ENTRY for value in (long_rule, short_rule)) or "模块" in name:
        return "explicit_other_blocked"
    if _EXTERNAL.search(text):
        return "blocked_missing_data"
    if timeframe == "multi_timeframe":
        return "blocked_engine_capability"
    if timeframe in {"session_or_calendar", "daily"}:
        return "unsafe_timeframe_conversion"
    if _AMBIGUOUS.search(text):
        return "ambiguous_manual_review"
    if _KNOWN_MISSING_OPERATOR.search(text):
        return "blocked_engine_capability"
    return "ambiguous_manual_review"


def _phase2_1_fields(row: list[str], registry_id: str, audit: dict[str, object]) -> dict[str, object]:
    old = _phase2_status(row, registry_id)
    status = str(audit["final_status"])
    text = " ".join(row[1:])
    timeframe = str(audit["source_timeframe_semantics"])
    if status == "implemented":
        family = str(audit["implementation_family"])
        capability = _CAPABILITY_BY_FAMILY.get(family, "") if registry_id not in PHASE2_IMPLEMENTED_IDS else ""
        return {
            "previous_phase2_status": old, "phase2_1_status": "IMPLEMENTED_STANDALONE",
            "unblocked_by_capability": bool(capability), "capability_added": capability,
            "original_strategy_status": "IMPLEMENTED", "minute_conversion_status": (
                "SAFE_BASELINE_1M" if timeframe not in {"daily", "session_or_calendar", "multi_timeframe"}
                else "UNSAFE_ORIGINAL_TIMEFRAME_RETAINED"
            ), "manual_review_subcategory": "", "terminal_blocker": "",
        }
    if status == "implemented_module":
        module_type = str(audit["implementation_family"])
        return {
            "previous_phase2_status": old, "phase2_1_status": "IMPLEMENTED_MODULE",
            "unblocked_by_capability": True,
            "capability_added": f"strategy_module_contract+{module_type}",
            "original_strategy_status": "REGISTERED_MODULE", "minute_conversion_status": "NOT_APPLICABLE",
            "manual_review_subcategory": "", "terminal_blocker": "",
        }
    if status == "blocked_missing_data":
        blocker = "UNAVAILABLE_EXTERNAL_UNIVERSE" if re.search(r"上涨家数|下跌家数|市场宽度|市场广度|NH\s*/\s*NL|TRIN|跨品种|横截面", text, re.I) else "MISSING_SOURCE_DATA"
        subcategory = "OTHER"
    elif status == "unsafe_timeframe_conversion":
        blocker = "ECONOMIC_SESSION_DEFINITION_REQUIRED" if timeframe == "session_or_calendar" else "AMBIGUOUS_NUMERIC_SEMANTICS"
        subcategory = "OTHER"
    elif status == "explicit_other_blocked":
        blocker = "NON_STANDALONE_MODULE_UNSUPPORTED"
        subcategory = "OTHER"
    elif _AMBIGUOUS.search(text) or re.search(r"减仓(?!\s*\d)|逐层|分层|极值|背离|企稳|站稳|有效", text):
        blocker = "AMBIGUOUS_ENTRY_EXIT_LOGIC"
        subcategory = "TRULY_AMBIGUOUS"
    elif re.search(r"阈值|高位|低位|中位|阶段新高|阶段新低", text):
        blocker = "AMBIGUOUS_NUMERIC_SEMANTICS"
        subcategory = "MISSING_NUMERIC_PARAMETER"
    else:
        # Phase 2.1 has canonical comparison/cross/previous/rolling/state and
        # completed multi-timeframe primitives.  An unmapped row is therefore
        # not labelled an engine blocker merely because no family has been
        # written yet: the workbook still lacks a reviewed unique execution
        # interpretation (often partial-exit timing, ATR period, channel-state
        # definition, or an indicator-specific qualitative clause).
        blocker = "AMBIGUOUS_ENTRY_EXIT_LOGIC"
        subcategory = "TRULY_AMBIGUOUS"
    return {
        "previous_phase2_status": old, "phase2_1_status": blocker,
        "unblocked_by_capability": False, "capability_added": "",
        "original_strategy_status": "BLOCKED", "minute_conversion_status": (
            "UNSAFE" if timeframe in {"daily", "session_or_calendar", "multi_timeframe"} else "NOT_APPLICABLE"
        ), "manual_review_subcategory": subcategory, "terminal_blocker": blocker,
    }


def write_csv(path: Path, fieldnames: tuple[str, ...] | list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def _json_atomic(path: Path, payload: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def build_manifest(workbook: Path) -> tuple[list[dict], dict[str, int]]:
    manifest: list[dict] = []
    counts: dict[str, int] = {}
    for sheet_index, (sheet_name, rows) in enumerate(read_workbook(workbook), start=1):
        counts[sheet_name] = len(rows) - 1
        for source_row, row in enumerate(rows[1:], start=2):
            source_number = int(float(row[0]))
            registry_id = f"xlsx_s{sheet_index}_{source_number:04d}"
            audit = classify(row, registry_id=registry_id)
            definition = IMPLEMENTED.get(registry_id, {})
            phase2b_definition = _SEMANTIC_IMPLEMENTED.get(registry_id, {}) if _SEMANTIC_STRATEGY_CONFIG.is_file() else {}
            defaulted = dict(definition.get("defaulted_parameters", {}))
            phase2b = {
                "phase2_2b_status": "IMPLEMENTED_STANDALONE" if registry_id in _SEMANTIC_IMPLEMENTED else "UNCHANGED_BLOCKED",
                "semantic_provenance": definition.get("semantic_provenance", "SOURCE_EXACT") if definition else "",
                "contracts_applied": ";".join(str(item) for item in definition.get("contracts_applied", [])),
                "defaulted_parameter_count": len(defaulted),
                "defaulted_parameters": json.dumps(defaulted, ensure_ascii=False, sort_keys=True) if defaulted else "",
            }
            manifest.append({
                "source_sheet": sheet_name, "source_sheet_index": sheet_index,
                "source_row": source_row, "source_strategy_number": row[0],
                "source_strategy_name": row[1], "source_indicator_definition": row[2],
                "source_long_condition": row[3], "source_short_condition": row[4],
                "source_exit_condition": row[5], "registry_id": registry_id,
                **audit,
                **_phase2_1_fields(row, registry_id, audit),
                **phase2b,
            })
    return manifest, counts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workbook", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    manifest, sheet_counts = build_manifest(args.workbook)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(args.output_dir / "strategy_workbook_conversion_manifest.csv", HEADERS, manifest)
    write_csv(args.output_dir / "strategy_conversion_manifest.csv", HEADERS, manifest)
    review = [row for row in manifest if row["final_status"] not in {"implemented", "implemented_module"}]
    write_csv(args.output_dir / "strategy_conversion_review.csv", HEADERS, review)
    implemented = [row for row in manifest if row["final_status"] == "implemented"]
    write_csv(args.output_dir / "registered_strategy_manifest.csv", HEADERS, implemented)
    transitions = [
        {
            "source_identity": row["registry_id"], "strategy_name": row["source_strategy_name"],
            "old_status": row["previous_phase2_status"], "new_status": row["phase2_1_status"],
            "reason": row["blocking_reason"] or row["capability_added"],
            "new_capability_added": row["capability_added"],
            "registry_id": row["registry_id"], "backtest_status": row["backtest_status"],
        }
        for row in manifest
        if row["previous_phase2_status"] != row["final_status"]
    ]
    write_csv(
        args.output_dir / "phase2_1_status_transitions.csv",
        ("source_identity", "strategy_name", "old_status", "new_status", "reason",
         "new_capability_added", "registry_id", "backtest_status"), transitions,
    )
    capability_rows = []
    for capability in sorted({str(row["capability_added"]) for row in manifest if row["capability_added"]}):
        ids = [str(row["registry_id"]) for row in manifest if row["capability_added"] == capability]
        capability_rows.append({
            "capability": capability, "unlocked_count": len(ids),
            "registry_ids": ";".join(ids), "validation_status": "targeted_tests_required",
        })
    write_csv(
        args.output_dir / "phase2_1_capability_manifest.csv",
        ("capability", "unlocked_count", "registry_ids", "validation_status"), capability_rows,
    )

    family_fields = ("implementation_family", "strategy_count", "registry_ids", "runtime_dependency")
    by_family: dict[str, list[str]] = {}
    for row in implemented:
        by_family.setdefault(str(row["implementation_family"]), []).append(str(row["registry_id"]))
    family_rows = [{
        "implementation_family": family, "strategy_count": len(ids),
        "registry_ids": ";".join(ids),
        "runtime_dependency": "normal FeatureSpec + StrategyPlugin + execution/backtest pipeline",
    } for family, ids in sorted(by_family.items())]
    write_csv(args.output_dir / "strategy_family_manifest.csv", family_fields, family_rows)

    search_fields = ("registry_id", "source_parameter", "target_timeframe", "adaptation_mode", "searchable_parameters", "fixed_parameters", "candidate_range", "ordering_constraints", "train_interval", "validation_interval", "test_interval", "objective", "status")
    search_rows = []
    for row in manifest:
        registry_id = str(row["registry_id"])
        if row["final_status"] == "implemented":
            params = dict(IMPLEMENTED[registry_id]["params"])
            defaulted = dict(IMPLEMENTED[registry_id].get("defaulted_parameters", {}))
            searchable = sorted({name for name in params if "window" in name} | set(defaulted))
            candidate_ranges = {}
            for name in searchable:
                value = defaulted.get(name, params.get(name))
                if isinstance(value, int):
                    candidate_ranges[name] = sorted({max(1, value // 2), value, value * 2})
                elif isinstance(value, float):
                    candidate_ranges[name] = sorted({max(0.0, value / 2), value, value * 1.5})
                else:
                    candidate_ranges[name] = [value]
            fixed = {name: value for name, value in params.items() if name not in searchable}
            status = "prepared_not_run"
        elif row["adaptation_mode"] == "SEARCH_ADAPTED":
            params = {"source_definition": row["source_indicator_definition"]}
            searchable = []
            candidate_ranges = {}
            fixed = {}
            status = "blocked_until_timeframe_semantic_review"
        else:
            continue
        search_rows.append({
            "registry_id": registry_id, "source_parameter": json.dumps(params, ensure_ascii=False),
            "target_timeframe": "1m", "adaptation_mode": row["adaptation_mode"],
            "searchable_parameters": json.dumps(searchable, ensure_ascii=False),
            "fixed_parameters": json.dumps(fixed, ensure_ascii=False),
            "candidate_range": json.dumps(candidate_ranges, ensure_ascii=False),
            "ordering_constraints": "fast < middle < slow where the family defines ordered windows",
            "train_interval": "2021-07-01/2023-06-30", "validation_interval": "2023-07-01/2024-06-30",
            "test_interval": "2024-07-01/2026-06-30",
            "objective": "existing validation score; held-out test excluded from selection",
            "status": status,
        })
    write_csv(args.output_dir / "parameter_search_manifest.csv", search_fields, search_rows)

    statuses = Counter(str(row["final_status"]) for row in manifest)
    phase2_1_statuses = Counter(str(row["phase2_1_status"]) for row in manifest)
    identifiers = [str(row["registry_id"]) for row in manifest]
    package_paths = [str(row["package_path"]) for row in implemented]
    config_paths = [str(row["config_path"]) for row in implemented]
    result_keys = [str(row["registry_id"]) for row in implemented]
    summary = {
        "source_workbook": str(args.workbook.resolve()),
        "source_sha256": hashlib.sha256(args.workbook.read_bytes()).hexdigest(),
        "sheet_counts": sheet_counts, "workbook_total_rows": len(manifest),
        "status_counts": dict(sorted(statuses.items())), "sum": sum(statuses.values()),
        "phase2_1_status_counts": dict(sorted(phase2_1_statuses.items())),
        "unaccounted": len(manifest) - sum(statuses.values()),
        "registry_collisions": len(identifiers) - len(set(identifiers)),
        "package_collisions": len(package_paths) - len(set(package_paths)),
        "config_identifier_collisions": len(config_paths) - len(set(config_paths)),
        "result_path_collisions": len(result_keys) - len(set(result_keys)),
        "implemented_registry_entries": len(implemented), "implemented_packages": len(implemented),
        "implemented_modules": statuses.get("implemented_module", 0),
        "unique_implementation_families": len(by_family),
        "identity_rule": "source sheet index + source strategy number",
        "runtime_reads_workbook": False,
    }
    _json_atomic(args.output_dir / "validation_summary.json", summary)
    _json_atomic(args.output_dir / "workbook_audit_summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False))
    collision_fields = (
        "registry_collisions", "package_collisions",
        "config_identifier_collisions", "result_path_collisions",
    )
    return 0 if summary["unaccounted"] == 0 and all(summary[key] == 0 for key in collision_fields) else 1


if __name__ == "__main__":
    raise SystemExit(main())
