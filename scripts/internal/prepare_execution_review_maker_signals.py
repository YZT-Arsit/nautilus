#!/usr/bin/env python3
"""Prepare March-2024 targets for the frozen FIRST_TICK selected cases."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from dataclasses import replace
from pathlib import Path

import pandas as pd
import yaml


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.internal.prepare_l1_maker_pilot_signals import (  # noqa: E402
    END,
    START,
    load_bars,
    load_funding,
    to_events,
)
from scripts.internal.run_all_strategy_timeframe_lag import (  # noqa: E402
    build_strategy_clock,
    run_decision_lifecycle,
)
from strategy_framework.registry import get_entry  # noqa: E402


SYMBOLS = (
    "XRPUSDT", "DOGEUSDT", "SUIUSDT", "BNBUSDT", "ETHUSDT",
    "BTCUSDT", "1000PEPEUSDT", "SOLUSDT", "ADAUSDT",
)
DEFAULT_SELECTION = Path(
    "outputs/baseline_evaluation/execution_method_and_reverse_review/selection/first_tick_selected_cases.csv"
)
DEFAULT_OUTPUT = Path(
    "outputs/baseline_evaluation/execution_method_and_reverse_review/maker_signals"
)


def atomic_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    frame.to_csv(temporary, index=False)
    os.replace(temporary, path)


def config_for(strategy_id: str, repo: Path) -> dict:
    entry = get_entry(strategy_id)
    path = Path(entry.default_config_path)
    if not path.is_absolute():
        path = repo / path
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=ROOT)
    parser.add_argument("--selection", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--temp", type=Path, default=Path(r"D:\nautilus\outputs\tmp_execution_review_signals"))
    parser.add_argument("--symbols", nargs="*", choices=SYMBOLS, default=list(SYMBOLS))
    args = parser.parse_args()
    repo = args.repo.resolve()
    selection_path = args.selection or repo / DEFAULT_SELECTION
    output = (args.output or repo / DEFAULT_OUTPUT).resolve()
    output.mkdir(parents=True, exist_ok=True)
    selected = pd.read_csv(selection_path)
    provenance: list[dict] = []
    mapping_rows: list[dict] = []

    for symbol in args.symbols:
        subset = selected[selected.symbol.eq(symbol)].copy()
        if subset.empty:
            continue
        physical = (
            subset.sort_values("strategy_id")
            .drop_duplicates(["semantic_group_id", "timeframe"])
            [["semantic_group_id", "timeframe", "strategy_id"]]
        )
        bars_frame, bar_provenance = load_bars(symbol, args.temp / symbol)
        funding, funding_provenance = load_funding(symbol, args.temp / symbol)
        provenance.extend(bar_provenance)
        provenance.append(funding_provenance)
        funding.to_parquet(output / f"funding_{symbol}.parquet", index=False, compression="zstd")
        all_events = to_events(bars_frame, symbol)
        live_mask = (
            bars_frame.open_time.ge(int(START.timestamp() * 1000))
            & bars_frame.open_time.lt(int(END.timestamp() * 1000))
        )
        warmup = [event for event, live in zip(all_events, live_mask, strict=True) if not live]
        live = [event for event, flag in zip(all_events, live_mask, strict=True) if flag]
        execution = [
            replace(
                event,
                event_time_ns=event.event_time_ns + 60_000_000_000,
                open=event.close,
                high=event.close,
                low=event.close,
            )
            for event in live
        ]
        target = pd.DataFrame(
            {
                "decision_time_ns": [event.event_time_ns for event in live],
                "mark_close": [event.close for event in live],
            }
        )
        for row in physical.itertuples(index=False):
            case_key = "case_" + hashlib.sha256(
                f"{row.semantic_group_id}|{row.timeframe}".encode()
            ).hexdigest()[:20]
            direction, _, metadata = run_decision_lifecycle(
                strategy_name=row.strategy_id,
                source_config=config_for(row.strategy_id, repo),
                frequency=row.timeframe,
                lag_minutes=0,
                bars_1m=live,
                strategy_bars=build_strategy_clock(live, row.timeframe),
                end_exclusive_ns=int(END.value),
                warmup_bars=warmup,
                execution_events=execution,
            )
            if len(direction) != len(target):
                raise ValueError(f"{symbol} {case_key}: target length mismatch")
            target[case_key] = direction
            mapping_rows.append(
                {
                    "symbol": symbol,
                    "case_key": case_key,
                    "semantic_group_id": row.semantic_group_id,
                    "timeframe": row.timeframe,
                    "representative_strategy_id": row.strategy_id,
                    "logical_strategy_ids": ";".join(
                        sorted(
                            subset.loc[
                                subset.semantic_group_id.eq(row.semantic_group_id)
                                & subset.timeframe.eq(row.timeframe),
                                "strategy_id",
                            ].unique()
                        )
                    ),
                    "direction_change_count": metadata.get("direction_change_count"),
                }
            )
        target.to_parquet(output / f"target_positions_{symbol}.parquet", index=False, compression="zstd")

    atomic_csv(pd.DataFrame(mapping_rows), output / "maker_case_mapping.csv")
    atomic_csv(pd.DataFrame(provenance), output / "signal_source_provenance.csv")
    summary = {
        "status": "PASSED",
        "selection_source": str(selection_path),
        "symbols": list(args.symbols),
        "physical_case_targets": len(mapping_rows),
        "signal_window": {"start": str(START), "end_exclusive": str(END)},
        "new_parameter_searches": 0,
        "maker_informed_selection": 0,
    }
    (output / "signal_preparation_summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
