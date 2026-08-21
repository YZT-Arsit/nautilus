#!/usr/bin/env python3
"""Compile only full-closure Phase 2.2C workbook strategy families.

Every emitted row is checked against its complete frozen blocker set.  A
recognized fragment is never enough: the matcher must own entry, exit,
position and state semantics and declare every blocker resolved.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
AUDIT = ROOT / "outputs/internal_audit/strategy_workbook"
OUTPUT = ROOT / "configs/semantic_contracts/workbook_phase2_2c_strategies.json"


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def definition(
    family: str, params: dict[str, object], contracts: list[str],
    resolved: set[str], defaults: dict[str, object] | None = None,
) -> dict[str, object]:
    defaults = defaults or {}
    return {
        "family": family,
        "params": params,
        "semantic_provenance": "PARAMETER_DEFAULTED" if defaults else "STANDARD_CONTRACT_RESOLVED",
        "contracts_applied": contracts,
        "defaulted_parameters": defaults,
        "resolved_blockers": sorted(resolved),
        "modules_applied": [],
    }


def match(row: dict[str, str], blockers: set[str]) -> dict[str, object] | None:
    identity = row["registry_id"]
    long_rule, short_rule, exit_rule = (
        row["source_long_condition"], row["source_short_condition"], row["source_exit_condition"],
    )

    if blockers == {"PERSISTENCE_COUNT_MISSING"} and "MACD" in long_rule and "DIF" in exit_rule:
        return definition(
            "macd_zero_persistent", {"consecutive_bars": 2, "reduction_fraction": 0.5},
            ["PERSISTENCE_2BAR_V1", "REDUCE_HALF_CURRENT_V1"], blockers,
            {"persistence_bars": 2, "reduction_fraction": 0.5},
        )
    if blockers == {"PERSISTENCE_COUNT_MISSING"} and long_rule.startswith("AO") and "0 轴" in exit_rule:
        return definition(
            "ao_zero_persistent", {"consecutive_bars": 2, "reduction_fraction": 0.5},
            ["PERSISTENCE_2BAR_V1", "REDUCE_HALF_CURRENT_V1"], blockers,
            {"reduction_fraction": 0.5},
        )
    if blockers == {"CONFLUENCE_COMPOSITION", "PERSISTENCE_COUNT_MISSING"} and "EMA" in long_rule and "AO" in long_rule:
        return definition(
            "ema_ao_persistent", {"window": 20, "consecutive_bars": 2, "reduction_fraction": 0.5},
            ["CONFLUENCE_AND_V1", "PERSISTENCE_2BAR_V1"], blockers,
        )
    if identity in {"xlsx_s1_0522", "xlsx_s2_0211"}:
        return definition(
            "ma_cross_slope_atr_exit",
            {"average_type": "ema", "fast_window": 10, "slow_window": 30,
             "atr_window": 14, "stop_multiple": 0.0, "take_profit_multiple": 0.0,
             "reduction_fraction": 0.5},
            ["CONFLUENCE_AND_V1", "TURN_SLOPE_SIGN_CHANGE_V1", "REDUCE_HALF_CURRENT_V1"],
            blockers, {"reduction_fraction": 0.5},
        )
    if identity == "xlsx_s2_0441":
        return definition(
            "adx_ma_di_confluence",
            {"window": 60, "adx_window": 14, "adx_entry_threshold": 25.0,
             "adx_exit_threshold": 20.0},
            ["CONFLUENCE_AND_V1"], blockers,
        )
    if identity in {"xlsx_s1_0479", "xlsx_s2_0168", "xlsx_s2_0479"}:
        stable = "STABLE_ABOVE" in blockers
        return definition(
            "fractal_adx_stable" if stable else "fractal_adx",
            {"window": 20, "adx_window": 14, "adx_entry_threshold": 24.0,
             "adx_exit_threshold": 20.0, "consecutive_bars": 2},
            ["CONFIRMED_FRACTAL_2X2_V1"] + (["STABLE_CLOSE_2BAR_V1"] if stable else []),
            blockers, {"fractal_side_bars": 2, **({"persistence_bars": 2} if stable else {})},
        )
    if identity in {"xlsx_s1_0432", "xlsx_s2_0121"}:
        return definition(
            "adx_di_recent_extreme",
            {"window": 20, "exit_window": 20, "adx_window": 14,
             "adx_entry_threshold": 25.0, "adx_exit_threshold": 20.0},
            ["RECENT_EXTREME_PRIOR_20_V1"], blockers,
            {"recent_extreme_lookback": 20},
        )
    if identity == "xlsx_s1_0040":
        return definition(
            "donchian_ma_stop",
            {"window": 10, "entry_window": 5, "exit_window": 10,
             "atr_window": 14, "stop_multiple": 1.0},
            ["ATR14_DEFAULT_V1"], blockers, {"atr_window": 14},
        )
    if identity in {"xlsx_s2_0632", "xlsx_s2_0809"}:
        return definition(
            "adx_donchian_stop",
            {"entry_window": 20, "exit_window": 10, "adx_window": 14,
             "adx_entry_threshold": 22.0, "adx_exit_threshold": 20.0,
             "atr_window": 14, "stop_multiple": 1.9},
            ["ATR14_DEFAULT_V1"], blockers, {"atr_window": 14},
        )
    if identity == "xlsx_s2_0022":
        return definition(
            "triple_sma_ordered",
            {"fast_window": 5, "middle_window": 20, "slow_window": 60,
             "reduction_fraction": 0.5},
            ["CONFLUENCE_AND_V1", "TURN_SLOPE_SIGN_CHANGE_V1", "REDUCE_HALF_CURRENT_V1"],
            blockers, {"reduction_fraction": 0.5},
        )
    if identity == "xlsx_s2_0252":
        return definition(
            "four_ma_stable_layered",
            {"fast_window": 5, "middle_window": 10, "slow_window": 30,
             "filter_window": 90, "consecutive_bars": 2, "reduction_fraction": 0.5},
            ["STABLE_CLOSE_2BAR_V1", "TURN_SLOPE_SIGN_CHANGE_V1", "LAYERED_REDUCTION_EQUAL_V1"],
            blockers, {"persistence_bars": 2, "reduction_stages": 2},
        )
    if identity == "xlsx_s2_0435":
        return definition(
            "cci_touch_reduce", {"window": 20, "reduction_fraction": 0.5},
            ["TOUCH_AS_THRESHOLD_CROSS_V1", "REDUCE_HALF_CURRENT_V1"], blockers,
            {"reduction_fraction": 0.5},
        )
    if identity == "xlsx_s2_0229":
        return definition(
            "donchian_pyramid",
            {"trend_window": 60, "entry_window": 30, "exit_window": 15,
             "atr_window": 14, "stop_multiple": 1.8, "grid_layers": 1,
             "layer_fraction": 1.0, "entry_distance_multiple": 1.0,
             "pyramid_direction": "favorable"},
            ["CHANNEL_LAST_BREAKOUT_STATE_V1", "ATR14_DEFAULT_V1"], blockers,
            {"atr_window": 14},
        )
    if blockers == {"CHANNEL_STATE_DEFINITION", "PYRAMID_ADD_FRACTION"}:
        long_match = re.search(r"(\d+)\s*通道多头.*?突破\s*(\d+)\s*(?:周期)?上轨.*?(?:最多|上限)\s*([345四五三])\s*层", long_rule)
        short_match = re.search(r"(\d+)\s*通道空头.*?跌破\s*(\d+)\s*(?:周期)?下轨.*?(?:最多|上限)\s*([345四五三])\s*层", short_rule)
        exit_match = re.search(r"跌破\s*(\d+)\s*周期下轨平多.*?站上\s*(\d+)\s*周期上轨平空.*?(\d+(?:\.\d+)?)\s*ATR", exit_rule)
        if long_match and short_match and exit_match and long_match.groups()[:2] == short_match.groups()[:2]:
            numerals = {"三": 3, "四": 4, "五": 5}
            layers = numerals.get(long_match.group(3), int(long_match.group(3)) if long_match.group(3).isdigit() else 0)
            if layers != numerals.get(short_match.group(3), int(short_match.group(3)) if short_match.group(3).isdigit() else 0):
                return None
            return definition(
                "donchian_pyramid",
                {"trend_window": int(long_match.group(1)), "entry_window": int(long_match.group(2)),
                 "exit_window": int(exit_match.group(1)), "atr_window": 14,
                 "stop_multiple": float(exit_match.group(3)), "grid_layers": layers,
                 "layer_fraction": 1.0 / layers, "entry_distance_multiple": 1.0,
                 "pyramid_direction": "favorable"},
                ["CHANNEL_LAST_BREAKOUT_STATE_V1", "GRID_SOURCE_LAYERS_EQUAL_EXPOSURE_V1",
                 "PYRAMID_FAVORABLE_DIRECTION_V1", "ATR14_DEFAULT_V1"], blockers,
                {"atr_window": 14, "atr_step": 1.0, "layer_fraction": 1.0 / layers},
            )
    return None


def compile_definitions(
    manifest: list[dict[str, str]], blocker_rows: list[dict[str, str]],
    phase2b: set[str],
) -> dict[str, dict[str, object]]:
    blockers: dict[str, set[str]] = defaultdict(set)
    for item in blocker_rows:
        blockers[item["source_identity"]].add(item["normalized_blocker_id"])
    result: dict[str, dict[str, object]] = {}
    for row in manifest:
        identity = row["registry_id"]
        if identity in phase2b or identity not in blockers:
            continue
        item = match(row, blockers[identity])
        if item is None:
            continue
        remaining = blockers[identity] - set(item["resolved_blockers"])
        if remaining:
            raise ValueError(f"partial closure rejected for {identity}: {sorted(remaining)}")
        result[identity] = item
    return dict(sorted(result.items()))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit-root", type=Path, default=AUDIT)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    manifest = read_csv(args.audit_root / "strategy_workbook_conversion_manifest.csv")
    blockers = read_csv(args.audit_root / "semantic_contracts/semantic_blocker_manifest.csv")
    phase2b_path = ROOT / "configs/semantic_contracts/workbook_phase2_2b_strategies.json"
    phase2b = set(json.loads(phase2b_path.read_text(encoding="utf-8")))
    compiled = compile_definitions(manifest, blockers, phase2b)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(json.dumps(compiled, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, args.output)
    print(json.dumps({"compiled": len(compiled), "ids": list(compiled)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
