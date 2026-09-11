#!/usr/bin/env python3
"""Build a read-only audit layer over the completed reverse study."""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
DELIVERY = Path("outputs/deliverables/strategy_execution_reverse_review")
WORK = Path("outputs/baseline_evaluation/execution_method_and_reverse_review")


def atomic_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    frame.to_csv(temp, index=False)
    os.replace(temp, path)


def atomic_json(value: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    os.replace(temp, path)


def truth(value: object) -> bool:
    return str(value).strip().lower() == "true"


def display_name(strategy_id: str) -> str:
    if strategy_id.startswith("xlsx_s"):
        parts = strategy_id.split("_")
        return f"Workbook {parts[1].upper()} {parts[2]}"
    words = strategy_id.split("_")
    if words[-1] in {"long", "short"}:
        side = words.pop().title()
        return f"{' '.join(word.title() for word in words)} — {side}"
    return " ".join(word.title() for word in words)


def main() -> None:  # noqa: C901
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=ROOT)
    args = parser.parse_args()
    repo = args.repo.resolve()
    delivery, work = repo / DELIVERY, repo / WORK
    reverse = pd.read_csv(delivery / "reverse_validation/reverse_case_comparison.csv")
    freeze = json.loads((delivery / "selection/selection_freeze.json").read_text())

    strict_discovery = (
        (
            reverse.timeframe.eq("1m")
            & reverse.Return_NORMAL_DISCOVERY.lt(0)
            & reverse.Sharpe_NORMAL_DISCOVERY.lt(-1.5)
        )
        | (
            reverse.timeframe.isin(["10m", "15m"])
            & reverse.Return_NORMAL_DISCOVERY.lt(0)
            & reverse.Sharpe_NORMAL_DISCOVERY.lt(-1.0)
            & reverse.Signed_BE_bps_NORMAL_DISCOVERY.lt(-10.0)
        )
    )
    provisional_positive = (
        strict_discovery
        & reverse.Return_REVERSE.gt(0)
        & reverse.Sharpe_REVERSE.gt(0)
        & reverse.Signed_BE_bps_REVERSE.gt(0)
        & reverse.Return_REVERSE.gt(reverse.Return_NORMAL)
    )
    # The parent FIRST_TICK selection window spans the later validation period.
    # This cannot be repaired retroactively by relabeling a subset.
    selection_overlaps_validation = True
    provenance = pd.DataFrame({
        "strategy_id": reverse.strategy_id,
        "display_name": reverse.strategy_id.map(display_name),
        "symbol": reverse.symbol,
        "timeframe": reverse.timeframe,
        "discovery_start": reverse.discovery_start,
        "discovery_end": reverse.discovery_end_exclusive,
        "NORMAL_discovery_Return": reverse.Return_NORMAL_DISCOVERY,
        "NORMAL_discovery_Sharpe": reverse.Sharpe_NORMAL_DISCOVERY,
        "NORMAL_discovery_BE": reverse.Signed_BE_bps_NORMAL_DISCOVERY,
        "reverse_selected_from_discovery": strict_discovery,
        "validation_start": reverse.validation_start,
        "validation_end": reverse.validation_end_exclusive,
        "NORMAL_validation_Return": reverse.Return_NORMAL,
        "NORMAL_validation_Sharpe": reverse.Sharpe_NORMAL,
        "NORMAL_validation_BE": reverse.Signed_BE_bps_NORMAL,
        "REVERSE_validation_Return": reverse.Return_REVERSE,
        "REVERSE_validation_Sharpe": reverse.Sharpe_REVERSE,
        "REVERSE_validation_BE": reverse.Signed_BE_bps_REVERSE,
        "validation_data_used_in_selection": selection_overlaps_validation,
        "leakage_status": "CONTAMINATED_PARENT_FULL_PERIOD_SELECTION",
        "provisional_validation_positive_not_clean": provisional_positive,
    })
    atomic_csv(provenance, delivery / "reverse_validation_provenance.csv")

    source = pd.read_csv(delivery / "data_provenance/maker_market_data_source.csv")
    availability = pd.read_csv(delivery / "data_provenance/maker_archive_availability.csv")
    maker = pd.read_csv(work / "reverse_maker_comparison/execution_metrics.csv")
    maker = maker[maker.execution_model.eq("STRICT_REVERSE_GTC_UNTIL_SIGNAL_INVALID")].copy()
    mapping = pd.read_csv(work / "maker_signals/maker_case_mapping.csv")
    case_key = mapping.set_index(["symbol", "semantic_group_id", "timeframe"])["case_key"]
    maker_lookup = maker.set_index(["symbol", "case_key"])

    source_summary: dict[tuple[str, str], dict] = {}
    for (symbol, source_type), group in source.groupby(["symbol", "source_type"]):
        source_summary[(symbol, source_type)] = {
            "rows": int(group.rows.sum()),
            "first": float(group.first_timestamp.min()),
            "last": float(group.last_timestamp.max()),
            "archives": ";".join(group.source_archive.astype(str)),
            "urls": ";".join(group.source_url.astype(str)),
            "checksums": ";".join(group.checksum.astype(str)),
            "converted_paths": ";".join(group.converted_path.astype(str)),
            "converted_hashes": ";".join(group.converted_sha256.astype(str)),
            "checksum_valid": bool(group.checksum_valid.map(truth).all()),
            "validation": bool(group.validation_status.eq("PASSED").all()),
        }
    compressed = availability.groupby(["symbol", "source_type"]).compressed_bytes.sum().to_dict()
    coverage_rows = []
    for row in reverse.itertuples(index=False):
        key = case_key.loc[(row.symbol, row.semantic_group_id, row.timeframe)]
        if isinstance(key, pd.Series):
            key = key.iloc[0]
        metric = maker_lookup.loc[(row.symbol, key)]
        quote = source_summary[(row.symbol, "L1_BBO")]
        trade = source_summary[(row.symbol, "RAW_TRADES")]
        complete = (
            truth(metric.post_only) and truth(metric.trade_execution)
            and metric.execution_model == "STRICT_REVERSE_GTC_UNTIL_SIGNAL_INVALID"
            and quote["checksum_valid"] and trade["checksum_valid"]
            and quote["validation"] and trade["validation"]
            and int(metric.filled_orders) > 0
        )
        coverage_rows.append({
            "strategy_id": row.strategy_id,
            "display_name": display_name(row.strategy_id),
            "semantic_group_id": row.semantic_group_id,
            "symbol": row.symbol,
            "timeframe": row.timeframe,
            "physical_case_key": key,
            "start": "2024-03-01",
            "end": "2024-03-31",
            "quote_source": "Binance USD-M Futures historical bookTicker -> Nautilus QuoteTick",
            "trade_source": "Binance USD-M Futures historical trades -> Nautilus TradeTick",
            "bookTicker_archive_path": quote["urls"],
            "trade_archive_path": trade["urls"],
            "bookTicker_checksums": quote["checksums"],
            "trade_checksums": trade["checksums"],
            "bookTicker_converted_paths": quote["converted_paths"],
            "trade_converted_paths": trade["converted_paths"],
            "bookTicker_converted_sha256": quote["converted_hashes"],
            "trade_converted_sha256": trade["converted_hashes"],
            "bookTicker_compressed_bytes": int(compressed[(row.symbol, "L1_BBO")]),
            "trade_compressed_bytes": int(compressed[(row.symbol, "RAW_TRADES")]),
            "L1_rows": quote["rows"],
            "trade_rows": trade["rows"],
            "Nautilus_QuoteTick_count": quote["rows"],
            "Nautilus_TradeTick_count": trade["rows"],
            "actual_maker_order_count": int(metric.submitted_orders),
            "actual_maker_fill_count": int(metric.filled_orders),
            "post_only": truth(metric.post_only),
            "trade_execution": truth(metric.trade_execution),
            "queue_position": truth(metric.queue_position),
            "maker_class": "L1_BBO_MAKER",
            "maker_case_status": (
                "L1_BBO_MAKER_REVERSE_COMPLETED"
                if complete else "L1_BBO_MAKER_DATA_COMPLETE_NO_ORDERFILLED_EVENT"
            ),
            "MAKER_REVERSE_COMPLETED": complete,
        })
    coverage = pd.DataFrame(coverage_rows)
    atomic_csv(coverage, delivery / "maker_reverse_data_coverage.csv")

    names = pd.DataFrame({"internal_strategy_id": sorted(reverse.strategy_id.unique())})
    names["display_name"] = names.internal_strategy_id.map(display_name)
    atomic_csv(names, delivery / "strategy_display_names.csv")

    inverse_return = np.isclose(reverse.Return_REVERSE, -reverse.Return_NORMAL, atol=1e-12, rtol=1e-12)
    inverse_sharpe = np.isclose(reverse.Sharpe_REVERSE, -reverse.Sharpe_NORMAL, atol=1e-12, rtol=1e-12)
    inverse_be = np.isclose(reverse.Signed_BE_bps_REVERSE, -reverse.Signed_BE_bps_NORMAL, atol=1e-12, rtol=1e-12)
    result = {
        "status": "CORRECTED",
        "reported_reverse_candidates": int(len(reverse)),
        "strict_discovery_rule_eligible_within_contaminated_parent_pool": int(strict_discovery.sum()),
        "provisional_positive_within_contaminated_parent_pool": int(provisional_positive.sum()),
        "clean_temporally_validated_reverse_positive": 0,
        "clean_validated_reverse_strategy_ids": 0,
        "temporal_leakage_rows": int(len(reverse)),
        "selection_window": freeze["selection_window"],
        "discovery_window": "[2024-07-01, 2025-07-01)",
        "validation_window": "[2025-07-01, 2026-06-30)",
        "exploratory_reverse_positive": int(reverse.exploratory_positive.map(truth).sum()),
        "previously_reported_validation_positive": int(reverse.validation_positive.map(truth).sum()),
        "previous_success_rate": float(reverse.validation_positive.map(truth).mean()),
        "exact_sign_inverse_return_cases": int(inverse_return.sum()),
        "exact_sign_inverse_sharpe_cases": int(inverse_sharpe.sum()),
        "exact_sign_inverse_BE_cases": int(inverse_be.sum()),
        "first_tick_reverse_completed_logical": int(len(reverse)),
        "l1_bbo_maker_reverse_completed_logical": int(coverage.MAKER_REVERSE_COMPLETED.sum()),
        "l1_bbo_maker_reverse_completed_physical": int(
            coverage.loc[coverage.MAKER_REVERSE_COMPLETED, ["symbol", "physical_case_key"]]
            .drop_duplicates().shape[0]
        ),
        "l1_bbo_maker_data_complete_no_orderfilled_logical": int((~coverage.MAKER_REVERSE_COMPLETED).sum()),
        "l1_bbo_maker_data_complete_no_orderfilled_physical": int(
            coverage.loc[~coverage.MAKER_REVERSE_COMPLETED, ["symbol", "physical_case_key"]]
            .drop_duplicates().shape[0]
        ),
        "trade_only_maker_approximation": 0,
        "maker_data_unavailable": 0,
        "reason_counts_matched": "The previous 676 count represented data-complete simulations: every logical FIRST_TICK reverse row mapped to one of 408 semantic physical cases with complete March-2024 L1 bookTicker and raw-trade data. Results were computed physically once and expanded to equivalent source IDs. The strict OrderFilled completion audit corrects this to 638 logical / 389 physical completed cases because 38 logical / 19 physical cases generated no orders or fills.",
        "audit_note": "The prior temporal-validation label is invalid because the parent FIRST_TICK selection window includes the validation interval. Existing validation results remain audit evidence only and are not clean holdout evidence.",
    }
    atomic_json(result, delivery / "final_reverse_audit_summary.json")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
