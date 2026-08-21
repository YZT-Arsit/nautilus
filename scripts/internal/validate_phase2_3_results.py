#!/usr/bin/env python3
"""Validate Phase 2.3 result completeness and UTC-session execution boundaries."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import numpy as np
import pandas as pd


DAY_NS = 86_400_000_000_000
MINUTE_NS = 60_000_000_000


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--backtest-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    strategies = sorted(json.loads(args.plan.read_text(encoding="utf-8")))
    cases = (("1m_lag0", 0), ("1m_lag1", 1))
    rows: list[dict[str, object]] = []
    failures: list[str] = []

    for strategy in strategies:
        for case, lag_minutes in cases:
            root = args.backtest_root / strategy / case
            required = [root / "summary.json", root / "timeseries.parquet", root / "execution_events.csv"]
            missing = [str(path) for path in required if not path.is_file()]
            if missing:
                failures.append(f"{strategy}/{case}: missing {missing}")
                continue
            summary = json.loads(required[0].read_text(encoding="utf-8"))
            frame = pd.read_parquet(
                required[1],
                columns=[
                    "event_time_ns", "normal_direction", "long_only_direction",
                    "short_only_direction", "strict_reverse_direction",
                ],
            )
            source = frame["normal_direction"].to_numpy(dtype=np.float64)
            direction_residual = max(
                float(np.max(np.abs(frame["long_only_direction"] - np.maximum(source, 0.0)))),
                float(np.max(np.abs(frame["short_only_direction"] - np.minimum(source, 0.0)))),
                float(np.max(np.abs(frame["strict_reverse_direction"] + source))),
            )
            boundary = frame.loc[frame["event_time_ns"] % DAY_NS == 0, "normal_direction"]
            nonflat_boundaries = int(np.count_nonzero(np.abs(boundary.to_numpy()) > 1e-12))

            execution = pd.read_csv(required[2])
            filled = execution.loc[execution["fill_count"].fillna(0).astype(int) > 0].copy()
            observed = filled["observed_lag_ns"].dropna().to_numpy(dtype=np.int64)
            expected_lag = lag_minutes * MINUTE_NS
            lag_residual = int(np.max(np.abs(observed - expected_lag))) if len(observed) else 0
            signal_times = filled["signal_time_ns"].to_numpy(dtype=np.int64)
            fill_times = filled["fill_time_ns"].to_numpy(dtype=np.int64)
            cross_session_fills = int(np.count_nonzero(signal_times // DAY_NS != fill_times // DAY_NS))
            entry = filled.loc[
                (filled["exposure_before"].abs() <= 1e-12)
                & (filled["exposure_after"].abs() > 1e-12)
            ]
            cutoff = DAY_NS - expected_lag - MINUTE_NS
            late_entries = int(np.count_nonzero(entry["signal_time_ns"].to_numpy(dtype=np.int64) % DAY_NS >= cutoff))
            variants_present = sorted(summary) == ["long_only", "normal", "short_only", "strict_reverse"]
            passed = (
                variants_present and direction_residual <= 1e-12 and nonflat_boundaries == 0
                and lag_residual == 0 and cross_session_fills == 0 and late_entries == 0
            )
            row = {
                "strategy": strategy, "case": case, "passed": passed,
                "variants_present": variants_present,
                "direction_residual": direction_residual,
                "utc_boundary_count": int(len(boundary)),
                "nonflat_utc_boundary_count": nonflat_boundaries,
                "filled_event_count": int(len(filled)),
                "lag_residual_ns": lag_residual,
                "cross_session_fill_count": cross_session_fills,
                "late_entry_count": late_entries,
            }
            rows.append(row)
            if not passed:
                failures.append(f"{strategy}/{case}: {row}")

    expected_cases = len(strategies) * len(cases)
    result = {
        "status": "passed" if not failures and len(rows) == expected_cases else "failed",
        "strategy_count": len(strategies), "expected_case_count": expected_cases,
        "validated_case_count": len(rows), "failures": failures, "cases": rows,
    }
    atomic_json(args.output, result)
    print(json.dumps({key: value for key, value in result.items() if key != "cases"}, ensure_ascii=False))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
