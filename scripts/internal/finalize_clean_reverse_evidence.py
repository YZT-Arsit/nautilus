#!/usr/bin/env python3
"""Separate retrospective reverse evidence from any clean forward evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = Path("outputs/baseline_evaluation/reverse_clean_validation")
OLD = Path("outputs/deliverables/strategy_execution_reverse_review")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    frame.to_csv(temp, index=False)
    os.replace(temp, path)


def atomic_json(value: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    os.replace(temp, path)


def retrospective_rows(frame: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for row in frame.itertuples(index=False):
        common = {
            "strategy_id": row.strategy_id,
            "semantic_group_id": row.semantic_group_id,
            "symbol": row.symbol,
            "timeframe": row.timeframe,
        }
        rows.append({
            **common,
            "evaluation_segment": "DISCOVERY",
            "evidence_class": "EXPLORATORY_SAME_SAMPLE",
            "start": row.discovery_start,
            "end": row.discovery_end_exclusive,
            "NORMAL_Return": row.Return_NORMAL_DISCOVERY,
            "NORMAL_Sharpe": row.Sharpe_NORMAL_DISCOVERY,
            "NORMAL_BE": row.Signed_BE_bps_NORMAL_DISCOVERY,
            "NORMAL_MaxDD": row.MaxDD_NORMAL_DISCOVERY,
            "NORMAL_Turnover": row.Turnover_raw_NORMAL_DISCOVERY,
            "REVERSE_Return": row.Return_REVERSE_DISCOVERY,
            "REVERSE_Sharpe": row.Sharpe_REVERSE_DISCOVERY,
            "REVERSE_BE": row.Signed_BE_bps_REVERSE_DISCOVERY,
            "REVERSE_MaxDD": row.MaxDD_REVERSE_DISCOVERY,
            "REVERSE_Turnover": row.Turnover_raw_REVERSE_DISCOVERY,
        })
        rows.append({
            **common,
            "evaluation_segment": "SPENT_VALIDATION",
            "evidence_class": "RETROSPECTIVE_CONTAMINATED",
            "start": row.validation_start,
            "end": row.validation_end_exclusive,
            "NORMAL_Return": row.Return_NORMAL,
            "NORMAL_Sharpe": row.Sharpe_NORMAL,
            "NORMAL_BE": row.Signed_BE_bps_NORMAL,
            "NORMAL_MaxDD": row.MaxDD_NORMAL,
            "NORMAL_Turnover": row.Turnover_raw_NORMAL,
            "REVERSE_Return": row.Return_REVERSE,
            "REVERSE_Sharpe": row.Sharpe_REVERSE,
            "REVERSE_BE": row.Signed_BE_bps_REVERSE,
            "REVERSE_MaxDD": row.MaxDD_REVERSE,
            "REVERSE_Turnover": row.Turnover_raw_REVERSE,
        })
    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=ROOT)
    args = parser.parse_args()
    repo = args.repo.resolve()
    output, old = repo / OUTPUT, repo / OLD
    manifest_path = output / "clean_reverse_candidate_manifest.csv"
    freeze = json.loads((output / "candidate_manifest_freeze.json").read_text())
    if sha256(manifest_path) != freeze["candidate_manifest_sha256"]:
        raise ValueError("candidate manifest changed after freeze")
    manifest = pd.read_csv(manifest_path)
    selected = manifest[manifest.reverse_selected.astype(str).str.lower().eq("true")]

    old_reverse = pd.read_csv(old / "reverse_validation/reverse_case_comparison.csv")
    retrospective = retrospective_rows(old_reverse)
    atomic_csv(retrospective, output / "retrospective_reverse_results.csv")
    mechanical = old_reverse[[
        "strategy_id", "semantic_group_id", "symbol", "timeframe",
        "Return_NORMAL_DISCOVERY", "Return_REVERSE_DISCOVERY",
        "Return_NORMAL", "Return_REVERSE",
    ]].copy()
    mechanical["discovery_Return_REVERSE_plus_NORMAL"] = (
        mechanical.Return_REVERSE_DISCOVERY + mechanical.Return_NORMAL_DISCOVERY
    )
    mechanical["spent_validation_Return_REVERSE_plus_NORMAL"] = (
        mechanical.Return_REVERSE + mechanical.Return_NORMAL
    )
    mechanical["mechanical_sign_inverse_discovery"] = np.isclose(
        mechanical.Return_REVERSE_DISCOVERY, -mechanical.Return_NORMAL_DISCOVERY,
        atol=1e-12, rtol=1e-12,
    )
    mechanical["mechanical_sign_inverse_spent_validation"] = np.isclose(
        mechanical.Return_REVERSE, -mechanical.Return_NORMAL,
        atol=1e-12, rtol=1e-12,
    )
    atomic_csv(mechanical, output / "reverse_mechanical_inversion_audit.csv")

    holdout = pd.read_csv(output / "clean_holdout_definition.csv")
    sufficient = holdout.status.eq("SUFFICIENT").all() and not holdout.empty
    if sufficient:
        raise ValueError("forward data marked sufficient but holdout runner is not authorized in this finalizer")
    holdout_columns = [
        "strategy_id", "semantic_group_id", "source_origin", "symbol", "timeframe",
        "evidence_class", "holdout_start", "holdout_end", "NORMAL_Return",
        "NORMAL_Sharpe", "NORMAL_BE", "NORMAL_MaxDD", "NORMAL_Turnover",
        "REVERSE_Return", "REVERSE_Sharpe", "REVERSE_BE", "REVERSE_MaxDD",
        "REVERSE_Turnover", "daily_observations", "completed_episodes",
        "positive_period_count", "negative_period_count", "clean_reverse_positive",
    ]
    atomic_csv(pd.DataFrame(columns=holdout_columns), output / "clean_reverse_holdout_results.csv")
    maker_columns = [
        "strategy_id", "symbol", "timeframe", "holdout_start", "holdout_end",
        "evidence_class", "maker_model", "Return", "Sharpe", "BE", "MaxDD",
        "Turnover", "quantity_fill_ratio", "zero_fill_rate",
        "target_position_error", "orders", "OrderFilled_events", "maker_status",
    ]
    atomic_csv(pd.DataFrame(columns=maker_columns), output / "maker_forward_results.csv")

    old_exploratory = int(
        (old_reverse.Return_REVERSE_DISCOVERY.gt(0))
        .sum()
    )
    old_contaminated = int(
        old_reverse.validation_positive.astype(str).str.lower().eq("true").sum()
    )
    summary_rows = [
        ["full eligible cases audited", len(manifest), "DISCOVERY_ONLY_SELECTION"],
        ["discovery-selected reverse candidates", len(selected), "DISCOVERY_ONLY_SELECTION"],
        ["candidate strategy IDs", selected.strategy_id.nunique(), "DISCOVERY_ONLY_SELECTION"],
        ["candidate independent semantic groups", selected.semantic_group_id.nunique(), "DISCOVERY_ONLY_SELECTION"],
        ["exploratory same-sample positives", old_exploratory, "EXPLORATORY_SAME_SAMPLE"],
        ["retrospective contaminated positives", old_contaminated, "RETROSPECTIVE_CONTAMINATED"],
        ["clean forward positives", 0, "CLEAN_FORWARD_HOLDOUT"],
        ["clean forward strategy IDs", 0, "CLEAN_FORWARD_HOLDOUT"],
        ["clean forward independent semantic groups", 0, "CLEAN_FORWARD_HOLDOUT"],
        ["maker-forward cases", 0, "CLEAN_FORWARD_HOLDOUT"],
        ["maker-forward positives", 0, "CLEAN_FORWARD_HOLDOUT"],
    ]
    atomic_csv(
        pd.DataFrame(summary_rows, columns=["metric", "value", "evidence_class"]),
        output / "reverse_evidence_summary.csv",
    )
    validation = {
        "status": "PASSED",
        "research_status": "PARTIAL",
        "full_eligible_cases_audited": len(manifest),
        "discovery_only_reverse_candidates": len(selected),
        "candidate_strategy_ids": int(selected.strategy_id.nunique()),
        "candidate_independent_semantic_groups": int(selected.semantic_group_id.nunique()),
        "candidate_manifest_sha256": freeze["candidate_manifest_sha256"],
        "previous_contaminated_validation_excluded": True,
        "exploratory_same_sample_positive": old_exploratory,
        "retrospective_contaminated_positive": old_contaminated,
        "clean_forward_status": str(holdout.status.iloc[0]),
        "clean_forward_reverse_cases": 0,
        "clean_forward_positive": 0,
        "maker_forward_cases": 0,
        "maker_forward_positive": 0,
        "target_reverse_mismatch": 0,
        "headline_conclusion": "REVERSE_HYPOTHESIS_INTERESTING_BUT_NOT_YET_VALIDATED",
        "new_parameter_changes": 0,
        "new_threshold_changes": 0,
    }
    atomic_json(validation, output / "validation_summary.json")
    print(json.dumps(validation, indent=2))


if __name__ == "__main__":
    main()
