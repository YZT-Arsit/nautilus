#!/usr/bin/env python3
"""Freeze the canonical-5y FIRST_TICK selection and reverse manifests."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

import pandas as pd


TIMEFRAMES = ("1m", "10m", "15m")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    frame.to_csv(tmp, index=False)
    os.replace(tmp, path)


def atomic_json(value: object, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def load_summary(path: Path, source_origin: str, semantic_group_id: str) -> list[dict[str, object]]:
    summary = json.loads(path.read_text(encoding="utf-8-sig"))
    if summary.get("status") != "COMPLETED" or summary.get("review_sample_version") != 2:
        raise ValueError(f"incomplete/old result: {path}")
    if summary.get("window_start") != "2021-07-01" or summary.get("window_end_exclusive") != "2026-07-01":
        raise ValueError(f"wrong window: {path}")
    if int(summary.get("n_daily_observations", 0)) != 1826:
        raise ValueError(f"wrong daily observations: {path}")
    members = str(summary.get("member_strategy_ids", summary.get("representative_strategy_id", ""))).split(";")
    rows = []
    for strategy_id in members:
        timeframe = str(summary["timeframe"])
        sharpe = float(summary["Sharpe"])
        turnover = float(summary["Turnover_raw"])
        raw_be = summary["BE_bps"]
        if raw_be is None:
            if turnover != 0.0:
                raise ValueError(f"undefined BE with nonzero turnover: {path}")
            be = 0.0
            be_defined = False
        else:
            be = float(raw_be)
            be_defined = True
        ret = float(summary["Return_fee0"])
        selected = abs(sharpe) > 1.5 if timeframe == "1m" else abs(be) > 10 and abs(sharpe) > 1.0
        reverse = (
            ret < 0 and sharpe < -1.5
            if timeframe == "1m"
            else ret < 0 and sharpe < -1.0 and be < -10
        )
        rows.append({
            "strategy_id": strategy_id,
            "source_origin": source_origin,
            "semantic_group_id": semantic_group_id,
            "representative_strategy_id": summary["representative_strategy_id"],
            "symbol": summary["symbol"],
            "timeframe": timeframe,
            "long_horizon_start": summary["window_start"],
            "long_horizon_end": summary["window_end_exclusive"],
            "n_daily_observations": int(summary["n_daily_observations"]),
            "Return_FIRST_TICK": ret,
            "Sharpe_FIRST_TICK": sharpe,
            "Signed_BE_FIRST_TICK": be,
            "Signed_BE_defined": be_defined,
            "MaxDD_FIRST_TICK": float(summary["MDD"]),
            "Turnover_FIRST_TICK": turnover,
            "selection_rule": "ABS_SHARPE_GT_1_5" if timeframe == "1m" else "ABS_BE_GT_10_AND_ABS_SHARPE_GT_1",
            "selected": selected,
            "negative_reverse_candidate": reverse,
            "normal_summary_path": str(path),
        })
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()

    rows: list[dict[str, object]] = []
    workbook_paths = sorted((args.root / "matrix_cases/symbol=BTCUSDT").glob("timeframe=*/semantic=*/summary.json"))
    for path in workbook_paths:
        semantic = path.parent.name.removeprefix("semantic=")
        rows.extend(load_summary(path, "WORKBOOK", semantic))
    pre_paths = sorted((args.root / "pre_workbook/matrix_cases/symbol=BTCUSDT").glob("timeframe=*/strategy=*/summary.json"))
    for path in pre_paths:
        strategy = path.parent.name.removeprefix("strategy=")
        rows.extend(load_summary(path, "PRE_WORKBOOK", f"PRE_WORKBOOK:{strategy}"))

    all_cases = pd.DataFrame(rows).sort_values(["source_origin", "strategy_id", "timeframe", "symbol"])
    if len(all_cases) != 993 or all_cases.strategy_id.nunique() != 331:
        raise ValueError(f"expected 993 cases/331 strategies, got {len(all_cases)}/{all_cases.strategy_id.nunique()}")
    if all_cases.duplicated(["strategy_id", "symbol", "timeframe"]).any():
        raise ValueError("duplicate logical cases")
    selection = args.root / "selection"
    atomic_csv(all_cases, selection / "long_horizon_first_tick_all_cases.csv")
    selected = all_cases[all_cases.selected].copy()
    atomic_csv(selected, selection / "long_horizon_first_tick_selected_cases.csv")
    candidates = all_cases[all_cases.negative_reverse_candidate].copy()
    candidate_path = selection / "long_horizon_negative_reverse_candidates.csv"
    atomic_csv(candidates, candidate_path)
    selected_hash = sha256(selection / "long_horizon_first_tick_selected_cases.csv")
    candidate_hash = sha256(candidate_path)
    atomic_json({
        "status": "FROZEN",
        "window_start": "2021-07-01",
        "window_end_exclusive": "2026-07-01",
        "n_daily_observations": 1826,
        "full_eligible_cases": len(all_cases),
        "strategy_ids": int(all_cases.strategy_id.nunique()),
        "selected_cases": len(selected),
        "selected_strategy_ids": int(selected.strategy_id.nunique()),
        "negative_reverse_candidates": len(candidates),
        "negative_reverse_strategy_ids": int(candidates.strategy_id.nunique()),
        "selected_manifest_sha256": selected_hash,
        "reverse_manifest_sha256": candidate_hash,
        "selection_used_march_pilot": False,
        "selection_metric": "LONG_HORIZON_FIRST_TICK",
    }, selection / "selection_freeze.json")
    print(json.dumps(json.loads((selection / "selection_freeze.json").read_text()), indent=2))


if __name__ == "__main__":
    main()
