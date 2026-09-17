#!/usr/bin/env python3
"""Run STRICT_REVERSE for every frozen NORMAL-selected logical case."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.internal.run_boss_multitimeframe_tick_screen import atomic_json, load_symbol  # noqa: E402
from scripts.internal.run_long_horizon_strict_reverse import run_reverse_from_frozen_normal  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--market-root", type=Path, required=True)
    parser.add_argument("--shard-count", type=int, default=1)
    parser.add_argument("--shard-index", type=int, default=0)
    args = parser.parse_args()
    manifest = pd.read_csv(args.root / "selection/long_horizon_first_tick_selected_cases.csv")
    physical = manifest.sort_values(
        ["semantic_group_id", "timeframe", "symbol", "strategy_id"]
    ).drop_duplicates(["semantic_group_id", "timeframe", "symbol"]).reset_index(drop=True)
    physical = physical.iloc[args.shard_index :: args.shard_count]
    start, end_exclusive = "2021-07-01", "2026-07-01"
    end_inclusive = (date.fromisoformat(end_exclusive) - timedelta(days=1)).isoformat()
    bars, funding, _, tick_prices, waits = load_symbol(
        args.market_root, args.root / "tick_execution_index", "BTCUSDT", start, end_inclusive
    )
    complete = reused = failures = 0
    progress = args.root / f"selected_reverse_progress_shard_{args.shard_index}_of_{args.shard_count}.json"
    for row in physical.itertuples(index=False):
        safe_group = str(row.semantic_group_id).replace(":", "__")
        target_root = args.root / "selected_reverse_cases" / f"symbol={row.symbol}" / f"timeframe={row.timeframe}" / f"semantic={safe_group}"
        target_summary = target_root / "summary.json"
        target_review = target_root / "review_timeseries.parquet"
        existing_root = args.root / "reverse_cases" / f"symbol={row.symbol}" / f"timeframe={row.timeframe}" / f"semantic={safe_group}"
        existing_summary = existing_root / "summary.json"
        existing_review = existing_root / "review_timeseries.parquet"
        if existing_summary.is_file() and existing_review.is_file():
            saved = json.loads(existing_summary.read_text(encoding="utf-8-sig"))
            if saved.get("status") == "COMPLETED" and saved.get("strict_reverse_target_mismatch_count") == 0:
                target_root.mkdir(parents=True, exist_ok=True)
                if not target_summary.exists():
                    target_summary.write_bytes(existing_summary.read_bytes())
                if not target_review.exists():
                    try:
                        os.link(existing_review, target_review)
                    except OSError:
                        target_review.write_bytes(existing_review.read_bytes())
                complete += 1
                reused += 1
                continue
        try:
            members = sorted(manifest.loc[
                manifest.semantic_group_id.eq(row.semantic_group_id)
                & manifest.timeframe.eq(row.timeframe)
                & manifest.symbol.eq(row.symbol), "strategy_id"
            ].tolist())
            normal_summary_path = Path(str(row.normal_summary_path))
            normal_summary = json.loads(normal_summary_path.read_text(encoding="utf-8-sig"))
            summary, review = run_reverse_from_frozen_normal(
                row=row, members=members, normal_summary=normal_summary,
                normal_review_path=normal_summary_path.parent / "review_timeseries.parquet",
                bars=bars, funding=funding, tick_prices=tick_prices, waits=waits,
            )
            target_root.mkdir(parents=True, exist_ok=True)
            atomic_json(target_summary, summary)
            temporary = target_review.with_suffix(target_review.suffix + ".tmp")
            review.to_parquet(temporary, index=False, compression="zstd")
            os.replace(temporary, target_review)
            complete += 1
        except Exception as exc:  # preserve per-case evidence and finish the shard
            failures += 1
            atomic_json(target_summary, {
                "status": "FAILED", "strategy_id": row.representative_strategy_id,
                "semantic_group_id": row.semantic_group_id, "symbol": row.symbol,
                "timeframe": row.timeframe, "error": f"{type(exc).__name__}: {exc}",
            })
        atomic_json(progress, {
            "status": "RUNNING", "physical_planned": len(physical),
            "physical_completed": complete, "physical_reused": reused,
            "physical_failures": failures,
        })
    atomic_json(progress, {
        "status": "PASSED" if failures == 0 else "COMPLETED_WITH_FAILURES",
        "physical_planned": len(physical), "physical_completed": complete,
        "physical_reused": reused, "physical_failures": failures,
    })
    return 0 if failures == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
