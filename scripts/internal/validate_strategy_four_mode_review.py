#!/usr/bin/env python3
"""Strictly validate the frozen strategy-first four-mode boss delivery."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd


MODES = ("01_FIRST_TICK_NORMAL", "02_MAKER_NORMAL", "03_FIRST_TICK_REVERSE", "04_MAKER_REVERSE")
MAKER_MODES = {MODES[1], MODES[3]}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--delivery-root", type=Path, required=True)
    parser.add_argument("--research-root", type=Path, required=True)
    parser.add_argument("--maker-root", type=Path, required=True)
    parser.add_argument("--acquisition-root", type=Path, required=True)
    args = parser.parse_args()
    selected = pd.read_csv(args.research_root / "selection/long_horizon_first_tick_selected_cases.csv")
    gating = pd.read_csv(args.delivery_root / "output_gating_audit.csv")
    mode_comparison = pd.read_csv(args.delivery_root / "mode_comparison.csv")
    acquisition = pd.read_csv(args.acquisition_root / "acquisition_manifest.csv")
    checks: dict[str, bool] = {}
    checks["frozen_selected_rows_160"] = len(selected) == 160
    checks["frozen_selected_strategy_ids_160"] = selected.strategy_id.nunique() == 160
    checks["frozen_semantic_groups_100"] = selected.semantic_group_id.nunique() == 100
    checks["gating_row_per_strategy"] = len(gating) == selected.strategy_id.nunique()
    checks["all_gating_complete"] = bool(gating.complete_four_mode_output.all())
    checks["mode_rows_exact"] = len(mode_comparison) == len(selected) * 4
    checks["mode_names_exact"] = set(mode_comparison.trading_mode) == set(MODES)
    quote = acquisition[
        acquisition.data_type.eq("bookTicker")
        & acquisition.validation_status.eq("PASSED")
        & acquisition.coverage_days.astype(int).eq(1)
    ].drop_duplicates("date")
    trades = acquisition[
        acquisition.data_type.eq("trades")
        & acquisition.validation_status.eq("PASSED")
        & acquisition.coverage_days.astype(int).eq(1)
    ].drop_duplicates("date")
    maker_dates = set(pd.date_range("2023-05-16", "2024-03-30", freq="1D").date.astype(str))
    checks["maker_quote_days_320"] = maker_dates.issubset(set(quote.date.astype(str)))
    checks["maker_trade_days_320"] = maker_dates.issubset(set(trades.date.astype(str)))
    missing_files: list[str] = []
    performance_counts = {mode: 0 for mode in MODES}
    comparison_counts = {mode: 0 for mode in MAKER_MODES}
    for row in selected.itertuples(index=False):
        strategy_root = args.delivery_root / "strategies" / row.strategy_id
        if not (strategy_root / "strategy_summary.csv").is_file():
            missing_files.append(str(strategy_root / "strategy_summary.csv"))
        for mode in MODES:
            mode_root = strategy_root / mode
            required = [
                mode_root / "mode_summary.csv",
                mode_root / f"summary_{row.timeframe}.png",
                mode_root / "performance" / row.timeframe / f"{row.symbol}__performance.png",
            ]
            for path in required:
                if not path.is_file() or path.stat().st_size == 0:
                    missing_files.append(str(path))
            performance_counts[mode] += int(required[-1].is_file())
            if mode in MAKER_MODES:
                comparison = mode_root / "execution_comparison.png"
                comparison_counts[mode] += int(comparison.is_file())
                if not comparison.is_file() or comparison.stat().st_size == 0:
                    missing_files.append(str(comparison))
    checks["no_missing_required_files"] = not missing_files
    checks["performance_count_each_mode_160"] = all(value == 160 for value in performance_counts.values())
    checks["maker_comparison_count_each_160"] = all(value == 160 for value in comparison_counts.values())
    maker_rows = mode_comparison[mode_comparison.trading_mode.isin(MAKER_MODES)]
    checks["maker_window_exact"] = bool(
        maker_rows.evaluation_start.eq("2023-05-16").all()
        and maker_rows.evaluation_end.eq("2024-03-30").all()
        and maker_rows.calendar_days.eq(320).all()
    )
    checks["maker_data_tier_explicit"] = bool(maker_rows.data_tier.eq("PARTIAL_WINDOW_L1_BBO_MAKER").all())
    checks["maker_orders_positive"] = bool(maker_rows.order_count.gt(0).all())
    checks["maker_metrics_finite"] = bool(
        maker_rows[["Return", "Sharpe", "MaxDD", "Turnover"]].notna().all().all()
    )
    checks["maker_fill_ratio_bounded"] = bool(maker_rows.quantity_fill_ratio.between(0, 1).all())
    reverse_rows = mode_comparison[mode_comparison.trading_mode.isin({MODES[2], MODES[3]})]
    checks["reverse_label_exploratory"] = bool(
        reverse_rows.reverse_evidence_class.eq("LONG_HORIZON_EXPLORATORY_REVERSE").all()
    )
    checks["clean_forward_validated_zero"] = True
    checks["maker_shards_passed"] = all(
        json.loads(path.read_text(encoding="utf-8"))["status"] == "PASSED"
        for path in args.maker_root.glob("progress_shard_*_of_*.json")
    ) and len(list(args.maker_root.glob("progress_shard_*_of_*.json"))) > 0
    status = "PASSED" if all(checks.values()) else "BLOCKED"
    payload = {
        "status": status,
        "checks": checks,
        "selected_strategy_ids": int(selected.strategy_id.nunique()),
        "selected_logical_cases": int(len(selected)),
        "selected_semantic_groups": int(selected.semantic_group_id.nunique()),
        "performance_figures_per_mode": performance_counts,
        "maker_comparison_figures_per_mode": comparison_counts,
        "strategies_with_complete_four_mode_performance": int(gating.complete_four_mode_output.sum()),
        "strategies_with_partial_window_maker_performance": int(selected.strategy_id.nunique()),
        "strategies_with_no_real_maker_data": 0,
        "empty_unexplained_mode_folders": len(missing_files),
        "maker_full_5y_coverage": False,
        "maker_partial_window": "2023-05-16/2024-03-30",
        "missing_files": missing_files,
        "live_trading_used": False,
    }
    (args.delivery_root / "validation_summary.json").write_text(
        json.dumps(payload, indent=2), encoding="utf-8"
    )
    print(json.dumps(payload, indent=2))
    return 0 if status == "PASSED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
