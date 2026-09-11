#!/usr/bin/env python3
"""Compute discovery-only NORMAL metrics for the full eligible universe."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.internal.run_execution_review_reverse_validation import (  # noqa: E402
    INDEX_ROOT,
    MARKET_ROOT,
    atomic_json,
    build_strategy_clock,
    config_for,
    execute,
    load_symbol,
    metrics,
    run_decision_lifecycle,
)


SYMBOLS = (
    "XRPUSDT", "DOGEUSDT", "SUIUSDT", "BNBUSDT", "ETHUSDT",
    "BTCUSDT", "1000PEPEUSDT", "SOLUSDT", "ADAUSDT",
)
DISCOVERY_START = "2024-07-01"
DISCOVERY_END = "2025-07-01"
DISCOVERY_END_INCLUSIVE = "2025-06-30"
UNIVERSE = Path("outputs/deliverables/tick_review_stageA_9symbols/all_1m10m15m_results.csv")
OUTPUT = Path("outputs/baseline_evaluation/reverse_clean_validation/discovery/shards")


def atomic_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    frame.to_csv(temp, index=False)
    os.replace(temp, path)


def clean_number(value: object) -> object:
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def selected(timeframe: str, values: dict[str, float]) -> bool:
    if timeframe == "1m":
        return values["Return"] < 0 and values["Sharpe"] < -1.5
    return (
        values["Return"] < 0
        and values["Sharpe"] < -1.0
        and values["Signed_BE_bps"] < -10.0
    )


def main() -> None:  # noqa: C901
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=ROOT)
    parser.add_argument("--symbol", choices=SYMBOLS, required=True)
    parser.add_argument("--start-index", type=int, default=0)
    parser.add_argument("--stop-index", type=int)
    args = parser.parse_args()
    repo = args.repo.resolve()
    output = repo / OUTPUT / args.symbol
    output.mkdir(parents=True, exist_ok=True)

    universe = pd.read_csv(repo / UNIVERSE)
    logical = universe[universe.symbol.eq(args.symbol)].copy()
    if len(logical) != 331 * 3:
        raise ValueError(f"unexpected logical universe for {args.symbol}: {len(logical)}")
    if logical[["strategy_id", "symbol", "timeframe"]].duplicated().any():
        raise ValueError("duplicate logical universe identities")
    physical = (
        logical.sort_values("strategy_id")
        .drop_duplicates(["semantic_group_id", "timeframe"])
        [["semantic_group_id", "timeframe", "representative_strategy_id"]]
    )
    physical_work = physical.iloc[args.start_index:args.stop_index]

    bars, funding, execution_events, tick_prices, _ = load_symbol(
        repo / MARKET_ROOT,
        repo / INDEX_ROOT,
        args.symbol,
        DISCOVERY_START,
        DISCOVERY_END_INCLUSIVE,
    )
    event_time = np.fromiter((bar.event_time_ns for bar in bars), dtype=np.int64)
    close = np.fromiter((bar.close for bar in bars), dtype=float)
    rows: list[dict] = []
    failures: list[dict] = []

    for item in physical_work.itertuples(index=False):
        key = "case_" + hashlib.sha256(
            f"{item.semantic_group_id}|{item.timeframe}".encode()
        ).hexdigest()[:20]
        checkpoint = output / "physical_metrics" / f"{key}.json"
        try:
            if checkpoint.exists():
                record = json.loads(checkpoint.read_text(encoding="utf-8"))
            else:
                strategy_id = str(item.representative_strategy_id)
                direction, _, _ = run_decision_lifecycle(
                    strategy_name=strategy_id,
                    source_config=config_for(strategy_id, repo),
                    frequency=item.timeframe,
                    lag_minutes=0,
                    bars_1m=bars,
                    strategy_bars=build_strategy_clock(bars, item.timeframe),
                    execution_events=execution_events,
                    end_exclusive_ns=int(pd.Timestamp(DISCOVERY_END, tz="UTC").value),
                )
                result = execute(
                    np.asarray(direction, dtype=float), event_time, close, funding, tick_prices
                )
                values = metrics(result, np.ones(len(result), dtype=bool))
                record = {
                    "semantic_group_id": item.semantic_group_id,
                    "representative_strategy_id": strategy_id,
                    "symbol": args.symbol,
                    "timeframe": item.timeframe,
                    "discovery_start": DISCOVERY_START,
                    "discovery_end": DISCOVERY_END,
                    "NORMAL_discovery_Return": clean_number(values["Return"]),
                    "NORMAL_discovery_Sharpe": clean_number(values["Sharpe"]),
                    "NORMAL_discovery_BE": clean_number(values["Signed_BE_bps"]),
                    "NORMAL_discovery_MaxDD": clean_number(values["MaxDD"]),
                    "NORMAL_discovery_Turnover": clean_number(values["Turnover_raw"]),
                    "reverse_selected": bool(selected(item.timeframe, values)),
                }
                atomic_json(record, checkpoint)
            members = logical[
                logical.semantic_group_id.eq(item.semantic_group_id)
                & logical.timeframe.eq(item.timeframe)
            ]
            for member in members.itertuples(index=False):
                rows.append({
                    "strategy_id": member.strategy_id,
                    "semantic_group_id": member.semantic_group_id,
                    "source_origin": member.source_origin,
                    **record,
                })
        except Exception as exc:  # preserve a complete failure ledger
            failures.append({
                "semantic_group_id": item.semantic_group_id,
                "timeframe": item.timeframe,
                "representative_strategy_id": item.representative_strategy_id,
                "error": f"{type(exc).__name__}: {exc}",
            })

    frame = pd.DataFrame(rows).sort_values(["strategy_id", "timeframe"])
    atomic_csv(frame, output / "all_discovery_metrics.csv")
    atomic_csv(pd.DataFrame(failures), output / "failures.csv")
    summary = {
        "status": "PASSED" if not failures else "BLOCKED",
        "symbol": args.symbol,
        "logical_cases": len(frame),
        "physical_cases_total": len(physical),
        "physical_cases_this_worker": len(physical_work),
        "start_index": args.start_index,
        "stop_index": args.stop_index,
        "selected_logical_cases": int(frame.reverse_selected.sum()) if len(frame) else 0,
        "selected_physical_cases": int(
            frame.loc[frame.reverse_selected, ["semantic_group_id", "timeframe"]]
            .drop_duplicates().shape[0]
        ) if len(frame) else 0,
        "failures": len(failures),
        "holdout_metrics_read": 0,
        "reverse_runs": 0,
    }
    atomic_json(summary, output / "run_summary.json")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
