#!/usr/bin/env python3
"""Build the deterministic physical-to-logical selected-case mapping."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
WORK = ROOT / "outputs/baseline_evaluation/execution_method_and_reverse_review"


def main() -> None:
    selected = pd.read_csv(WORK / "selection/first_tick_selected_cases.csv")
    rows = []
    for symbol, symbol_rows in selected.groupby("symbol"):
        physical = symbol_rows.sort_values("strategy_id").drop_duplicates(
            ["semantic_group_id", "timeframe"]
        )
        for row in physical.itertuples(index=False):
            case_key = "case_" + hashlib.sha256(
                f"{row.semantic_group_id}|{row.timeframe}".encode()
            ).hexdigest()[:20]
            logical = symbol_rows.loc[
                symbol_rows.semantic_group_id.eq(row.semantic_group_id)
                & symbol_rows.timeframe.eq(row.timeframe), "strategy_id"
            ]
            rows.append(
                {
                    "symbol":symbol, "case_key":case_key,
                    "semantic_group_id":row.semantic_group_id, "timeframe":row.timeframe,
                    "representative_strategy_id":row.strategy_id,
                    "logical_strategy_ids":";".join(sorted(logical.unique())),
                }
            )
    output = WORK / "maker_signals/maker_case_mapping.csv"
    output.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).sort_values(["symbol", "timeframe", "case_key"]).to_csv(output, index=False)
    print(f"physical cases={len(rows)}")


if __name__ == "__main__":
    main()
