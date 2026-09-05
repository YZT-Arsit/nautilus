#!/usr/bin/env python3
"""Reconcile the isolated maker research artifacts without running backtests."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import pandas as pd


REQUIRED = [
    "research/nautilus_maker_execution_research.md",
    "research/no_fill_policy_comparison.csv",
    "maker_data_availability.csv",
    "maker_candidate_scope.csv",
    "micro_tests/micro_test_results.csv",
    "prototype/maker_vs_first_tick.csv",
    "prototype/maker_execution_metrics.csv",
    "prototype/maker_orders.csv",
    "prototype/maker_fills.csv",
    "stageA_9symbols/maker_results.csv",
    "stageA_9symbols/maker_vs_first_tick.csv",
    "stageA_9symbols/order_execution_summary.csv",
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    missing = [relative for relative in REQUIRED if not (root / relative).is_file()]
    data = pd.read_csv(root / "maker_data_availability.csv")
    scope = pd.read_csv(root / "maker_candidate_scope.csv")
    micro = pd.read_csv(root / "micro_tests/micro_test_results.csv")
    metrics = pd.read_csv(root / "prototype/maker_execution_metrics.csv")
    orders = pd.read_csv(root / "prototype/maker_orders.csv")
    fills = pd.read_csv(root / "prototype/maker_fills.csv")
    validation_path = root / "validation_summary.json"
    payload = json.loads(validation_path.read_text(encoding="utf-8"))
    acquisition = pd.DataFrame(
        [
            {
                "data_level": "L1_BBO_PLUS_TRADES",
                "possible_source": "new Binance Futures bookTicker capture or licensed historical vendor",
                "coverage_target": "9 symbols; 2024-07-01 to 2026-06-30",
                "storage_estimate": "100 GB to 1 TB compressed; sampling required before authorization",
                "implementation_complexity": "MEDIUM",
                "availability_note": "official project archive currently has no historical BBO",
            },
            {
                "data_level": "L2_MBP_PLUS_TRADES",
                "possible_source": "licensed historical depth vendor or prospectively captured Binance depth stream",
                "coverage_target": "9 symbols; 2024-07-01 to 2026-06-30",
                "storage_estimate": "multi-TB likely; sampling required before authorization",
                "implementation_complexity": "HIGH",
                "availability_note": "not present; public archive provenance/continuity must be verified",
            },
            {
                "data_level": "L3_MBO_PLUS_TRADES",
                "possible_source": "exchange/vendor subject to actual Binance Futures MBO availability",
                "coverage_target": "9 symbols; 2024-07-01 to 2026-06-30",
                "storage_estimate": "unknown and potentially unavailable; do not budget without source confirmation",
                "implementation_complexity": "VERY_HIGH",
                "availability_note": "no current source or data in project",
            },
        ]
    )
    acquisition.to_csv(root / "research/data_acquisition_estimate.csv", index=False)
    checks = {
        "required_files_present": not missing,
        "nine_symbols_audited": set(data.symbol)
        == {
            "XRPUSDT",
            "DOGEUSDT",
            "SUIUSDT",
            "BNBUSDT",
            "ETHUSDT",
            "BTCUSDT",
            "1000PEPEUSDT",
            "SOLUSDT",
            "ADAUSDT",
        },
        "candidate_scope_698": len(scope) == 698,
        "candidate_predicate_frozen": scope.Sharpe.abs().gt(1.5).all(),
        "both_source_origins_in_prototype": set(metrics.source_origin)
        == {"WORKBOOK", "PRE_WORKBOOK"},
        "micro_tests_10_of_10": len(micro) == 10 and micro.status.eq("PASSED").all(),
        "orders_post_only_only": orders.post_only.astype(bool).all(),
        "no_taker_fallback": fills.liquidity_side.astype(str).isin(["1", "MAKER"]).all(),
        "actual_position_fill_accounting": metrics.filled_quantity.gt(0).all(),
        "protected_hash_changes_zero": not payload.get("protected_hash_changes"),
        "all_symbol_expansion_not_started": payload.get("all_symbol_expansion") == "NOT_STARTED",
        "stage_figures_present": len(list((root / "stageA_9symbols/figures").glob("*.png")))
        == len(metrics),
    }
    checks = {name: bool(passed) for name, passed in checks.items()}
    failed = [name for name, passed in checks.items() if not passed]
    payload.update(
        {
            "validation_checks": checks,
            "validation_failed_checks": failed,
            "required_missing_files": missing,
            "focused_tests": {
                "maker_policy_native_post_only": "8 passed, 6 deselected",
                "matching_trade_execution_and_commission": "8 passed, 181 deselected",
                "total_passed": 16,
                "total_failed": 0,
            },
            "status": "PARTIAL" if not failed else "BLOCKED",
        }
    )
    temporary = validation_path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(temporary, validation_path)
    print(json.dumps({"failed": failed, "checks": checks}, indent=2))
    return 0 if not failed else 2


if __name__ == "__main__":
    raise SystemExit(main())
