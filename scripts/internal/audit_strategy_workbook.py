#!/usr/bin/env python3
"""Audit the strategy workbook without modifying it or inventing semantics.

The source workbook is read directly from its OOXML members with the Python
standard library.  This keeps the audit read-only and makes it runnable in the
same constrained server environment as the backtests.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import zipfile
from collections import Counter
from pathlib import Path
from xml.etree import ElementTree as ET


NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
REL_NS = {"r": "http://schemas.openxmlformats.org/package/2006/relationships"}
HEADERS = (
    "source_sheet", "source_strategy_number", "source_strategy_name", "registry_id",
    "classification", "conversion_status", "implementation_family", "required_data",
    "source_timeframe_semantics", "target_timeframe_support", "tunable_parameters",
    "automatic_conversion_safe", "manual_review_required", "reason", "code_path",
    "test_status",
)
REGISTERED = {
    "xlsx_s1_0002": "sma_crossover",
    "xlsx_s1_0005": "ma_envelope",
    "xlsx_s1_0010": "bollinger",
    "xlsx_s1_0012": "atr_channel",
}


def _column(cell_reference: str) -> int:
    letters = re.match(r"[A-Z]+", cell_reference).group(0)
    value = 0
    for char in letters:
        value = value * 26 + ord(char) - 64
    return value - 1


def read_workbook(path: Path) -> list[tuple[str, list[list[str]]]]:
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


FAMILIES = (
    ("sma_crossover", re.compile(r"(?:双均线|均线.*(?:金叉|死叉)|SMA\s*\d+\s*[/、]\s*\d+)", re.I)),
    ("price_vs_sma", re.compile(r"(?:单均线|价格.*均线|均线.*突破)", re.I)),
    ("donchian", re.compile(r"(?:Donchian|唐奇安|海龟)", re.I)),
    ("rsi", re.compile(r"\bRSI\s*\d*", re.I)),
    ("macd", re.compile(r"\bMACD\b|DIF.*DEA", re.I)),
    ("ema_crossover", re.compile(r"EMA.*(?:金叉|死叉|交叉|/)", re.I)),
    ("bollinger", re.compile(r"Bollinger|布林", re.I)),
    ("atr", re.compile(r"\bATR\s*\d*|平均真实波幅", re.I)),
)


def _numbers(text: str) -> list[int | float]:
    values: list[int | float] = []
    for raw in re.findall(r"(?<![.\d])\d{1,5}(?:\.\d+)?(?![.\d])", text):
        value = float(raw)
        values.append(int(value) if value.is_integer() else value)
    return values


def classify(row: list[str]) -> dict[str, object]:
    number, name, definition, long_rule, short_rule, exit_rule = row
    text = " ".join(row[1:])
    family = next((value for value, pattern in FAMILIES if pattern.search(text)), "unmapped")
    no_entry = all(value.strip() in {"", "-", "—", "/"} for value in (long_rule, short_rule))
    external = re.search(r"上涨家数|下跌家数|市场宽度|市场广度|\bMSI\b|\bTRIN\b|NH\s*/\s*NL|跨品种|横截面", text, re.I)
    multi = re.search(r"多周期|多时间框架|日线\s*[+＋/与和].*\d+\s*[Hh]|(?:\d+\s*[/、+＋]\s*)+\d+\s*(?:分|分钟|[mM])", text)
    calendar = re.search(r"跳空|隔夜|开盘|收盘前|尾盘|月末|月度|周末|交易日", text)
    ambiguous = re.search(r"有效突破|持续站稳|明显放量|阶段新高|阶段新低|多重共振|显著|适当|较大|背离|企稳|拐头|斜率同步|共振|确认波动有效", text)
    explicit_intraday = re.search(r"日内|\d+\s*(?:分钟|分|秒|[mMsS])", text)
    daily = re.search(r"近?\s*\d+\s*日|日线|当日|每日", text)

    if no_entry or re.search(r"模块", name):
        category, reason = "G_RISK_OR_EXIT_MODULE_ONLY", "no standalone long/short entry or explicitly named module"
    elif external:
        category, reason = "F_EXTERNAL_OR_CROSS_SECTIONAL_DATA", "requires data outside a single-symbol OHLCV/trade stream"
    elif multi:
        category, reason = "D_MULTI_TIMEFRAME", "contains multiple explicit timeframes; must not be flattened"
    elif calendar:
        category, reason = "E_CALENDAR_OR_SESSION_SEMANTICS", "depends on a calendar/session convention"
    elif ambiguous or family == "unmapped":
        category, reason = "H_AMBIGUOUS_OR_UNDERSPECIFIED", "rule is not an exact supported numerical family"
    elif explicit_intraday:
        category, reason = "A_DIRECT_INTRADAY", "explicit intraday timeframe with a recognized family"
    elif daily:
        category, reason = "C_DAILY_TO_INTRADAY_PARAMETRIC", "recognized formula has daily semantics requiring explicit adaptation"
    else:
        category, reason = "B_BAR_PERIOD_PARAMETRIC", "recognized formula is expressed in bars/periods"

    nums = _numbers(text)
    exact_family_supported = family in {"sma_crossover", "price_vs_sma", "bollinger", "atr"}
    unrelated_indicator = re.search(r"\b(?:ADX|RSI|MACD|CCI|OBV|WVF|CMO|TSI|AO)\b|成交量|分形", text, re.I)
    safe = (
        category in {"A_DIRECT_INTRADAY", "B_BAR_PERIOD_PARAMETRIC"}
        and exact_family_supported
        and bool(nums)
        and unrelated_indicator is None
    )
    if category in {"A_DIRECT_INTRADAY", "B_BAR_PERIOD_PARAMETRIC"} and unrelated_indicator is not None:
        reason = "recognized family is combined with another indicator and requires exact composite semantics"
    required = "single_symbol_ohlcv"
    if category == "F_EXTERNAL_OR_CROSS_SECTIONAL_DATA":
        required = "external_cross_sectional_data"
    elif category == "D_MULTI_TIMEFRAME":
        required = "multi_timeframe_ohlcv"
    status = "eligible_exact_family" if safe else "manual_review"
    if category == "G_RISK_OR_EXIT_MODULE_ONLY":
        status = "module_only"
    return {
        "classification": category,
        "conversion_status": status,
        "implementation_family": family,
        "required_data": required,
        "source_timeframe_semantics": "intraday" if explicit_intraday else ("daily" if daily else "bar_period"),
        "target_timeframe_support": "N-minute" if category not in {"D_MULTI_TIMEFRAME", "E_CALENDAR_OR_SESSION_SEMANTICS", "F_EXTERNAL_OR_CROSS_SECTIONAL_DATA"} else "review_required",
        "tunable_parameters": json.dumps(nums, ensure_ascii=False),
        "automatic_conversion_safe": safe,
        "manual_review_required": not safe,
        "reason": reason,
        "code_path": "strategies/workbook_parametric" if safe else "",
        "test_status": "not_implemented",
    }


def write_csv(path: Path, fieldnames: tuple[str, ...] | list[str], rows: list[dict]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workbook", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    sheets = read_workbook(args.workbook)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    manifest: list[dict] = []
    sheet_counts: dict[str, int] = {}
    for sheet_index, (sheet_name, rows) in enumerate(sheets, start=1):
        data = rows[1:]
        sheet_counts[sheet_name] = len(data)
        for row_index, row in enumerate(data, start=1):
            source_number = int(float(row[0]))
            registry_id = f"xlsx_s{sheet_index}_{source_number:04d}"
            manifest.append({
                "source_sheet": sheet_name,
                "source_strategy_number": row[0],
                "source_strategy_name": row[1],
                "registry_id": registry_id,
                **classify(row),
            })
            if registry_id in REGISTERED:
                manifest[-1].update(
                    conversion_status="registered_first_batch",
                    implementation_family=REGISTERED[registry_id],
                    automatic_conversion_safe=True,
                    manual_review_required=False,
                    code_path="strategies/workbook_parametric",
                    test_status="contract_tests_passed",
                )
    write_csv(args.output_dir / "strategy_conversion_manifest.csv", HEADERS, manifest)
    review = [row for row in manifest if row["manual_review_required"]]
    write_csv(args.output_dir / "strategy_conversion_review.csv", HEADERS, review)
    registered = [row for row in manifest if row["registry_id"] in REGISTERED]
    write_csv(args.output_dir / "registered_strategy_manifest.csv", HEADERS, registered)
    mapping_fields = ["registry_id", "source_sheet", "source_strategy_number", "implementation_family", "source_timeframe_semantics", "tunable_parameters", "adaptation_mode", "constraints"]
    mappings = [{
        **row,
        "adaptation_mode": "duration_preserving_or_walk_forward_search" if row["classification"] == "C_DAILY_TO_INTRADAY_PARAMETRIC" else "bar_period_preserving",
        "constraints": "preserve ordered windows; dimensionless thresholds unchanged",
    } for row in manifest if row["implementation_family"] != "unmapped"]
    write_csv(args.output_dir / "parameter_mapping_manifest.csv", mapping_fields, mappings)
    counts = Counter(str(row["classification"]) for row in manifest)
    summary = {
        "source_workbook": str(args.workbook.resolve()),
        "sheet_counts": sheet_counts,
        "total_strategies": len(manifest),
        "classification_counts": dict(sorted(counts.items())),
        "automatic_conversion_safe": sum(bool(row["automatic_conversion_safe"]) for row in manifest),
        "manual_review_required": len(review),
        "registered_first_batch": len(registered),
        "identity_rule": "source_sheet + source_strategy_number; stable registry_id xlsx_s<sheet>_<number>",
    }
    temporary = args.output_dir / "workbook_audit_summary.json.tmp"
    temporary.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(args.output_dir / "workbook_audit_summary.json")
    search_schema = {
        "status": "framework_only_no_optimization_claimed",
        "optimizer": "deterministic bounded candidate generation; evaluation uses the existing backtest runner",
        "selection_protocol": ["train", "validation"],
        "held_out_test": "test period is inaccessible to parameter selection",
        "parameter_semantics": {
            "lookback_bars": "preserve bars or explicitly search around the source prior",
            "physical_duration_minutes": "ceil(duration_minutes / target_bar_minutes), never silently cap",
            "dimensionless_threshold": "preserve by default",
            "calendar_semantic": "reject automatic conversion",
        },
        "candidate_generation": "bounded logarithmic integer candidates including the source seed",
        "constraints": ["short_window < long_window", "positive windows"],
        "lookahead_protection": "strictly ordered, non-overlapping train/validation/test boundaries",
    }
    temporary = args.output_dir / "parameter_search_schema.json.tmp"
    temporary.write_text(json.dumps(search_schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(args.output_dir / "parameter_search_schema.json")
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
