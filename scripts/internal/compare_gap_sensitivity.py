#!/usr/bin/env python3
"""Compare the frozen as-recorded run with a separate backfilled diagnostic replay."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd


CANDIDATE_ID = "pc_2fe14acb95eac19f88d4"
LABEL = "BACKFILLED_DIAGNOSTIC_REPLAY"


def _decisions(path: Path) -> pd.DataFrame:
    frame = pd.DataFrame([json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line])
    return frame[["event_time_ns", "target"]].sort_values("event_time_ns").reset_index(drop=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", type=Path, required=True)
    parser.add_argument("--diagnostic-replay", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    worker = args.worker.resolve()
    diagnostic = args.diagnostic_replay.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    online_frame = pd.read_csv(worker / "strategy_case_summary.csv")
    backfilled_frame = pd.read_csv(diagnostic / "strategy_case_summary.csv")
    online = online_frame.loc[online_frame.experiment_candidate_id.eq(CANDIDATE_ID)].iloc[0]
    backfilled = backfilled_frame.loc[backfilled_frame.experiment_candidate_id.eq(CANDIDATE_ID)].iloc[0]
    online_decisions = _decisions(worker / "decisions" / f"{CANDIDATE_ID}.jsonl")
    backfilled_decisions = _decisions(diagnostic / "decisions" / f"{CANDIDATE_ID}.jsonl")
    joined = online_decisions.merge(backfilled_decisions, on="event_time_ns", how="outer", suffixes=("_as_recorded", "_backfilled"), indicator=True)
    same_target = np.isclose(
        pd.to_numeric(joined.target_as_recorded, errors="coerce"),
        pd.to_numeric(joined.target_backfilled, errors="coerce"),
        rtol=0.0,
        atol=1e-12,
        equal_nan=False,
    )
    divergent = joined.loc[(joined._merge != "both") | ~same_target].copy()
    divergent.to_csv(output / "decision_divergence.csv", index=False)
    first_divergence_ns = int(divergent.event_time_ns.min()) if len(divergent) else None
    comparison_rows = []
    for field in (
        "FIRST_TICK_Return", "FIRST_TICK_total_turnover_raw", "MAKER_Return",
        "MAKER_total_turnover_raw", "MAKER_quantity_fill_ratio", "MAKER_mean_target_position_error",
    ):
        a, b = float(online[field]), float(backfilled[field])
        comparison_rows.append({"metric": field, "as_recorded": a, "backfilled_diagnostic": b, "difference": b - a})
    pd.DataFrame(comparison_rows).to_csv(output / "metric_comparison.csv", index=False)

    fills_online = pd.read_csv(worker / "fills" / "simulated_fills.csv")
    fills_backfilled = pd.read_csv(diagnostic / "fills" / "simulated_fills.csv")
    fill_count_difference = len(fills_backfilled) - len(fills_online)
    return_delta = max(
        abs(float(backfilled.FIRST_TICK_Return) - float(online.FIRST_TICK_Return)),
        abs(float(backfilled.MAKER_Return) - float(online.MAKER_Return)),
    )
    turnover_delta = max(
        abs(float(backfilled.FIRST_TICK_total_turnover_raw) - float(online.FIRST_TICK_total_turnover_raw)),
        abs(float(backfilled.MAKER_total_turnover_raw) - float(online.MAKER_total_turnover_raw)),
    )
    insensitive = len(divergent) == 0 and fill_count_difference == 0 and return_delta <= 1e-12 and turnover_delta <= 1e-12
    result = {
        "status": "PASSED",
        "classification": "NUMERICALLY_INSENSITIVE_TO_SINGLE_GAP" if insensitive else "PERFORMANCE_SENSITIVE_TO_SINGLE_GAP",
        "evidence_label": LABEL,
        "post_hoc_data_label": "POST_HOC_BACKFILL_FOR_SENSITIVITY_ONLY",
        "first_decision_divergence_timestamp_ns": first_divergence_ns,
        "first_decision_divergence_timestamp_utc": (
            pd.Timestamp(first_divergence_ns, unit="ns", tz="UTC").isoformat() if first_divergence_ns is not None else None
        ),
        "divergent_decisions": int(len(divergent)),
        "fill_count_difference": int(fill_count_difference),
        "max_absolute_final_return_difference": return_delta,
        "max_absolute_final_turnover_difference": turnover_delta,
        "target_and_execution_paths_unchanged": bool(insensitive),
        "source_experiment_modified": False,
        "forward_validation_claim": False,
        "production_exchange_orders": 0,
    }
    (output / "gap_sensitivity.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
