#!/usr/bin/env python3
"""Validate the selectively re-materialized 1m review series."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    args = parser.parse_args()
    repo = args.repo.resolve()
    prior = pd.read_csv(repo / "outputs/deliverables/10m15m_tick_be_sharpe_review/all_10m15m_results.csv")
    selected_hashes = set(prior.loc[
        prior.Signed_BE_bps.abs().gt(10) & prior.Sharpe.abs().gt(1), "semantic_execution_hash"
    ].astype(str))
    inventory_path = repo / "outputs/internal_audit/final_manifests/cleanup_inventory_Server.csv"
    expected_hashes: dict[str, str] = {}
    with inventory_path.open(encoding="utf-8", newline="") as stream:
        for row in csv.DictReader(stream):
            rel = row["relative_path"].replace("\\", "/")
            if (
                "/matrix_cases/" in f"/{rel}"
                and "/timeframe=1m/" in rel
                and rel.endswith("/review_timeseries.parquet")
                and any(f"/semantic={semantic}/" in rel for semantic in selected_hashes)
            ):
                expected_hashes[rel] = row["sha256_or_status"]
    master = pd.read_csv(
        repo / "outputs/deliverables/boss_multitimeframe_final_delivery/02_full_results/boss_multitimeframe_tick_master.csv"
    )
    master = master[master.timeframe.eq("1m") & master.semantic_execution_hash.astype(str).isin(selected_hashes)]
    master = master.drop_duplicates(["semantic_execution_hash", "symbol", "timeframe"])
    missing = []
    mismatched = []
    max_residuals = {"Return": 0.0, "Turnover": 0.0, "BE": 0.0, "MDD": 0.0}
    for row in master.itertuples(index=False):
        rel = (
            Path("baseline_evaluation/boss_multitimeframe_tick_screen/matrix_cases")
            / f"symbol={row.symbol}" / "timeframe=1m"
            / f"semantic={row.semantic_execution_hash}" / "review_timeseries.parquet"
        ).as_posix()
        path = repo / "outputs" / rel
        if not path.is_file():
            missing.append(rel)
            continue
        expected_hash = expected_hashes.get(rel)
        actual_hash = sha256(path)
        if expected_hash and actual_hash != expected_hash:
            mismatched.append({"path": rel, "expected": expected_hash, "actual": actual_hash})
        frame = pd.read_parquet(path)
        result_return = float(frame.cumulative_return_with_premium.iloc[-1])
        turnover = float(frame.cumulative_turnover.iloc[-1])
        break_even = result_return * 10000.0 / turnover if turnover > 0 else float("nan")
        residuals = {
            "Return": abs(result_return - float(row.Return_fee0)),
            "Turnover": abs(turnover - float(row.Turnover_raw)),
            "BE": 0.0 if np.isnan(break_even) and np.isnan(float(row.BE_bps)) else abs(break_even - float(row.BE_bps)),
            "MDD": abs(float(frame.drawdown.min()) - float(row.MDD)),
        }
        for key, value in residuals.items():
            max_residuals[key] = max(max_residuals[key], value)
    hash_coverage = len(expected_hashes)
    status = (
        len(selected_hashes) == 28
        and len(master) == 252
        and not missing
        and not mismatched
        and hash_coverage == 252
        and max_residuals["Return"] <= 1e-10
        and max_residuals["Turnover"] <= 1e-6
        and max_residuals["BE"] <= 1e-10
        and max_residuals["MDD"] <= 1e-10
    )
    report = {
        "status": "PASSED" if status else "FAILED",
        "selected_semantic_groups": len(selected_hashes),
        "expected_physical_1m_series": len(master),
        "present": len(master) - len(missing),
        "missing": len(missing),
        "predelete_hash_coverage": hash_coverage,
        "predelete_hash_mismatches": len(mismatched),
        "max_aggregate_residuals": max_residuals,
        "missing_examples": missing[:5],
        "mismatch_examples": mismatched[:5],
    }
    target = repo / "outputs/internal_audit/final_manifests/rematerialized_filtered_1m_timeseries_validation.json"
    target.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if status else 2


if __name__ == "__main__":
    raise SystemExit(main())
