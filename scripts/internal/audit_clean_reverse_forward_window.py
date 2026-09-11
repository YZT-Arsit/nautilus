#!/usr/bin/env python3
"""Freeze a performance-blind availability decision for clean reverse holdout."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = Path("outputs/baseline_evaluation/reverse_clean_validation")
TICK_MANIFEST = Path(
    "outputs/baseline_evaluation/boss_multitimeframe_tick_screen/"
    "tick_execution_index_manifest.csv"
)
SYMBOLS = (
    "XRPUSDT", "DOGEUSDT", "SUIUSDT", "BNBUSDT", "ETHUSDT",
    "BTCUSDT", "1000PEPEUSDT", "SOLUSDT", "ADAUSDT",
)
CUTOFF = pd.Timestamp("2026-07-01", tz="UTC")
MINIMUM_COMPLETE_DAYS = 180


def atomic_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    frame.to_csv(temp, index=False)
    os.replace(temp, path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=ROOT)
    args = parser.parse_args()
    repo = args.repo.resolve()
    manifest_path = repo / TICK_MANIFEST
    manifest = pd.read_csv(manifest_path)
    manifest["date"] = pd.to_datetime(manifest.date, utc=True)
    rows = []
    for symbol in SYMBOLS:
        group = manifest[manifest.symbol.eq(symbol)]
        passed = group[group.validation_status.astype(str).str.upper().eq("PASSED")]
        first = passed.date.min() if not passed.empty else pd.NaT
        last = passed.date.max() if not passed.empty else pd.NaT
        forward = passed[passed.date.ge(CUTOFF)]
        dates = sorted(forward.date.dt.normalize().unique())
        contiguous_days = 0
        expected = CUTOFF
        for value in dates:
            current = pd.Timestamp(value)
            if current != expected:
                break
            contiguous_days += 1
            expected += pd.Timedelta(days=1)
        rows.append({
            "symbol": symbol,
            "last_reverse_research_timestamp_seen": "2026-06-30T23:59:59.999999999Z",
            "earliest_eligible_clean_forward": "2026-07-01T00:00:00Z",
            "first_tick_index_first_date": first.date().isoformat() if pd.notna(first) else "",
            "first_tick_index_last_date": last.date().isoformat() if pd.notna(last) else "",
            "post_cutoff_contiguous_complete_days": contiguous_days,
            "bars_required": True,
            "funding_required": True,
            "first_tick_required": True,
            "minimum_predeclared_complete_days": MINIMUM_COMPLETE_DAYS,
            "performance_inspected_for_window_choice": False,
            "status": "SUFFICIENT" if contiguous_days >= MINIMUM_COMPLETE_DAYS else "INSUFFICIENT",
            "reason": (
                "COMPLETE_POST_CUTOFF_FIRST_TICK_WINDOW"
                if contiguous_days >= MINIMUM_COMPLETE_DAYS
                else "CLEAN_FORWARD_HOLDOUT_INSUFFICIENT"
            ),
        })
    result = pd.DataFrame(rows)
    # Full-universe validation requires a common window across all frozen symbols.
    common_days = int(result.post_cutoff_contiguous_complete_days.min())
    overall = pd.DataFrame([{
        "symbol": "ALL_9_COMMON",
        "last_reverse_research_timestamp_seen": "2026-06-30T23:59:59.999999999Z",
        "earliest_eligible_clean_forward": "2026-07-01T00:00:00Z",
        "first_tick_index_first_date": "",
        "first_tick_index_last_date": "",
        "post_cutoff_contiguous_complete_days": common_days,
        "bars_required": True,
        "funding_required": True,
        "first_tick_required": True,
        "minimum_predeclared_complete_days": MINIMUM_COMPLETE_DAYS,
        "performance_inspected_for_window_choice": False,
        "status": "SUFFICIENT" if common_days >= MINIMUM_COMPLETE_DAYS else "INSUFFICIENT",
        "reason": (
            "COMPLETE_POST_CUTOFF_COMMON_WINDOW"
            if common_days >= MINIMUM_COMPLETE_DAYS
            else "CLEAN_FORWARD_HOLDOUT_INSUFFICIENT"
        ),
    }])
    output = repo / OUTPUT / "clean_holdout_definition.csv"
    atomic_csv(pd.concat([overall, result], ignore_index=True), output)
    print(json.dumps({
        "status": str(overall.status.iloc[0]),
        "common_complete_days": common_days,
        "minimum_complete_days": MINIMUM_COMPLETE_DAYS,
        "holdout_performance_read": 0,
    }, indent=2))


if __name__ == "__main__":
    main()
