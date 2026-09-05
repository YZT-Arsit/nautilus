#!/usr/bin/env python3
"""Re-render pilot figures from completed paths; never executes a backtest."""

from __future__ import annotations

from pathlib import Path
import sys

import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.internal.run_l1_maker_pilot import render_case  # noqa: E402


OUTPUT = ROOT / "outputs/baseline_evaluation/maker_execution_research/l1_pilot"


def main() -> None:
    models = pd.read_csv(OUTPUT / "maker_model_comparison.csv")
    orders = pd.read_csv(OUTPUT / "maker_orders.csv")
    fills = pd.read_csv(OUTPUT / "maker_fills.csv")
    keys = models.loc[
        models.execution_model.eq("L1_BBO_MAKER"), ["strategy_id", "symbol"]
    ].drop_duplicates()
    for row in keys.itertuples(index=False):
        comparison = models[
            models.strategy_id.eq(row.strategy_id)
            & models.symbol.eq(row.symbol)
            & models.execution_model.isin(["FIRST_TICK_IDEALIZED", "L1_BBO_MAKER"])
        ]
        path = pd.read_parquet(
            OUTPUT / "paths" / f"{row.strategy_id}__{row.symbol}__L1_BBO_MAKER.parquet"
        )
        case_orders = orders[
            orders.strategy_id.eq(row.strategy_id)
            & orders.symbol.eq(row.symbol)
            & orders.fill_probability.eq(0.5)
        ]
        case_fills = fills[
            fills.strategy_id.eq(row.strategy_id)
            & fills.symbol.eq(row.symbol)
            & fills.fill_probability.eq(0.5)
        ]
        render_case(
            OUTPUT, comparison, path, row.strategy_id, row.symbol, case_orders, case_fills
        )


if __name__ == "__main__":
    main()
