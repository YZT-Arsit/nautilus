#!/usr/bin/env python3
"""Measure position occupancy/state durations in the boss-provided reference export."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import numpy as np
import pandas as pd


def atomic_csv(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    frame.to_csv(temporary, index=False, encoding="utf-8-sig")
    os.replace(temporary, path)


def durations(values: np.ndarray, step_seconds: float) -> tuple[np.ndarray, np.ndarray]:
    starts = np.flatnonzero(np.r_[True, values[1:] != values[:-1]])
    ends = np.r_[starts[1:], len(values)]
    return values[starts], (ends - starts) * step_seconds


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reference-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    rows: list[dict] = []
    folders = {"策略_保守": "conservative", "策略_激进": "aggressive"}
    for folder, strategy in folders.items():
        path = args.reference_root / folder / "backtest_position_df.csv"
        frame = pd.read_csv(path)
        timestamps = pd.to_datetime(frame.pop("timestamp"), unit="s", utc=True)
        step_seconds = float(timestamps.diff().dt.total_seconds().dropna().median())
        for symbol in frame:
            values = frame[symbol].to_numpy(float)
            state, seconds = durations(values, step_seconds)
            changes = values[1:] != values[:-1]
            direct = values[1:] * values[:-1] < 0
            rows.append({
                "reference_strategy": strategy,
                "symbol": symbol,
                "start_timestamp": timestamps.iloc[0].isoformat(),
                "end_timestamp": timestamps.iloc[-1].isoformat(),
                "decision_interval_seconds": step_seconds,
                "observation_count": len(values),
                "long_fraction": float(np.mean(values > 0)),
                "short_fraction": float(np.mean(values < 0)),
                "flat_fraction": float(np.mean(values == 0)),
                "nonflat_fraction": float(np.mean(values != 0)),
                "position_change_count": int(changes.sum()),
                "sign_change_count": int(direct.sum()),
                "direct_reversal_count": int(direct.sum()),
                "mean_state_duration_minutes": float(seconds.mean() / 60),
                "median_state_duration_minutes": float(np.median(seconds) / 60),
                "longest_long_duration_minutes": float(seconds[state > 0].max() / 60) if np.any(state > 0) else 0.0,
                "longest_short_duration_minutes": float(seconds[state < 0].max() / 60) if np.any(state < 0) else 0.0,
                "longest_flat_duration_minutes": float(seconds[state == 0].max() / 60) if np.any(state == 0) else 0.0,
                "near_always_in_market": bool(np.mean(values != 0) >= 0.90),
            })
    result = pd.DataFrame(rows)
    if len(result) != 18 or not np.allclose(
        result.long_fraction + result.short_fraction + result.flat_fraction, 1.0, atol=1e-12
    ):
        raise AssertionError("reference position audit did not reconcile")
    atomic_csv(args.output, result)
    print(result[["reference_strategy", "symbol", "nonflat_fraction", "median_state_duration_minutes"]].to_string(index=False))


if __name__ == "__main__":
    main()
