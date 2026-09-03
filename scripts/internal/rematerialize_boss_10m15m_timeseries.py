#!/usr/bin/env python3
"""Deterministically re-materialize only the deleted 10m/15m review series.

This deliberately reuses the frozen strategy scope, canonical market data,
compact first-trade index, and the original case function.  It validates every
recomputed aggregate against the preserved 9,612-row master before publishing
the compact Parquet.  No 1m/5m case is evaluated.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.internal.run_boss_multitimeframe_tick_screen import (  # noqa: E402
    load_symbol,
    run_group_case,
    semantic_groups,
    strategy_scope,
)

TIMEFRAMES = ("10m", "15m")
METRICS = ("Return_fee0", "Turnover_raw", "BE_bps", "MDD")
TOL = 1e-11


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, default=str) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def inventory_hashes(repo: Path) -> dict[str, str]:
    path = repo / "outputs/internal_audit/final_manifests/cleanup_inventory_Server.csv"
    hashes: dict[str, str] = {}
    with path.open(encoding="utf-8", newline="") as stream:
        for row in csv.DictReader(stream):
            rel = row["relative_path"].replace("\\", "/")
            if "/matrix_cases/" in f"/{rel}" and rel.endswith("/review_timeseries.parquet"):
                hashes[rel] = row["sha256_or_status"]
    return hashes


def expected_row(master: pd.DataFrame, semantic_hash: str, symbol: str, timeframe: str) -> pd.Series:
    rows = master[
        master.semantic_execution_hash.eq(semantic_hash)
        & master.symbol.eq(symbol)
        & master.timeframe.eq(timeframe)
    ]
    if rows.empty:
        raise ValueError(f"master row missing: {semantic_hash}/{symbol}/{timeframe}")
    for metric in METRICS:
        values = rows[metric].astype(float).to_numpy()
        if np.ptp(values) > TOL:
            raise ValueError(f"duplicate identity metrics disagree: {metric}")
    return rows.iloc[0]


def metric_residual(actual: object, expected: object) -> float:
    left = float("nan") if actual is None else float(actual)
    right = float(expected)
    if np.isnan(left) and np.isnan(right):
        return 0.0
    return abs(left - right)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=ROOT)
    parser.add_argument("--symbols", required=True, help="comma-separated frozen symbols")
    parser.add_argument("--limit-groups", type=int)
    args = parser.parse_args()
    repo = args.repo.resolve()
    result_root = repo / "outputs/baseline_evaluation/boss_multitimeframe_tick_screen"
    master_path = (
        repo / "outputs/deliverables/boss_multitimeframe_final_delivery/02_full_results/"
        "boss_multitimeframe_tick_master.csv"
    )
    master = pd.read_csv(master_path)
    master = master[master.timeframe.isin(TIMEFRAMES)].copy()
    scope_path = result_root / "boss_multitimeframe_strategy_scope.csv"
    strategies = (
        strategy_scope(scope_path)
        if scope_path.is_file()
        else sorted(master.strategy_id.astype(str).unique())
    )
    if len(strategies) != 267:
        raise ValueError(f"frozen master must contain 267 strategies, found {len(strategies)}")
    groups = semantic_groups(strategies)
    if args.limit_groups:
        groups = groups[: args.limit_groups]
    hashes = inventory_hashes(repo)
    window_path = result_root / "boss_tick_index_data_window.json"
    window = (
        json.loads(window_path.read_text(encoding="utf-8"))
        if window_path.is_file()
        else {"common_start": "2024-07-01", "common_end_exclusive": "2026-06-30"}
    )
    start = window["common_start"]
    end_exclusive = window["common_end_exclusive"]
    end_inclusive = (date.fromisoformat(end_exclusive) - timedelta(days=1)).isoformat()
    end_ns = int(pd.Timestamp(end_exclusive, tz="UTC").value)
    state_root = result_root / "timeseries_rematerialization_state"
    total = 0
    skipped = 0
    max_residual = 0.0
    hash_mismatches: list[dict[str, str]] = []

    for symbol in args.symbols.split(","):
        bars, funding, execution, tick_prices, waits = load_symbol(
            repo / "historical_data/market_data",
            result_root / "tick_execution_index",
            symbol,
            start,
            end_inclusive,
        )
        completed = 0
        for timeframe in TIMEFRAMES:
            for semantic_hash, members, source in groups:
                destination = (
                    result_root / "matrix_cases" / f"symbol={symbol}" / f"timeframe={timeframe}"
                    / f"semantic={semantic_hash}" / "review_timeseries.parquet"
                )
                rel = destination.relative_to(repo / "outputs").as_posix()
                wanted_hash = hashes.get(rel)
                if destination.is_file() and wanted_hash and sha256(destination) == wanted_hash:
                    skipped += 1
                    completed += 1
                    continue
                summary, review = run_group_case(
                    representative=members[0], members=members, source=source,
                    semantic_hash=semantic_hash, symbol=symbol, timeframe=timeframe,
                    bars=bars, funding=funding, execution=execution, tick_prices=tick_prices,
                    waits=waits, end_ns=end_ns,
                )
                expected = expected_row(master, semantic_hash, symbol, timeframe)
                residuals = {metric: metric_residual(summary[metric], expected[metric]) for metric in METRICS}
                max_residual = max(max_residual, *residuals.values())
                if any(value > TOL for value in residuals.values()):
                    raise RuntimeError(
                        f"aggregate invariance failed {symbol}/{timeframe}/{semantic_hash}: {residuals}"
                    )
                destination.parent.mkdir(parents=True, exist_ok=True)
                temporary = destination.with_suffix(".parquet.tmp")
                review.to_parquet(temporary, index=False, compression="zstd")
                os.replace(temporary, destination)
                actual_hash = sha256(destination)
                if wanted_hash and actual_hash != wanted_hash:
                    hash_mismatches.append({"relative_path": rel, "expected": wanted_hash, "actual": actual_hash})
                total += 1
                completed += 1
                if completed % 10 == 0:
                    atomic_json(state_root / f"{symbol}.json", {
                        "status": "RUNNING", "symbol": symbol, "completed": completed,
                        "planned": len(groups) * len(TIMEFRAMES), "max_metric_residual": max_residual,
                    })
        atomic_json(state_root / f"{symbol}.json", {
            "status": "PASSED", "symbol": symbol, "completed": completed,
            "planned": len(groups) * len(TIMEFRAMES), "newly_materialized": total,
            "skipped_existing": skipped, "max_metric_residual": max_residual,
            "parquet_hash_mismatches": len(hash_mismatches),
        })
    print(json.dumps({
        "status": "PASSED", "newly_materialized": total, "skipped_existing": skipped,
        "max_metric_residual": max_residual, "parquet_hash_mismatches": len(hash_mismatches),
        "hash_mismatch_examples": hash_mismatches[:5],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
