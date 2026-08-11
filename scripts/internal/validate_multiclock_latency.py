#!/usr/bin/env python3
"""Validate sparse strategy decisions against a finer execution clock."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib as mpl


mpl.use("Agg")

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeseries", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--strategy-frequency-minutes", type=int, default=10)
    parser.add_argument("--execution-frequency-minutes", type=int, default=1)
    parser.add_argument("--lag-minutes", type=int, default=1)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if min(
        args.strategy_frequency_minutes,
        args.execution_frequency_minutes,
        args.lag_minutes,
    ) <= 0:
        raise ValueError("frequencies and lag must be positive")
    if args.execution_frequency_minutes >= args.strategy_frequency_minutes:
        raise ValueError("execution frequency must be finer than strategy frequency")

    frame = pd.read_parquet(
        args.timeseries,
        columns=["event_time_ns", "direction"],
    ).sort_values("event_time_ns")
    clock = pd.to_datetime(frame["event_time_ns"], unit="ns", utc=True)
    clock_ns = frame["event_time_ns"].to_numpy(dtype=np.int64, copy=False)
    expected_step_ns = args.execution_frequency_minutes * 60 * 1_000_000_000
    if not np.all(np.diff(clock_ns) == expected_step_ns):
        raise ValueError("input is not a complete regular execution clock")

    minute_of_day = clock.dt.hour * 60 + clock.dt.minute
    decision_mask = (
        minute_of_day % args.strategy_frequency_minutes
    ).to_numpy() == 0
    decision_index = np.flatnonzero(decision_mask)
    decision_time_ns = clock_ns[decision_index]
    requested_execution_ns = (
        decision_time_ns + args.lag_minutes * 60 * 1_000_000_000
    )
    execution_index = np.searchsorted(clock_ns, requested_execution_ns, side="left")
    valid = execution_index < len(clock_ns)
    decision_index = decision_index[valid]
    execution_index = execution_index[valid]
    decision_time_ns = decision_time_ns[valid]
    actual_execution_ns = clock_ns[execution_index]
    lag_seconds = (actual_execution_ns - decision_time_ns) / 1_000_000_000

    audit = pd.DataFrame(
        {
            "decision_time_utc": pd.to_datetime(
                decision_time_ns, unit="ns", utc=True
            ),
            "strategy_clock_minutes": args.strategy_frequency_minutes,
            "target_direction": frame["direction"].to_numpy()[decision_index],
            "requested_execution_time_utc": pd.to_datetime(
                requested_execution_ns[valid], unit="ns", utc=True
            ),
            "actual_execution_time_utc": pd.to_datetime(
                actual_execution_ns, unit="ns", utc=True
            ),
            "execution_clock_minutes": args.execution_frequency_minutes,
            "actual_lag_seconds": lag_seconds,
            "executed_after_decision": actual_execution_ns > decision_time_ns,
            "exact_requested_lag": actual_execution_ns == requested_execution_ns[valid],
        }
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    audit.to_csv(args.output_dir / "multiclock_latency_audit.csv", index=False)

    summary = {
        "source_timeseries": str(args.timeseries),
        "strategy_frequency_minutes": args.strategy_frequency_minutes,
        "execution_frequency_minutes": args.execution_frequency_minutes,
        "requested_lag_minutes": args.lag_minutes,
        "decision_count": len(audit),
        "all_executed_after_decision": bool(audit["executed_after_decision"].all()),
        "all_exact_requested_lag": bool(audit["exact_requested_lag"].all()),
        "minimum_actual_lag_seconds": float(audit["actual_lag_seconds"].min()),
        "maximum_actual_lag_seconds": float(audit["actual_lag_seconds"].max()),
        "scope": (
            "clock-contract prototype using sampled validated directions; "
            "not a 10m strategy performance backtest"
        ),
    }
    (args.output_dir / "multiclock_latency_summary.json").write_text(
        json.dumps(summary, indent=2) + "\n",
        encoding="utf-8",
    )

    active = np.flatnonzero(audit["target_direction"].to_numpy() != 0)
    center = int(active[0]) if len(active) else 0
    sample_start = max(0, center - 72)
    sample = audit.iloc[sample_start : sample_start + 24 * 6].copy()
    figure, axis = plt.subplots(figsize=(13, 6))
    axis.step(
        sample["decision_time_utc"],
        sample["target_direction"],
        where="post",
        label="10m decision target",
    )
    axis.scatter(
        sample["actual_execution_time_utc"],
        sample["target_direction"],
        s=12,
        color="#d95f02",
        label="1m execution at decision + 1m",
    )
    axis.set_title("Multi-clock latency contract prototype")
    axis.set_xlabel("Time (UTC)")
    axis.set_ylabel("Target direction")
    axis.set_yticks([-1, 0, 1])
    axis.grid(alpha=0.25)
    axis.legend(loc="best")
    axis.xaxis.set_major_formatter(
        mdates.ConciseDateFormatter(axis.xaxis.get_major_locator())
    )
    figure.tight_layout()
    figure.savefig(args.output_dir / "multiclock_latency_timeline.png", dpi=160)
    plt.close(figure)
    print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
