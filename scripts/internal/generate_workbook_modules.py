#!/usr/bin/env python3
"""Compile precise workbook module rows into normal runtime module configs."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from scripts.internal.audit_strategy_workbook import read_workbook


_NON_ATR_DEPENDENCY = re.compile(
    r"分形|ROC|MACD|VWAP|WVAP|Zscore|CI|HV|BBW|POC|唐奇安|一目|OBV|PSAR", re.I,
)


def compile_module(registry_id: str, row: list[str]) -> dict | None:
    name, definition, long_rule, short_rule, exit_rule = row[1:]
    no_entry_values = {"", "-", "—", "/", "不具备开仓逻辑，仅配套趋势策略出场"}
    if long_rule.strip() not in no_entry_values or short_rule.strip() not in no_entry_values:
        return None
    compact = re.sub(r"\s+", "", f"{name}{definition}{exit_rule}")
    if "唐奇安出场系统" in compact and re.search(r"10周期", compact):
        return {
            "module_id": registry_id, "module_type": "donchian_exit", "window": 10,
            "source_name": name,
            "compatibility": "requires canonical completed 10-bar upper/lower channel features",
        }
    if "2ATR固定止损模块" in compact and "2ATR" in compact:
        return {
            "module_id": registry_id, "module_type": "atr_hard_stop", "stop_loss_atr": 2.0,
            "source_name": name,
            "compatibility": "requires fill entry price, current price, and canonical ATR",
        }
    if "ADX" in compact and "满仓" in compact and ("半仓" in compact or "减半" in compact):
        if re.search(r"ADX[＞>]25", compact) and re.search(r"ADX[＜<]20", compact) and "3成" in compact:
            return {
                "module_id": registry_id, "module_type": "adx_exposure",
                "full_threshold": 25.0, "medium_threshold": 20.0,
                "medium_exposure": 0.5, "low_exposure": 0.3,
                "source_name": name, "compatibility": "requires canonical ADX feature",
            }
        if re.search(r"ADX30\+", compact) and re.search(r"[＜<]20", compact):
            return {
                "module_id": registry_id, "module_type": "adx_exposure",
                "full_threshold": 30.0, "medium_threshold": 20.0,
                "medium_exposure": 0.5, "low_exposure": 0.0,
                "source_name": name, "compatibility": "requires canonical ADX feature",
            }
    if _NON_ATR_DEPENDENCY.search(f"{name} {definition}"):
        return None
    text = exit_rule.replace("再减", "减").replace("减仓", "减")
    pairs = [
        (float(level), float(percent) / 100.0)
        for level, percent in re.findall(
            r"(\d+(?:\.\d+)?)\s*ATR[^；，、/]*?减\s*(\d+(?:\.\d+)?)\s*%", text, re.I,
        )
    ]
    finals = [float(value) for value in re.findall(
        r"(\d+(?:\.\d+)?)\s*ATR[^；，、/]*?(?:全部|全额|全仓|全清|全平)", text, re.I,
    )]
    stops = [float(value) for value in re.findall(
        r"浮亏\s*(\d+(?:\.\d+)?)\s*ATR[^；，、/]*?(?:止损|平仓|清仓|离场)", text, re.I,
    )]
    if not pairs or not finals or not stops:
        return None
    if sum(fraction for _, fraction in pairs) > 1.0 + 1e-12 or finals[-1] <= pairs[-1][0]:
        return None
    return {
        "module_id": registry_id,
        "module_type": "atr_ladder_exit",
        "profit_levels_atr": [level for level, _ in pairs],
        "reduction_fractions": [fraction for _, fraction in pairs],
        "final_profit_atr": finals[-1],
        "stop_loss_atr": stops[-1],
        "source_name": name,
        "compatibility": "requires fill entry price, current price, and canonical ATR",
    }


def build(workbook: Path) -> list[dict]:
    result = []
    for sheet_index, (_sheet, rows) in enumerate(read_workbook(workbook), 1):
        for row in rows[1:]:
            source_number = int(float(row[0]))
            registry_id = f"xlsx_s{sheet_index}_{source_number:04d}"
            compiled = compile_module(registry_id, row)
            if compiled is not None:
                result.append(compiled)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workbook", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    rows = build(args.workbook)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(args.output)
    print(f"compiled strategy modules: {len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
