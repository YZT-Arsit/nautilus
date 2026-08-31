#!/usr/bin/env python3
"""Refresh only boss-facing performance PNG titles from saved result streams."""

from __future__ import annotations

import argparse
import json
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd

from results.strategy_evaluation import (
    build_additive_strategy_evaluation_from_columns,
    render_additive_strategy_evaluation,
    validate_strategy_evaluation,
)
from scripts.internal.build_all_converted_workbook_results import (
    ARCHIVE,
    AUDIT,
    FINAL,
    SYMBOL,
    TOL,
    lag_dir,
    source_columns,
    zip_final,
)


def refresh(row: dict) -> str:
    path = Path(row["timeseries"])
    names = source_columns(path)
    frame = pd.read_parquet(path, columns=list(names))
    time_col, direction_col, trading_col, funding_col, turnover_col = names
    series, metrics = build_additive_strategy_evaluation_from_columns(
        event_time_ns=frame[time_col].to_numpy(np.int64, copy=False),
        executed_direction=frame[direction_col].to_numpy(float, copy=False),
        trading_return=frame[trading_col].to_numpy(float, copy=False),
        funding_return=frame[funding_col].to_numpy(float, copy=False),
        turnover=frame[turnover_col].to_numpy(float, copy=False),
    )
    validate_strategy_evaluation(series, metrics, tolerance=TOL)
    lag_minutes = int(row["lag_minutes"])
    destination = FINAL / "strategies" / row["strategy_id"] / row["timeframe"] / lag_dir(row["timeframe"], lag_minutes) / "performance.png"
    render_additive_strategy_evaluation(
        series, metrics, destination=destination,
        run_name=f"{row['strategy_id']} / {SYMBOL} / {row['timeframe']} / NORMAL",
        lag_label=f"{lag_minutes}m physical-time", turnover_display_percent=True,
    )
    return f"{row['strategy_id']} {lag_dir(row['timeframe'], lag_minutes)}"


def main() -> None:
    parser = argparse.ArgumentParser(); parser.add_argument("--workers", type=int, default=2); args = parser.parse_args()
    if not FINAL.is_dir():
        raise FileNotFoundError(FINAL)
    cases = pd.read_csv(AUDIT / "final_case_manifest.csv").to_dict("records")
    with ProcessPoolExecutor(max_workers=args.workers) as executor:
        futures = [executor.submit(refresh, row) for row in cases]
        for index, future in enumerate(as_completed(futures), 1):
            print(f"REFRESH_PERFORMANCE {index}/{len(futures)} {future.result()}", flush=True)
    digest, members, size = zip_final(FINAL, ARCHIVE)
    delivery_path = AUDIT / "delivery_summary.json"
    delivery = json.loads(delivery_path.read_text(encoding="utf-8"))
    delivery.update({"sha256": digest, "zip_members": members, "zip_size_bytes": size, "performance_titles_refreshed": len(cases)})
    delivery_path.write_text(json.dumps(delivery, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(delivery, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
