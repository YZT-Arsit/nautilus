#!/usr/bin/env python3
"""Render only strict-rule reverse cases absent from the previous delivery."""

from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.internal.finalize_execution_method_reverse_review import (
    render_reverse_figures,
    render_reverse_maker_sensitivity,
)


WORK = ROOT / "outputs/baseline_evaluation/execution_method_and_reverse_review"
DELIVERY = ROOT / "outputs/deliverables/execution_method_and_reverse_review"
MISSING = {
    ("xlsx_s2_0408", "BNBUSDT", "15m"),
    ("xlsx_s2_0605", "BNBUSDT", "15m"),
    ("xlsx_s2_0782", "BNBUSDT", "15m"),
}


def main() -> None:
    cases = pd.read_csv(WORK / "reverse_validation/reverse_case_comparison.csv")
    selected = cases[
        cases.apply(
            lambda row: (row.strategy_id, row.symbol, row.timeframe) in MISSING,
            axis=1,
        )
    ]
    if len(selected) != 3:
        raise ValueError(f"expected 3 missing logical cases, found {len(selected)}")
    render_reverse_figures(WORK, DELIVERY, selected)
    maker = render_reverse_maker_sensitivity(WORK, DELIVERY, selected)
    if maker != 6:
        raise ValueError(f"expected 6 maker sensitivity figures, found {maker}")
    print("rendered_first_tick=12 rendered_maker=6")


if __name__ == "__main__":
    main()
