#!/usr/bin/env python3
"""Hash frozen target paths to expose exact duplicate maker computations."""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.internal.run_boss_multitimeframe_tick_screen import load_symbol  # noqa: E402
from scripts.internal.run_selected_partial_window_maker import (  # noqa: E402
    END, START, atomic_csv, case_key, reconstruct_interval_position,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--research-root", type=Path, required=True)
    parser.add_argument("--market-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    selected = pd.read_csv(args.research_root / "selection/long_horizon_first_tick_selected_cases.csv")
    physical = selected.sort_values(
        ["semantic_group_id", "timeframe", "symbol", "strategy_id"]
    ).drop_duplicates(["semantic_group_id", "timeframe", "symbol"]).reset_index(drop=True)
    bars, _, _, _, _ = load_symbol(
        args.market_root, args.research_root / "tick_execution_index", "BTCUSDT",
        START.date().isoformat(), (END - pd.Timedelta(days=1)).date().isoformat(),
    )
    target_times = pd.Series([bar.event_time_ns for bar in bars], dtype="int64").to_numpy()
    rows = []
    for row in physical.itertuples(index=False):
        target = reconstruct_interval_position(
            Path(str(row.normal_summary_path)).parent / "review_timeseries.parquet", target_times
        )
        digest = hashlib.sha256(target.astype("float32", copy=False).tobytes()).hexdigest()
        rows.append({
            "case_key": case_key(str(row.semantic_group_id), str(row.timeframe)),
            "semantic_group_id": row.semantic_group_id,
            "timeframe": row.timeframe,
            "target_path_sha256": digest,
        })
    frame = pd.DataFrame(rows)
    counts = frame.groupby("target_path_sha256").size().rename("execution_group_size")
    frame = frame.join(counts, on="target_path_sha256")
    atomic_csv(frame, args.output)
    print({
        "physical_semantic_cases": len(frame),
        "unique_target_paths": frame.target_path_sha256.nunique(),
        "duplicate_computations": len(frame) - frame.target_path_sha256.nunique(),
    })


if __name__ == "__main__":
    main()
