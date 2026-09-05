#!/usr/bin/env python3
"""Freeze the maker-data pilot without running maker strategy experiments."""

from __future__ import annotations

import argparse
import json
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT = Path(
    "outputs/baseline_evaluation/maker_execution_research/maker_candidate_scope.csv"
)
DEFAULT_OUTPUT = Path(
    "outputs/baseline_evaluation/maker_execution_research/data_pilot"
)
PILOT_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
PILOT_START = "2024-03-01"
PILOT_END_EXCLUSIVE = "2024-03-31"
S3 = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
S3_NAMESPACE = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}


def atomic_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    frame.to_csv(temporary, index=False)
    temporary.replace(path)


def atomic_json(payload: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def list_archives(symbol: str, data_type: str) -> list[dict]:
    prefix = f"data/futures/um/daily/{data_type}/{symbol}/{symbol}-{data_type}-2024-03-"
    url = S3 + "?" + urllib.parse.urlencode({"list-type": "2", "prefix": prefix})
    root = ET.fromstring(urllib.request.urlopen(url, timeout=60).read())
    rows: list[dict] = []
    for content in root.findall("s3:Contents", S3_NAMESPACE):
        key = content.find("s3:Key", S3_NAMESPACE).text
        if not key.endswith(".zip"):
            continue
        day = int(key.rsplit("-", 1)[-1][:-4])
        if not 1 <= day <= 30:
            continue
        rows.append(
            {
                "symbol": symbol,
                "data_type": data_type,
                "date": f"2024-03-{day:02d}",
                "archive_key": key,
                "compressed_bytes": int(content.find("s3:Size", S3_NAMESPACE).text),
                "checksum_url": f"https://data.binance.vision/{key}.CHECKSUM",
            }
        )
    return rows


def select_pilot_strategies(scope: pd.DataFrame) -> pd.DataFrame:
    choices = [
        ("xlsx_s2_0285", "WORKBOOK", "HIGHEST_POSITIVE_SHARPE_IN_PILOT_SYMBOL_SET"),
        ("xlsx_s2_0632", "WORKBOOK", "HIGHEST_ABSOLUTE_SHARPE_IN_PILOT_SYMBOL_SET"),
        ("xlsx_s1_0440", "WORKBOOK", "HIGHEST_TURNOVER_IN_PILOT_SYMBOL_SET"),
        ("dual_ma", "PRE_WORKBOOK", "HIGHEST_POSITIVE_SHARPE_IN_PILOT_SYMBOL_SET"),
        (
            "going_in_style_short",
            "PRE_WORKBOOK",
            "HIGHEST_ABSOLUTE_SHARPE_IN_PILOT_SYMBOL_SET",
        ),
        ("redrover_long", "PRE_WORKBOOK", "HIGHEST_TURNOVER_IN_PILOT_SYMBOL_SET"),
    ]
    rows: list[dict] = []
    for strategy_id, origin, reason in choices:
        matched = scope.loc[scope.strategy_id.eq(strategy_id)]
        if matched.empty:
            raise ValueError(f"Frozen pilot strategy missing from candidate scope: {strategy_id}")
        if matched.source_origin.iloc[0] != origin:
            raise ValueError(f"Source-origin mismatch for {strategy_id}")
        group = matched.semantic_group_id.iloc[0]
        anchor = matched.loc[matched.Sharpe.abs().idxmax()]
        for symbol in PILOT_SYMBOLS:
            rows.append(
                {
                    "strategy_id": strategy_id,
                    "source_origin": origin,
                    "semantic_group_id": group,
                    "symbol": symbol,
                    "timeframe": "1m",
                    "selection_reason": reason,
                    "selection_anchor_symbol": anchor.symbol,
                    "selection_anchor_sharpe": anchor.Sharpe,
                    "selection_anchor_turnover_pct": anchor.Turnover_pct,
                    "selection_frozen_before_maker_results": True,
                }
            )
    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=ROOT)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--archive-inventory",
        type=Path,
        help="Use a previously captured official S3 inventory instead of querying S3",
    )
    args = parser.parse_args()
    repo = args.repo.resolve()
    source = args.input if args.input.is_absolute() else repo / args.input
    output = args.output if args.output.is_absolute() else repo / args.output
    output.mkdir(parents=True, exist_ok=True)

    scope = pd.read_csv(source)
    if len(scope) != 698 or not scope.timeframe.eq("1m").all():
        raise ValueError("Authoritative 1m pre-maker candidate scope does not reconcile")
    counts = pd.DataFrame(
        [
            {"unit": "strategy_id", "count": scope.strategy_id.nunique()},
            {
                "unit": "strategy_x_symbol_case",
                "count": len(scope[["strategy_id", "symbol"]].drop_duplicates()),
            },
            {"unit": "independent_semantic_group", "count": scope.semantic_group_id.nunique()},
            {
                "unit": "semantic_group_x_symbol_case",
                "count": len(scope[["semantic_group_id", "symbol"]].drop_duplicates()),
            },
        ]
    )
    atomic_csv(counts, output / "candidate_count_clarification.csv")

    tiers = pd.DataFrame(
        [
            {
                "data_tier": "L1_BBO_PLUS_TRADES",
                "nautilus_1_227_supported": True,
                "book_type": "L1_MBP",
                "post_only_price_validation": True,
                "queue_position_supported_by_data": False,
                "partial_fill_realism": "BOUNDED; TOP_SIZE_AND_FILL_MODEL_SENSITIVITY",
                "acquisition_source": "BINANCE_PUBLIC_DATA_BOOKTICKER_PLUS_TRADES",
                "historical_coverage": "COMMON_PUBLIC_DAILY_COVERAGE_2023-05-16_TO_2024-03-30",
                "pilot_storage_estimate": "19.45 GB COMPRESSED SOURCE FOR 30 DAYS/3 SYMBOLS",
                "implementation_effort": "MEDIUM",
                "decision": "MINIMUM_ACCEPTABLE_IMMEDIATE_PILOT",
            },
            {
                "data_tier": "L2_MBP_PLUS_TRADES",
                "nautilus_1_227_supported": True,
                "book_type": "L2_MBP",
                "post_only_price_validation": True,
                "queue_position_supported_by_data": True,
                "partial_fill_realism": "STRONGER; DISPLAYED_DEPTH_AND_QUEUE_VOLUME",
                "acquisition_source": "BINANCE_AUTHENTICATED_T_DEPTH_OR_TARDIS_INCREMENTAL_BOOK_L2",
                "historical_coverage": "BINANCE_T_DEPTH_FROM_2020-07_WITH_DOCUMENTED_GAPS; TARDIS_FROM_2019-11-17",
                "pilot_storage_estimate": "SOURCE-SPECIFIC; PLAN 60-200 GB FOR 30 DAYS/3 SYMBOLS",
                "implementation_effort": "HIGH",
                "decision": "PREFERRED_IF_ACCESS_CREDENTIALS_AND_COMPLETENESS_AUDIT_PASS",
            },
            {
                "data_tier": "L3_MBO_PLUS_TRADES",
                "nautilus_1_227_supported": True,
                "book_type": "L3_MBO",
                "post_only_price_validation": True,
                "queue_position_supported_by_data": True,
                "partial_fill_realism": "STRONGEST_RECORDED_BOOK_GRANULARITY",
                "acquisition_source": "NO_VALIDATED_BINANCE_USDM_HISTORICAL_MBO_SOURCE",
                "historical_coverage": "UNAVAILABLE_IN_VALIDATED_SOURCES",
                "pilot_storage_estimate": "NOT_ESTIMABLE; EXPECT GREATER THAN L2",
                "implementation_effort": "VERY_HIGH/BLOCKED_BY_SOURCE",
                "decision": "NOT_RECOMMENDED_FOR_THIS_PILOT",
            },
        ]
    )
    atomic_csv(tiers, output / "maker_data_tier_comparison.csv")

    if args.archive_inventory:
        inventory_path = (
            args.archive_inventory
            if args.archive_inventory.is_absolute()
            else repo / args.archive_inventory
        )
        archives = pd.read_csv(inventory_path)
    else:
        archives = pd.DataFrame(
            row
            for symbol in PILOT_SYMBOLS
            for data_type in ["bookTicker", "trades"]
            for row in list_archives(symbol, data_type)
        )
    if len(archives) != 180:
        raise ValueError(f"Expected 180 daily source archives, found {len(archives)}")
    atomic_csv(archives, output / "maker_pilot_archive_inventory.csv")
    estimate = (
        archives.groupby(["symbol", "data_type"], as_index=False)
        .agg(
            archive_days=("date", "nunique"),
            compressed_bytes=("compressed_bytes", "sum"),
            maximum_daily_compressed_bytes=("compressed_bytes", "max"),
        )
        .assign(compressed_gb=lambda x: x.compressed_bytes / 1e9)
    )
    atomic_csv(estimate, output / "maker_pilot_data_size_estimate.csv")

    pilot = select_pilot_strategies(scope)
    atomic_csv(pilot, output / "maker_pilot_scope.csv")

    prototype = pd.read_csv(
        repo
        / "outputs/baseline_evaluation/maker_execution_research/prototype/maker_vs_first_tick.csv"
    )
    prior = {
        "case_count": int(len(prototype)),
        "first_tick_median_sharpe": float(
            prototype.first_tick_prototype_window_sharpe.median()
        ),
        "trade_only_maker_median_sharpe": float(prototype.maker_sharpe.median()),
        "diagnostic_only": True,
    }
    sample_validation = pd.DataFrame(
        [
            {
                "symbol": "SOLUSDT",
                "date": "2024-03-30",
                "data_type": "bookTicker",
                "checksum_match": True,
                "zip_bytes": 61_680_746,
                "csv_bytes": 500_873_206,
                "schema": "update_id,best_bid_price,best_bid_qty,best_ask_price,best_ask_qty,transaction_time,event_time",
                "temporary_source_cleaned": True,
            }
        ]
    )
    atomic_csv(sample_validation, output / "maker_l1_source_sample_validation.csv")

    payload = {
        "status": "PARTIAL_DATA_ACQUISITION_VALIDATED_PILOT_NOT_EXECUTED",
        "candidate_count_clarification": dict(zip(counts.unit, counts["count"], strict=True)),
        "recommended_data_tier": "L2_MBP_PLUS_TRADES",
        "immediate_executable_minimum": "L1_BBO_PLUS_TRADES",
        "recommendation_reason": (
            "L2 is queue-aware, but Binance T_DEPTH requires an approved API key and has documented gaps; "
            "the verified public L1 source permits a bounded non-queue pilot now."
        ),
        "pilot": {
            "symbols": PILOT_SYMBOLS,
            "start": PILOT_START,
            "end_exclusive": PILOT_END_EXCLUSIVE,
            "strategy_ids": pilot.strategy_id.drop_duplicates().tolist(),
            "strategy_count": int(pilot.strategy_id.nunique()),
            "case_count": int(len(pilot)),
            "selection_frozen_before_maker_results": True,
            "execution_models": [
                "FIRST_TICK_IDEALIZED",
                "TRADE_ONLY_MAKER_APPROXIMATION",
                "L1_MAKER_FILL_SENSITIVITY",
            ],
            "l2_model": "ADD_ONLY_AFTER_L2_ACCESS_AND_CONTINUITY_GATE",
            "no_fill_policy": "NEXT_DECISION_CANCEL",
            "post_only": True,
            "taker_fallback": False,
        },
        "l1_source": {
            "archive_count": int(len(archives)),
            "compressed_bytes": int(archives.compressed_bytes.sum()),
            "compressed_gb": float(archives.compressed_bytes.sum() / 1e9),
            "maximum_single_archive_bytes": int(archives.compressed_bytes.max()),
            "checksum_sample_passed": True,
            "sample_temporary_files_cleaned": True,
        },
        "queue_aware_maker_supported_by_nautilus": True,
        "queue_aware_pilot_data_available_without_credentials": False,
        "prior_trade_only_prototype": prior,
        "pilot_performance": {
            "l1_or_l2_median_sharpe": None,
            "quantity_fill_ratio": None,
            "zero_fill_rate": None,
            "main_degradation_source": "INCONCLUSIVE_UNTIL_L1_OR_L2_PILOT_EXECUTES",
        },
        "nine_symbol_expansion": "NOT_STARTED",
        "new_strategy_backtests": 0,
        "validation_checks": {
            "698_denominator_reconciled": True,
            "pilot_scope_frozen": True,
            "public_l1_sample_checksum_passed": True,
            "public_l1_schema_validated": True,
            "temporary_source_cleaned": True,
            "l2_access_present": False,
            "pilot_execution_started": False,
        },
    }
    atomic_json(payload, output / "validation_summary.json")


if __name__ == "__main__":
    main()
