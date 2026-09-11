#!/usr/bin/env python3
"""Reconstruct only the two headline reverse cases for denominator audits."""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.internal.run_execution_review_reverse_validation import (  # noqa: E402
    END_EXCLUSIVE,
    END_INCLUSIVE,
    INDEX_ROOT,
    MARKET_ROOT,
    SPLIT,
    build_strategy_clock,
    config_for,
    execute,
    load_symbol,
    metrics,
    run_decision_lifecycle,
)


TARGETS = (
    ("jailbreak_short", "DOGEUSDT", "1m", "LARGEST_VALIDATED_REVERSE_BE"),
    ("adx_ma_channel_long", "BTCUSDT", "1m", "BEST_VALIDATED_REVERSE_SHARPE"),
)


def episode_counts(position: np.ndarray) -> tuple[int, int]:
    signs = np.sign(np.asarray(position, dtype=float)).astype(np.int8)
    changes = np.flatnonzero(signs != np.r_[0, signs[:-1]])
    starts = np.flatnonzero(np.r_[True, signs[1:] != signs[:-1]])
    ends = np.r_[starts[1:], len(signs)]
    completed = int(sum(signs[start] != 0 and end < len(signs) for start, end in zip(starts, ends)))
    return completed, int(len(changes))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=ROOT)
    args = parser.parse_args()
    repo = args.repo.resolve()
    market_root, index_root = repo / MARKET_ROOT, repo / INDEX_ROOT
    output = repo / "outputs/deliverables/strategy_execution_reverse_review/top_reverse_case_audit.csv"
    rows = []
    for strategy, symbol, timeframe, label in TARGETS:
        bars, funding, execution_events, tick_prices, _ = load_symbol(
            market_root, index_root, symbol, "2024-07-01", END_INCLUSIVE
        )
        times = np.fromiter((bar.event_time_ns for bar in bars), dtype=np.int64)
        close = np.fromiter((bar.close for bar in bars), dtype=float)
        direction, _, _ = run_decision_lifecycle(
            strategy_name=strategy,
            source_config=config_for(strategy, repo),
            frequency=timeframe,
            lag_minutes=0,
            bars_1m=bars,
            strategy_bars=build_strategy_clock(bars, timeframe),
            execution_events=execution_events,
            end_exclusive_ns=int(pd.Timestamp(END_EXCLUSIVE, tz="UTC").value),
        )
        reverse_direction = -np.asarray(direction, dtype=float)
        result = execute(reverse_direction, times, close, funding, tick_prices)
        mask = times >= int(SPLIT.value)
        part = result.loc[mask].copy()
        computed = metrics(result, mask)
        cumulative = np.cumsum(part.total_return.to_numpy(float), dtype=float)
        series = pd.Series(cumulative, index=pd.to_datetime(part.event_time_ns, unit="ns", utc=True))
        daily = series.resample("1D").last().diff().dropna()
        completed, fills = episode_counts(part.direction.to_numpy(float))
        sharpe = float(daily.mean() / daily.std(ddof=1) * math.sqrt(365))
        if not math.isclose(sharpe, computed["Sharpe"], rel_tol=1e-12, abs_tol=1e-12):
            raise ValueError("daily Sharpe reconstruction mismatch")
        rows.append({
            "headline": label,
            "internal_strategy_id": strategy,
            "display_name": strategy.replace("_", " ").title(),
            "symbol": symbol,
            "timeframe": timeframe,
            "validation_start": "2025-07-01",
            "validation_end": "2026-06-30",
            "validation_Return": computed["Return"],
            "validation_Sharpe": computed["Sharpe"],
            "validation_Signed_BE_bps": computed["Signed_BE_bps"],
            "validation_Turnover_raw": computed["Turnover_raw"],
            "completed_episode_count": completed,
            "trade_fill_event_count": fills,
            "n_daily_observations": int(len(daily)),
            "daily_mean_Return": float(daily.mean()),
            "daily_sample_SD": float(daily.std(ddof=1)),
            "annualization": "sqrt(365)",
            "sample_label": "LOW_SAMPLE_HIGH_BE" if label == "LARGEST_VALIDATED_REVERSE_BE" and completed < 30 else "DESCRIPTIVE_VALIDATION_SAMPLE",
            "temporal_cleanliness": "CONTAMINATED_PARENT_FULL_PERIOD_SELECTION",
        })
    frame = pd.DataFrame(rows)
    output.parent.mkdir(parents=True, exist_ok=True)
    temp = output.with_suffix(".csv.tmp")
    frame.to_csv(temp, index=False)
    temp.replace(output)
    print(json.dumps(frame.to_dict(orient="records"), indent=2))


if __name__ == "__main__":
    main()
