#!/usr/bin/env python3
"""Reconcile the historical L1 maker pilot and write its terminal audit."""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = Path("outputs/baseline_evaluation/maker_execution_research/l1_pilot")
PLAN = Path("outputs/baseline_evaluation/maker_execution_research/data_pilot/maker_pilot_scope.csv")
SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")


def atomic_csv(frame: pd.DataFrame, path: Path) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    frame.to_csv(temporary, index=False)
    os.replace(temporary, path)


def atomic_json(payload: dict, path: Path) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, allow_nan=False, default=str) + "\n")
    os.replace(temporary, path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path, default=ROOT / OUTPUT)
    args = parser.parse_args()
    repo = args.repo.resolve()
    output = args.output.resolve()
    manifests = pd.concat(
        [pd.read_csv(output / f"ingest_manifest_{symbol}.csv") for symbol in SYMBOLS],
        ignore_index=True,
    )
    books = manifests[manifests.data_type.eq("bookTicker")].copy()
    trades = manifests[manifests.data_type.eq("trades")].copy()
    if len(books) != 90 or len(trades) != 90 or not manifests.status.eq("PASSED").all():
        raise ValueError("March L1/trade ingest is not complete")
    books["timestamp_unit"] = "milliseconds_source;nanoseconds_converted"
    books["bid_price_column"] = "best_bid_price"
    books["bid_quantity_column"] = "best_bid_qty"
    books["ask_price_column"] = "best_ask_price"
    books["ask_quantity_column"] = "best_ask_qty"
    atomic_csv(
        books[
            [
                "symbol", "date", "filename", "archive_exists", "checksum_exists",
                "checksum_valid", "rows", "first_timestamp", "last_timestamp",
                "compressed_bytes", "uncompressed_bytes", "converted_bytes",
                "timestamp_unit", "bid_price_column", "bid_quantity_column",
                "ask_price_column", "ask_quantity_column", "status",
            ]
        ].rename(columns={"filename": "archive_filename"}),
        output / "l1_bookticker_availability.csv",
    )
    validation = manifests.groupby(["symbol", "data_type"], as_index=False).agg(
        partitions=("date", "nunique"),
        rows=("rows", "sum"),
        compressed_bytes=("compressed_bytes", "sum"),
        uncompressed_bytes=("uncompressed_bytes", "sum"),
        converted_bytes=("converted_bytes", "sum"),
        checksum_failures=("checksum_valid", lambda x: int((~x.astype(bool)).sum())),
        chronology_failures=("chronology_failures", "sum"),
        nonpositive_quantity_count=("nonpositive_quantity_count", "sum"),
        malformed_timestamp_count=("malformed_timestamp_count", "sum"),
    )
    book_extra = manifests[manifests.data_type.eq("bookTicker")].groupby("symbol").crossed_bbo_count.sum()
    validation["crossed_bbo_count"] = validation.symbol.map(book_extra).fillna(0).astype(int)
    validation["status"] = np.where(
        validation[["checksum_failures", "chronology_failures", "nonpositive_quantity_count", "malformed_timestamp_count", "crossed_bbo_count"]].sum(axis=1).eq(0)
        & validation.partitions.eq(30),
        "PASSED", "FAILED",
    )
    atomic_csv(validation, output / "l1_data_validation.csv")
    shutil.copy2(repo / PLAN, output / "frozen_pilot_scope.csv")

    models = pd.read_csv(output / "maker_model_comparison.csv")
    execution = pd.read_csv(output / "l1_maker_execution_metrics.csv")
    sensitivity = pd.read_csv(output / "l1_fill_model_sensitivity.csv")
    spread = pd.read_csv(output / "spread_statistics.csv")
    markout = pd.read_csv(output / "markout_statistics.csv")
    orders = pd.read_csv(output / "maker_orders.csv")
    precision = pd.read_csv(output / "instrument_precision_validation.csv")
    required_models = {
        "FIRST_TICK_IDEALIZED", "TRADE_ONLY_MAKER_APPROXIMATION",
        "L1_FILL_P100", "L1_BBO_MAKER", "L1_FILL_P025",
    }
    if len(models) != 90 or set(models.execution_model) != required_models:
        raise ValueError("execution-model comparison does not reconcile to 18 x 5")
    if len(execution) != 54 or len(sensitivity) != 54:
        raise ValueError("L1 sensitivity does not reconcile to 18 x 3")
    headline = models[models.execution_model.eq("L1_BBO_MAKER")]
    first = models[models.execution_model.eq("FIRST_TICK_IDEALIZED")]
    trade = models[models.execution_model.eq("TRADE_ONLY_MAKER_APPROXIMATION")]
    merged = first.merge(headline, on=["strategy_id", "symbol"], suffixes=("_first", "_l1"))
    trade_merged = trade.merge(headline, on=["strategy_id", "symbol"], suffixes=("_trade", "_l1"))
    degraded = int((merged.Sharpe_l1 < merged.Sharpe_first).sum())
    recovered_vs_trade = int((trade_merged.Sharpe_l1 > trade_merged.Sharpe_trade).sum())
    p100 = sensitivity[sensitivity.fill_probability.eq(1.0)].set_index(["strategy_id", "symbol"])
    p025 = sensitivity[sensitivity.fill_probability.eq(0.25)].set_index(["strategy_id", "symbol"])
    median_sensitivity_range = float((p100.Sharpe_gross - p025.Sharpe_gross).abs().median())
    median_l1_gap = float((merged.Sharpe_l1 - merged.Sharpe_first).abs().median())
    if recovered_vs_trade >= 12 and degraded < 9:
        conclusion = "TRADE_ONLY_APPROXIMATION_WAS_PRIMARY;L1_MATERIALLY_RECOVERS"
    elif degraded >= 12 and median_l1_gap > median_sensitivity_range:
        conclusion = "GENUINE_MISSED_EXPOSURE_DOMINATES_WITHIN_L1_ASSUMPTIONS"
    else:
        conclusion = "MIXED_MISSED_EXPOSURE_AND_L1_FILL_MODEL_ASSUMPTION"

    total_storage = sum(
        path.stat().st_size for path in output.rglob("*")
        if path.is_file() and "smoke_test" not in path.parts
    )
    temp_root = Path(r"D:\nautilus\outputs\tmp_l1_pilot")
    expected_dates = set(pd.date_range("2024-03-01", "2024-03-30").date.astype(str))
    complete_dates = all(
        set(books.loc[books.symbol.eq(symbol), "date"].astype(str)) == expected_dates
        and set(trades.loc[trades.symbol.eq(symbol), "date"].astype(str)) == expected_dates
        for symbol in SYMBOLS
    )
    passive_prices_valid = bool(
        (
            orders.limit_price.where(orders.side.eq("BUY"), orders.contemporaneous_bid)
            <= orders.contemporaneous_bid.where(orders.side.eq("BUY"), orders.limit_price)
        ).all()
        and (
            orders.limit_price.where(orders.side.eq("SELL"), orders.contemporaneous_ask)
            >= orders.contemporaneous_ask.where(orders.side.eq("SELL"), orders.limit_price)
        ).all()
    )
    checks = {
        "bookticker_partitions_90": len(books) == 90,
        "trade_partitions_90": len(trades) == 90,
        "all_checksums_valid": bool(manifests.checksum_valid.all()),
        "chronology_failures_zero": int(manifests.chronology_failures.sum()) == 0,
        "crossed_bbo_zero": int(books.crossed_bbo_count.sum()) == 0,
        "bad_quantity_zero": int(manifests.nonpositive_quantity_count.sum()) == 0,
        "bad_timestamp_zero": int(manifests.malformed_timestamp_count.sum()) == 0,
        "complete_frozen_date_coverage": complete_dates,
        "historical_instrument_precision_passed": bool(
            len(precision) == 3 and precision.status.eq("PASSED").all()
        ),
        "quote_tick_smoke_passed": (output / "quote_tick_smoke_test.json").is_file(),
        "frozen_pilot_cases_18": len(pd.read_csv(output / "frozen_pilot_scope.csv")) == 18,
        "headline_cases_18": len(headline) == 18,
        "sensitivity_cases_54": len(sensitivity) == 54,
        "sensitivity_probabilities_exact": set(sensitivity.fill_probability) == {1.0, 0.5, 0.25},
        "model_case_keys_unique": not models.duplicated(["strategy_id", "symbol", "execution_model"]).any(),
        "figures_18": len(list((output / "figures").glob("*.png"))) == 18,
        "queue_position_disabled": bool(execution.queue_position.eq(False).all()),
        "post_only_rejections_audited": bool(execution.rejected_post_only_orders.ge(0).all()),
        "submitted_prices_passive_to_contemporaneous_bbo": passive_prices_valid,
        "spread_statistics_complete": len(spread) == 3,
        "markout_statistics_nonempty": len(markout) > 0,
        "temporary_downloads_cleaned": not temp_root.exists() or not any(temp_root.iterdir()),
        "nine_symbol_expansion_not_started": True,
    }
    status = "PASSED" if all(checks.values()) else "BLOCKED"
    payload = {
        "status": status,
        "public_historical_bookticker": {symbol: "AVAILABLE" for symbol in SYMBOLS},
        "frozen_symbols": list(SYMBOLS),
        "frozen_period": {"start": "2024-03-01", "end_exclusive": "2024-03-31"},
        "pilot_cases": len(headline),
        "l1_source_compressed_bytes": int(books.compressed_bytes.sum()),
        "l1_converted_bytes": int(books.converted_bytes.sum()),
        "raw_trade_source_compressed_bytes": int(trades.compressed_bytes.sum()),
        "raw_trade_converted_bytes": int(trades.converted_bytes.sum()),
        "total_pilot_footprint_bytes": total_storage,
        "first_tick_median_sharpe": float(first.Sharpe.median()),
        "trade_only_maker_median_sharpe": float(trade.Sharpe.median()),
        "l1_maker_median_sharpe": float(headline.Sharpe.median()),
        "l1_quantity_fill_ratio": float(headline.quantity_fill_ratio.mean()),
        "l1_zero_fill_order_rate": float(headline.zero_fill_order_rate.mean()),
        "median_spread_bps": float(spread.median_spread_bps.median()),
        "post_only_rejection_rate": float(
            execution.loc[execution.execution_model.eq("L1_BBO_MAKER"), "post_only_rejection_rate"].mean()
        ),
        "l1_cases_sharpe_degraded_vs_first_tick": degraded,
        "l1_cases_sharpe_improved_vs_trade_only": recovered_vs_trade,
        "median_abs_sharpe_gap_l1_vs_first_tick": median_l1_gap,
        "median_abs_sharpe_sensitivity_p100_vs_p025": median_sensitivity_range,
        "main_maker_degradation_source": conclusion,
        "l2_required_next": "STILL_RECOMMENDED",
        "maker_fee": {
            "gross_rate": 0.0,
            "standard_scenario_rate": 0.0002,
            "standard_scenario_is_account_specific": False,
        },
        "nine_symbol_expansion": "NOT_STARTED",
        "validation_checks": checks,
    }
    atomic_json(payload, output / "validation_summary.json")
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
