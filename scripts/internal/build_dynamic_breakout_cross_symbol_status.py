#!/usr/bin/env python3
"""Build the low-cost P1 cross-symbol status package from existing results.

This script is deliberately a post-processor.  It does not load market data,
run a strategy, connect to an exchange, or synthesize missing STRICT_REVERSE
metrics.  A reverse case is reusable only when both existing retrospective
segments from the actual rerun are present.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
STRATEGY = "dynamic_breakout_short"
TIMEFRAME = "1m"
SYMBOLS = (
    "ETHUSDT",
    "SOLUSDT",
    "BNBUSDT",
    "XRPUSDT",
    "DOGEUSDT",
    "ADAUSDT",
    "SUIUSDT",
    "1000PEPEUSDT",
)
EXPECTED_SEGMENTS = {"DISCOVERY", "SPENT_VALIDATION"}


def atomic_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    frame.to_csv(temporary, index=False)
    os.replace(temporary, path)


def atomic_json(value: dict[str, object], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def load_sources(repo: Path) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    normal_path = (
        repo / "outputs/deliverables/multi_symbol_classification/"
        "multi_symbol_classification_master.csv"
    )
    reverse_path = (
        repo / "outputs/baseline_evaluation/reverse_clean_validation/"
        "retrospective_reverse_results.csv"
    )
    availability_path = (
        repo / "outputs/baseline_evaluation/long_horizon_execution_reverse/"
        "long_horizon_project_data_availability.csv"
    )
    normal = pd.read_csv(normal_path)
    reverse = pd.read_csv(reverse_path)
    availability = pd.read_csv(availability_path)
    normal.attrs["source_path"] = str(normal_path)
    reverse.attrs["source_path"] = str(reverse_path)
    availability.attrs["source_path"] = str(availability_path)
    return normal, reverse, availability


def validate_normal(normal: pd.DataFrame) -> pd.DataFrame:
    selected = normal[
        normal.strategy_id.astype(str).eq(STRATEGY)
        & normal.timeframe.astype(str).eq(TIMEFRAME)
        & normal.symbol.astype(str).isin(SYMBOLS)
    ].copy()
    if len(selected) != len(SYMBOLS) or selected.symbol.nunique() != len(SYMBOLS):
        raise ValueError(
            f"expected {len(SYMBOLS)} unique NORMAL cases, found rows={len(selected)} "
            f"symbols={selected.symbol.nunique()}"
        )
    if set(selected.symbol) != set(SYMBOLS):
        raise ValueError("NORMAL symbol universe changed")
    if not selected.daily_turnover_reconciliation_mismatch.abs().le(1e-6).all():
        raise ValueError("NORMAL daily turnover reconciliation failed")
    return selected.sort_values("symbol").reset_index(drop=True)


def validate_reverse(reverse: pd.DataFrame) -> tuple[pd.DataFrame, set[str]]:
    selected = reverse[
        reverse.strategy_id.astype(str).eq(STRATEGY)
        & reverse.timeframe.astype(str).eq(TIMEFRAME)
        & reverse.symbol.astype(str).isin(SYMBOLS)
    ].copy()
    duplicates = selected.duplicated(["symbol", "evaluation_segment"])
    if duplicates.any():
        raise ValueError("duplicate reverse evidence segments")
    complete: set[str] = set()
    for symbol, group in selected.groupby("symbol"):
        if set(group.evaluation_segment.astype(str)) == EXPECTED_SEGMENTS:
            complete.add(str(symbol))
    # These are the only actual rerun segments mirrored into this evidence set.
    # A partial symbol is not silently promoted to reusable.
    partial = set(selected.symbol.astype(str)) - complete
    if partial:
        raise ValueError(f"partial reverse segment evidence: {sorted(partial)}")
    return selected.sort_values(["symbol", "evaluation_segment"]).reset_index(drop=True), complete


def validate_availability(availability: pd.DataFrame) -> pd.DataFrame:
    selected = availability[
        availability.symbol.astype(str).isin(SYMBOLS)
        & availability.data_type.astype(str).isin(["bar", "funding_rate"])
    ].copy()
    if selected.empty:
        raise ValueError("project data-availability audit has no P1 rows")
    return selected.sort_values(["symbol", "data_type"]).reset_index(drop=True)


def cross_symbol_rows(normal: pd.DataFrame, reverse: pd.DataFrame, complete: set[str]) -> pd.DataFrame:
    reverse_by_symbol = {symbol: group for symbol, group in reverse.groupby("symbol")}
    rows: list[dict[str, object]] = []
    for item in normal.itertuples(index=False):
        common = {
            "strategy_id": STRATEGY,
            "symbol": item.symbol,
            "timeframe": TIMEFRAME,
            "source_origin": item.source_origin,
            "semantic_group_id": item.semantic_group_id,
            "effective_start": item.effective_start,
            "end": item.end,
            "n_daily_observations": int(item.daily_observations),
            "effective_years": float(item.effective_years),
        }
        rows.append(
            {
                **common,
                "direction_variant": "NORMAL",
                "case_status": "REUSABLE_AUTHORITATIVE_RESULT",
                "evidence_class": "EXISTING_FIRST_TICK_HISTORICAL",
                "Return": item.Return,
                "Sharpe": item.Sharpe,
                "Signed_BE": item.Signed_BE,
                "MaxDD": item.MaxDD,
                "Avg_Daily_Turnover_pct": item.Avg_Daily_Turnover_pct,
                "Total_Turnover_raw": item.Total_Turnover_raw,
                "Funding_contribution": pd.NA,
                "Funding_contribution_status": "NOT_EXTRACTABLE_FROM_MIRRORED_MASTER",
                "completed_episodes": pd.NA,
                "existing_segment_count": 0,
                "missing_reason": "",
            }
        )
        if item.symbol in complete:
            segments = reverse_by_symbol[item.symbol]
            rows.append(
                {
                    **common,
                    "direction_variant": "STRICT_REVERSE",
                    "case_status": "REUSABLE_ACTUAL_RERUN_SEGMENTS",
                    "evidence_class": "EXPLORATORY_RETROSPECTIVE_ACTUAL_RERUN",
                    # Full-window Sharpe and MaxDD cannot be reconstructed from
                    # aggregate segment metrics.  Leave all headline metrics
                    # blank rather than manufacture a combined result.
                    "Return": pd.NA,
                    "Sharpe": pd.NA,
                    "Signed_BE": pd.NA,
                    "MaxDD": pd.NA,
                    "Avg_Daily_Turnover_pct": pd.NA,
                    "Total_Turnover_raw": pd.NA,
                    "Funding_contribution": pd.NA,
                    "Funding_contribution_status": "NOT_AVAILABLE_IN_RETROSPECTIVE_SUMMARY",
                    "completed_episodes": pd.NA,
                    "existing_segment_count": len(segments),
                    "missing_reason": "FULL_WINDOW_AGGREGATE_REQUIRES_EXISTING_PATH_TIMESERIES",
                }
            )
        else:
            rows.append(
                {
                    **common,
                    "direction_variant": "STRICT_REVERSE",
                    "case_status": "MISSING_RERUN",
                    "evidence_class": "NO_ACTUAL_RERUN_RESULT",
                    "Return": pd.NA,
                    "Sharpe": pd.NA,
                    "Signed_BE": pd.NA,
                    "MaxDD": pd.NA,
                    "Avg_Daily_Turnover_pct": pd.NA,
                    "Total_Turnover_raw": pd.NA,
                    "Funding_contribution": pd.NA,
                    "Funding_contribution_status": "MISSING_RERUN",
                    "completed_episodes": pd.NA,
                    "existing_segment_count": 0,
                    "missing_reason": "STRICT_REVERSE_WAS_NOT_PREVIOUSLY_RUN_FOR_THIS_CASE",
                }
            )
    return pd.DataFrame(rows).sort_values(["symbol", "direction_variant"]).reset_index(drop=True)


def forward_candidates(normal: pd.DataFrame, reverse: pd.DataFrame, complete: set[str]) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    normal_by_symbol = normal.set_index("symbol")
    for symbol in sorted(complete):
        base = normal_by_symbol.loc[symbol]
        segments = reverse[reverse.symbol.eq(symbol)].set_index("evaluation_segment")
        discovery = segments.loc["DISCOVERY"]
        validation = segments.loc["SPENT_VALIDATION"]
        reverse_better_both = bool(
            discovery.REVERSE_Return > discovery.NORMAL_Return
            and validation.REVERSE_Return > validation.NORMAL_Return
        )
        rows.append(
            {
                "symbol": symbol,
                "strategy_id": STRATEGY,
                "timeframe": TIMEFRAME,
                "effective_start": base.effective_start,
                "end": base.end,
                "n_daily_observations": int(base.daily_observations),
                "NORMAL_Return": base.Return,
                "NORMAL_Sharpe": base.Sharpe,
                "NORMAL_Signed_BE": base.Signed_BE,
                "NORMAL_MaxDD": base.MaxDD,
                "NORMAL_Avg_Daily_Turnover_pct": base.Avg_Daily_Turnover_pct,
                "REVERSE_discovery_Return": discovery.REVERSE_Return,
                "REVERSE_discovery_Sharpe": discovery.REVERSE_Sharpe,
                "REVERSE_spent_validation_Return": validation.REVERSE_Return,
                "REVERSE_spent_validation_Sharpe": validation.REVERSE_Sharpe,
                "preferred_historical_direction": "STRICT_REVERSE" if reverse_better_both else "UNRESOLVED",
                "forward_candidate_reason": (
                    "ACTUAL_NORMAL_AND_REVERSE_PATHS_EXIST; REVERSE_BEAT_NORMAL_IN_BOTH_"
                    "RETROSPECTIVE_SEGMENTS"
                    if reverse_better_both
                    else "ACTUAL_NORMAL_AND_REVERSE_PATHS_EXIST; DIRECTION_NOT_CONSISTENT"
                ),
                "evidence_class": "EXPLORATORY_RETROSPECTIVE_NOT_FORWARD_VALIDATED",
                "forward_launch_authorized": False,
            }
        )
    return pd.DataFrame(rows)


def build(repo: Path, research: Path, delivery: Path) -> dict[str, object]:
    raw_normal, raw_reverse, raw_availability = load_sources(repo)
    source_paths = {
        "normal": raw_normal.attrs["source_path"],
        "reverse": raw_reverse.attrs["source_path"],
        "availability": raw_availability.attrs["source_path"],
    }
    normal = validate_normal(raw_normal)
    reverse, complete = validate_reverse(raw_reverse)
    availability = validate_availability(raw_availability)
    cross = cross_symbol_rows(normal, reverse, complete)
    missing_symbols = sorted(set(SYMBOLS) - complete)
    missing = cross[
        cross.direction_variant.eq("STRICT_REVERSE") & cross.case_status.eq("MISSING_RERUN")
    ].copy()
    candidates = forward_candidates(normal, reverse, complete)

    summary: dict[str, object] = {
        "status": "PARTIAL",
        "strategy": STRATEGY,
        "timeframe": TIMEFRAME,
        "other_symbols": len(SYMBOLS),
        "normal_cases_reusable": len(normal),
        "strict_reverse_cases_reusable": len(complete),
        "total_cases_reusable": len(normal) + len(complete),
        "total_cases_requested": len(SYMBOLS) * 2,
        "complete_symbol_pairs": len(complete),
        "missing_reverse_reruns": len(missing_symbols),
        "missing_reverse_symbols": missing_symbols,
        "effective_window": "2024-07-01/2026-06-30",
        "daily_observations": sorted(normal.daily_observations.astype(int).unique().tolist()),
        "five_year_non_btc_data_complete": False,
        "performance_figure_generated": False,
        "performance_figure_reason": "MISSING_ACTUAL_REVERSE_RERUNS_AND_FULL_WINDOW_REVERSE_AGGREGATES",
        "backtests_rerun": 0,
        "market_data_downloads": 0,
        "forward_paper_started": False,
        "source_paths": source_paths,
    }
    if len(cross) != 16 or len(missing) != 4 or len(candidates) != 4:
        raise ValueError("P1 status cardinality changed")
    if cross.loc[cross.case_status.eq("MISSING_RERUN"), ["Return", "Sharpe", "Signed_BE", "MaxDD"]].notna().any().any():
        raise ValueError("missing rerun rows contain fabricated metrics")

    for root in (research, delivery):
        root.mkdir(parents=True, exist_ok=True)
        atomic_csv(cross, root / "dynamic_breakout_short_cross_symbol.csv")
        atomic_csv(reverse, root / "dynamic_breakout_short_reverse_existing_segments.csv")
        atomic_csv(missing, root / "missing_reverse_reruns.csv")
        atomic_csv(candidates, root / "next_forward_symbol_candidates.csv")
        atomic_csv(availability, root / "historical_data_availability.csv")
        atomic_json(summary, root / "validation_summary.json")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=ROOT)
    parser.add_argument("--research-output", type=Path)
    parser.add_argument("--delivery-output", type=Path)
    args = parser.parse_args()
    repo = args.repo.resolve()
    research = (
        args.research_output
        or repo / "outputs/baseline_evaluation/dynamic_breakout_short_cross_symbol"
    ).resolve()
    delivery = (
        args.delivery_output
        or repo / "outputs/deliverables/dynamic_breakout_short_cross_symbol"
    ).resolve()
    summary = build(repo, research, delivery)
    print(json.dumps(summary, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
