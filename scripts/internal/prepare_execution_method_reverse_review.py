#!/usr/bin/env python3
"""Freeze FIRST_TICK-only selection before maker/reverse execution."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
STAGE_A = ROOT / "outputs/deliverables/tick_review_stageA_9symbols"
WORK = ROOT / "outputs/baseline_evaluation/execution_method_and_reverse_review"
DELIVERY = ROOT / "outputs/deliverables/execution_method_and_reverse_review"


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def atomic_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    frame.to_csv(temporary, index=False)
    os.replace(temporary, path)


def atomic_json(payload: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def main() -> None:
    validation = json.loads((STAGE_A / "validation_summary.json").read_text(encoding="utf-8"))
    if validation.get("status") != "PASSED":
        raise ValueError("Stage-A source is not PASSED")
    all_results = pd.read_csv(STAGE_A / "all_1m10m15m_results.csv")
    expected = (
        (all_results.timeframe.eq("1m") & all_results.Sharpe.abs().gt(1.5))
        | (
            all_results.timeframe.isin(["10m", "15m"])
            & all_results.Signed_BE_bps.abs().gt(10.0)
            & all_results.Sharpe.abs().gt(1.0)
        )
    )
    selected = all_results[expected].copy()
    selected["selection_rule"] = selected.timeframe.map(
        {"1m": "1M_SHARPE", "10m": "10M_BE_SHARPE", "15m": "15M_BE_SHARPE"}
    )
    frozen = selected.rename(
        columns={
            "Return": "Return_FIRST_TICK",
            "Sharpe": "Sharpe_FIRST_TICK",
            "Signed_BE_bps": "Signed_BE_FIRST_TICK",
            "Max_Drawdown": "MaxDD_FIRST_TICK",
            "Turnover_raw": "Turnover_FIRST_TICK",
        }
    )[[
        "strategy_id", "semantic_group_id", "source_origin", "symbol", "timeframe",
        "Return_FIRST_TICK", "Sharpe_FIRST_TICK", "Signed_BE_FIRST_TICK",
        "MaxDD_FIRST_TICK", "Turnover_FIRST_TICK", "selection_rule",
    ]].sort_values(["strategy_id", "timeframe", "symbol"]).reset_index(drop=True)
    manifest = WORK / "selection/first_tick_selected_cases.csv"
    atomic_csv(frozen, manifest)
    digest_before = digest(manifest)
    priority = frozen[frozen.Signed_BE_FIRST_TICK.gt(0)].sort_values(
        ["Signed_BE_FIRST_TICK", "Sharpe_FIRST_TICK", "Return_FIRST_TICK", "MaxDD_FIRST_TICK"],
        ascending=[False, False, False, False],
    )
    atomic_csv(priority, WORK / "selection/positive_be_priority.csv")
    freeze = {
        "status": "FROZEN_BEFORE_MAKER_EXECUTION",
        "selection_source": "FIRST_TICK_IDEALIZED_ONLY",
        "selection_window": "[2024-07-01, 2026-07-01)",
        "comparison_window": "[2024-03-01, 2024-03-31)",
        "maker_policy": "GTC_UNTIL_SIGNAL_INVALID",
        "maker_model": "L1_BBO_MAKER",
        "selected_cases": len(frozen),
        "selected_strategy_ids": int(frozen.strategy_id.nunique()),
        "selected_semantic_groups": int(frozen.semantic_group_id.nunique()),
        "workbook_strategy_ids": int(frozen.loc[frozen.source_origin.eq("WORKBOOK"), "strategy_id"].nunique()),
        "pre_workbook_strategy_ids": int(frozen.loc[frozen.source_origin.eq("PRE_WORKBOOK"), "strategy_id"].nunique()),
        "selected_by_timeframe": frozen.groupby("timeframe").size().to_dict(),
        "manifest_sha256": digest_before,
        "maker_selected_new_cases": 0,
        "parameter_search": 0,
        "threshold_optimization": 0,
    }
    atomic_json(freeze, WORK / "selection/selection_freeze.json")
    DELIVERY.mkdir(parents=True, exist_ok=True)
    atomic_csv(frozen, DELIVERY / "selection/first_tick_selected_cases.csv")
    atomic_csv(priority, DELIVERY / "selection/positive_be_priority.csv")
    atomic_json(freeze, DELIVERY / "selection/selection_freeze.json")
    if digest(manifest) != digest_before or digest(DELIVERY / "selection/first_tick_selected_cases.csv") != digest_before:
        raise ValueError("selection manifest freeze/copy mismatch")
    print(json.dumps(freeze, indent=2))


if __name__ == "__main__":
    main()
