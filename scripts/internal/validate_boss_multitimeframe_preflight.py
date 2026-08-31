#!/usr/bin/env python3
"""Focused actual-data gate before the full boss tick screen."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.internal.run_all_strategy_timeframe_lag import build_strategy_clock
from scripts.internal.run_all_strategy_timeframe_lag import run_decision_lifecycle
from scripts.internal.run_boss_multitimeframe_tick_screen import load_symbol
from scripts.internal.run_boss_multitimeframe_tick_screen import semantic_groups
from scripts.internal.run_boss_multitimeframe_tick_screen import semantic_identity
from scripts.internal.run_boss_multitimeframe_tick_screen import strategy_scope


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root", type=Path,
        default=ROOT / "outputs/baseline_evaluation/boss_multitimeframe_tick_screen",
    )
    parser.add_argument("--market-root", type=Path, default=ROOT / "historical_data/market_data")
    args = parser.parse_args()
    details: dict[str, object] = {"status": "PASSED", "symbols": {}, "equivalence": []}
    strategies = strategy_scope(args.root / "boss_multitimeframe_strategy_scope.csv")
    groups = [group for group in semantic_groups(strategies) if len(group[1]) > 1][:5]
    for symbol in ("BTCUSDT", "ETHUSDT"):
        bars, _, execution, _, waits = load_symbol(
            args.market_root, args.root / "tick_execution_index", symbol,
            "2024-07-01", "2024-07-09",
        )
        bar_checks = 0
        max_error = 0.0
        for timeframe in ("5m", "10m", "15m"):
            size = int(timeframe.removesuffix("m"))
            derived = build_strategy_clock(bars, timeframe)
            indexes = np.linspace(0, len(derived) - 1, num=min(100, len(derived)), dtype=int)
            for index in indexes:
                children = bars[index * size : (index + 1) * size]
                event = derived[index]
                expected = (
                    children[0].open, max(child.high for child in children),
                    min(child.low for child in children), children[-1].close,
                    sum(child.volume for child in children),
                    sum(float(child.quote_volume or 0.0) for child in children),
                )
                actual = (
                    event.open, event.high, event.low, event.close, event.volume,
                    float(event.quote_volume or 0.0),
                )
                error = max(abs(float(left) - float(right)) for left, right in zip(expected, actual, strict=True))
                max_error = max(max_error, error)
                if not all(np.isclose(left, right, rtol=1e-12, atol=1e-9) for left, right in zip(expected, actual, strict=True)):
                    raise AssertionError(f"{symbol} {timeframe} aggregation mismatch at {index}")
                bar_checks += 1
        event_times = np.fromiter((event.event_time_ns for event in execution), dtype=np.int64)
        boundary = np.fromiter((bar.event_time_ns for bar in bars), dtype=np.int64)
        if np.any(event_times < boundary):
            raise AssertionError(f"{symbol}: pre-decision tick")
        details["symbols"][symbol] = {
            "bar_aggregation_checks": bar_checks,
            "bar_aggregation_max_absolute_error": max_error,
            "first_tick_predecision_count": int(np.sum(event_times < boundary)),
            "wait_median_ms": float(np.median(waits)),
            "wait_p95_ms": float(np.quantile(waits, 0.95)),
        }
        if symbol == "BTCUSDT":
            end_ns = int(pd.Timestamp("2024-07-10", tz="UTC").value)
            for digest, members, source in groups:
                rep = members[0]
                clock = build_strategy_clock(bars, "10m")
                expected, _, _ = run_decision_lifecycle(
                    strategy_name=rep, source_config=source, frequency="10m", lag_minutes=0,
                    bars_1m=bars, strategy_bars=clock, execution_events=execution,
                    end_exclusive_ns=end_ns,
                )
                for member in members[1:]:
                    _, member_source = semantic_identity(member)
                    actual, _, _ = run_decision_lifecycle(
                        strategy_name=member, source_config=member_source, frequency="10m",
                        lag_minutes=0, bars_1m=bars, strategy_bars=clock,
                        execution_events=execution, end_exclusive_ns=end_ns,
                    )
                    residual = float(np.max(np.abs(expected - actual)))
                    if residual > 1e-12:
                        raise AssertionError(f"semantic reuse mismatch {rep} vs {member}")
                    details["equivalence"].append(
                        {"semantic_hash": digest, "representative": rep, "member": member, "max_residual": residual}
                    )
    details["bar_aggregation_mismatch_count"] = 0
    details["first_tick_lookup_mismatch_count"] = 0
    details["semantic_equivalence_mismatch_count"] = 0
    output = args.root / "smoke/preflight_validation_detail.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(details, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(details, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
