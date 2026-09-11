#!/usr/bin/env python3
"""Build the strategy-first four-mode review from frozen execution artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
OLD = Path("outputs/deliverables/execution_method_and_reverse_review")
WORK = Path("outputs/baseline_evaluation/execution_method_and_reverse_review")
DEST = Path("outputs/deliverables/strategy_execution_reverse_review")
MODES = (
    "01_FIRST_TICK_NORMAL", "02_MAKER_NORMAL",
    "03_FIRST_TICK_REVERSE", "04_MAKER_REVERSE",
)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def atomic_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    frame.to_csv(temp, index=False)
    os.replace(temp, path)


def atomic_json(value: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, indent=2, allow_nan=False, default=str) + "\n", encoding="utf-8")
    os.replace(temp, path)


def bool_series(series: pd.Series) -> pd.Series:
    return series.astype(str).str.lower().eq("true")


def persistence(path: Path, maker: bool) -> int:
    frame = pd.read_parquet(path, columns=["actual_position" if maker else "position"])
    values = frame.iloc[:, 0].to_numpy(float)
    signs = np.sign(values).astype(np.int8)
    starts = np.flatnonzero(np.r_[True, signs[1:] != signs[:-1]])
    ends = np.r_[starts[1:], len(signs)]
    held = (ends - starts)[signs[starts] != 0] / 60.0
    switches = int(((signs[1:] * signs[:-1]) < 0).sum())
    days = len(signs) / 1440.0
    return int(
        (signs != 0).mean() >= .90
        and (float(np.median(held)) if len(held) else 0.0) >= 24
        and (switches / days if days else 0.0) <= 1
    )


def copy_file(source: Path, destination: Path) -> None:
    if not source.is_file():
        raise FileNotFoundError(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def main() -> None:  # noqa: C901
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=ROOT)
    args = parser.parse_args()
    repo = args.repo.resolve()
    old, work, final = repo / OLD, repo / WORK, repo / DEST
    staging = final.with_name(final.name + ".building")
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)

    frozen_path = old / "selection/first_tick_selected_cases.csv"
    freeze_hash = sha256(frozen_path)
    frozen = pd.read_csv(frozen_path)
    pairs = pd.read_csv(old / "execution_pair_index.csv")
    reverse = pd.read_csv(work / "reverse_validation/reverse_case_comparison.csv")
    mapping = pd.read_csv(work / "maker_signals/maker_case_mapping.csv")
    reverse_metrics = pd.read_csv(work / "reverse_maker_comparison/execution_metrics.csv")

    neg = (
        (frozen.timeframe.eq("1m") & frozen.Return_FIRST_TICK.lt(0) & frozen.Sharpe_FIRST_TICK.lt(-1.5))
        | (frozen.timeframe.isin(["10m", "15m"]) & frozen.Return_FIRST_TICK.lt(0)
           & frozen.Sharpe_FIRST_TICK.lt(-1.0) & frozen.Signed_BE_FIRST_TICK.lt(-10.0))
    )
    negative = frozen.loc[neg].copy()
    keys = ["strategy_id", "symbol", "timeframe"]
    if len(negative) != 676 or len(reverse) != 676:
        raise ValueError(f"negative/reverse reconciliation failed: {len(negative)} / {len(reverse)}")
    if set(map(tuple, negative[keys].to_numpy())) != set(map(tuple, reverse[keys].to_numpy())):
        raise ValueError("strict negative candidate identities differ from reverse results")

    negative_out = negative.rename(columns={
        "strategy_id": "strategy", "Return_FIRST_TICK": "Return_NORMAL",
        "Sharpe_FIRST_TICK": "Sharpe_NORMAL", "Signed_BE_FIRST_TICK": "BE_NORMAL",
    })
    negative_out["reverse_candidate_reason"] = np.where(
        negative_out.timeframe.eq("1m"),
        "Return<0 AND Sharpe<-1.5",
        "Return<0 AND Sharpe<-1.0 AND Signed_BE<-10bps",
    )
    negative_out = negative_out.sort_values(["BE_NORMAL", "Sharpe_NORMAL"], ascending=True)

    map_lookup = mapping.set_index(["symbol", "semantic_group_id", "timeframe"])["case_key"]
    metric_lookup = reverse_metrics.set_index(["symbol", "case_key", "execution_model"])
    reverse_lookup = reverse.set_index(keys)
    mode_rows: list[dict] = []

    for row in pairs.itertuples(index=False):
        base = dict(strategy_id=row.strategy_id, source_origin=row.source_origin,
                    semantic_group_id=row.semantic_group_id, symbol=row.symbol, timeframe=row.timeframe)
        mode_rows.extend([
            {**base, "trading_mode": MODES[0], "Return": row.Return_FIRST_TICK,
             "Sharpe": row.Sharpe_FIRST_TICK, "Signed_BE": row.BE_FIRST_TICK,
             "MaxDD": row.MaxDD_FIRST_TICK, "Turnover": row.Turnover_FIRST_TICK,
             "Persistent": row.Persistent_FIRST_TICK, "fill_ratio": math.nan,
             "zero_fill_rate": math.nan, "validation_status": "ORIGINALLY_SELECTED_FIRST_TICK"},
            {**base, "trading_mode": MODES[1], "Return": row.Return_MAKER,
             "Sharpe": row.Sharpe_MAKER, "Signed_BE": row.BE_MAKER,
             "MaxDD": row.MaxDD_MAKER, "Turnover": row.Turnover_MAKER,
             "Persistent": row.Persistent_MAKER, "fill_ratio": row.quantity_fill_ratio_MAKER,
             "zero_fill_rate": row.zero_fill_rate_MAKER, "validation_status": "HISTORICAL_L1_SENSITIVITY_ONLY"},
        ])

    for item in negative.itertuples(index=False):
        case_key = map_lookup.loc[(item.symbol, item.semantic_group_id, item.timeframe)]
        if isinstance(case_key, pd.Series): case_key = case_key.iloc[0]
        temporal = reverse_lookup.loc[(item.strategy_id, item.symbol, item.timeframe)]
        validated = str(temporal.reverse_validation_positive).lower() == "true"
        eligible = str(temporal.normal_discovery_negative).lower() == "true"
        base = dict(strategy_id=item.strategy_id, source_origin=item.source_origin,
                    semantic_group_id=item.semantic_group_id, symbol=item.symbol, timeframe=item.timeframe)
        for model, mode, maker in (
            ("STRICT_REVERSE_FIRST_TICK", MODES[2], False),
            ("STRICT_REVERSE_GTC_UNTIL_SIGNAL_INVALID", MODES[3], True),
        ):
            metric = metric_lookup.loc[(item.symbol, case_key, model)]
            if maker:
                values = (metric.Return_gross, metric.Sharpe_gross, metric.Signed_BE_bps_gross,
                          metric.Max_Drawdown_gross, metric.Turnover_raw,
                          metric.quantity_fill_ratio, metric.zero_fill_order_rate)
                p = work / "reverse_maker_comparison/paths" / item.symbol / f"{case_key}__MAKER.parquet"
            else:
                values = (metric.Return, metric.Sharpe, metric.Signed_BE_bps,
                          metric.Max_Drawdown, metric.Turnover_raw, math.nan, math.nan)
                p = work / "reverse_maker_comparison/paths" / item.symbol / f"{case_key}__FIRST_TICK.parquet"
            status = (
                "VALIDATED_REVERSE_POSITIVE" if (not maker and validated)
                else "TEMPORAL_VALIDATION_FAILED" if (not maker and eligible)
                else "EXPLORATORY_ONLY_DISCOVERY_NOT_ELIGIBLE" if not maker
                else "HISTORICAL_L1_SENSITIVITY_ONLY"
            )
            mode_rows.append({**base, "trading_mode": mode, "Return": values[0], "Sharpe": values[1],
                              "Signed_BE": values[2], "MaxDD": values[3], "Turnover": values[4],
                              "Persistent": persistence(p, maker), "fill_ratio": values[5],
                              "zero_fill_rate": values[6], "validation_status": status})

    modes = pd.DataFrame(mode_rows)
    modes = modes.sort_values(["strategy_id", "trading_mode", "timeframe", "symbol"])
    atomic_csv(modes, staging / "mode_comparison.csv")
    atomic_csv(negative_out, staging / "reverse_validation/negative_reverse_candidates.csv")

    # Reverse comparison keeps temporal FIRST_TICK evidence and historical L1 maker sensitivity distinct.
    reverse_out = reverse.copy()
    reverse_out["execution_method"] = "FIRST_TICK"
    reverse_out["Delta_Return"] = reverse_out.Return_REVERSE - reverse_out.Return_NORMAL
    reverse_out["Delta_Sharpe"] = reverse_out.Sharpe_REVERSE - reverse_out.Sharpe_NORMAL
    reverse_out["Delta_BE"] = reverse_out.Signed_BE_bps_REVERSE - reverse_out.Signed_BE_bps_NORMAL
    reverse_out["exploratory_positive"] = (
        reverse_out.Return_REVERSE_DISCOVERY.gt(0) & reverse_out.Sharpe_REVERSE_DISCOVERY.gt(0)
        & reverse_out.Signed_BE_bps_REVERSE_DISCOVERY.gt(0)
    )
    reverse_out["validation_positive"] = bool_series(reverse_out.reverse_validation_positive)
    atomic_csv(reverse_out, staging / "reverse_validation/reverse_case_comparison.csv")
    validated = reverse_out[reverse_out.validation_positive].sort_values(
        ["Signed_BE_bps_REVERSE", "Sharpe_REVERSE", "Return_REVERSE"], ascending=False
    )
    atomic_csv(validated, staging / "reverse_validation/validated_reverse_candidates.csv")
    copy_file(old / "reverse_validation/validation_window.json", staging / "reverse_validation/validation_window.json")
    copy_file(old / "reverse_validation/maker_reverse_validation_status.csv", staging / "reverse_validation/maker_reverse_validation_status.csv")
    copy_file(frozen_path, staging / "selection/first_tick_selected_cases.csv")
    copy_file(old / "selection/selection_freeze.json", staging / "selection/selection_freeze.json")
    copy_file(old / "selection/positive_be_priority.csv", staging / "selection/positive_be_priority.csv")
    shutil.copytree(old / "data_provenance", staging / "data_provenance", dirs_exist_ok=True)

    # Strategy-first figure hierarchy. Existing images are copied byte-for-byte.
    for row in pairs.itertuples(index=False):
        root = staging / "strategies" / row.strategy_id
        for old_mode, new_mode in (("01_FIRST_TICK", MODES[0]), ("02_MAKER", MODES[1])):
            source = old / old_mode / row.strategy_id / "performance" / row.timeframe / f"{row.symbol}__performance.png"
            copy_file(source, root / new_mode / "performance" / row.timeframe / source.name)
        # Copy each summary once.
        for old_mode, new_mode in (("01_FIRST_TICK", MODES[0]), ("02_MAKER", MODES[1])):
            source = old / old_mode / row.strategy_id / f"summary_{row.timeframe}.png"
            destination = root / new_mode / source.name
            if not destination.exists(): copy_file(source, destination)

    for item in negative.itertuples(index=False):
        root = staging / "strategies" / item.strategy_id
        for variant, mode in (("NORMAL", MODES[0]), ("STRICT_REVERSE", MODES[2])):
            for window in ("discovery", "validation"):
                source = old / "reverse_validation" / variant / "FIRST_TICK" / item.strategy_id / item.timeframe / f"{item.symbol}__{window}.png"
                copy_file(source, root / mode / "performance" / item.timeframe / f"{item.symbol}__reverse_study_{window}.png")
        for variant, mode in (("NORMAL", MODES[1]), ("STRICT_REVERSE", MODES[3])):
            source = old / "reverse_validation" / variant / "MAKER" / item.strategy_id / item.timeframe / f"{item.symbol}__historical_march_sensitivity.png"
            copy_file(source, root / mode / "performance" / item.timeframe / f"{item.symbol}__historical_march_sensitivity.png")

    # Generate reverse-mode heatmaps directly from the already-computed metrics.
    from scripts.internal.finalize_execution_method_reverse_review import render_summary
    for (strategy, mode, timeframe), group in modes[modes.trading_mode.isin(MODES[2:])].groupby(
        ["strategy_id", "trading_mode", "timeframe"]
    ):
        summary = group[["symbol", "Return", "Signed_BE", "Sharpe", "MaxDD", "Persistent"]].rename(columns={"Signed_BE": "BE"})
        render_summary(summary, staging / "strategies" / strategy / mode / f"summary_{timeframe}.png", strategy, timeframe, mode)

    index_rows = []
    for strategy, group in modes.groupby("strategy_id"):
        candidate = negative.strategy_id.eq(strategy).any()
        validated_strategy = validated.strategy_id.eq(strategy).any()
        strategy_root = staging / "strategies" / strategy
        atomic_csv(group, strategy_root / "strategy_summary.csv")
        counts = group.groupby("trading_mode").size().to_dict()
        index_rows.append({
            "strategy_id": strategy, "source_origin": group.source_origin.iloc[0],
            "semantic_group_id": group.semantic_group_id.iloc[0], "originally_selected": True,
            "negative_reverse_candidate": candidate,
            "modes_available": ";".join(mode for mode in MODES if mode in counts),
            "first_tick_normal_cases": counts.get(MODES[0], 0), "maker_normal_cases": counts.get(MODES[1], 0),
            "first_tick_reverse_cases": counts.get(MODES[2], 0), "maker_reverse_cases": counts.get(MODES[3], 0),
            "validated_reverse_positive": validated_strategy,
            "strategy_folder": f"strategies/{strategy}",
        })
    index = pd.DataFrame(index_rows).sort_values("strategy_id")
    atomic_csv(index, staging / "strategy_index.csv")

    maker_reverse = modes[modes.trading_mode.eq(MODES[3])]
    maker_positive = maker_reverse.Return.gt(0) & maker_reverse.Sharpe.gt(0) & maker_reverse.Signed_BE.gt(0)
    exploratory = reverse_out.exploratory_positive
    top_be = validated.iloc[0]
    top_sharpe = validated.sort_values("Sharpe_REVERSE", ascending=False).iloc[0]
    answers = pd.DataFrame([
        ["selected strategies", frozen.strategy_id.nunique()],
        ["negative reverse candidate cases", len(negative)],
        ["negative reverse candidate strategies", negative.strategy_id.nunique()],
        ["reverse exploratory positive", int(exploratory.sum())],
        ["reverse temporally validated positive", len(validated)],
        ["FIRST_TICK selected cases", len(frozen)],
        ["FIRST_TICK selected cases also maker positive", int((pairs.Return_MAKER.gt(0)&pairs.Sharpe_MAKER.gt(0)&pairs.BE_MAKER.gt(0)).sum())],
        ["reverse positive under maker", int(maker_positive.sum())],
        ["largest validated reverse BE", f"{top_be.strategy_id}/{top_be.symbol}/{top_be.timeframe}/{top_be.Signed_BE_bps_REVERSE:.6f}"],
        ["best validated reverse Sharpe", f"{top_sharpe.strategy_id}/{top_sharpe.symbol}/{top_sharpe.timeframe}/{top_sharpe.Sharpe_REVERSE:.6f}"],
        ["maker quote data", "Binance USD-M historical bookTicker / L1 BBO"],
        ["maker trade data", "Binance USD-M historical trades"],
    ], columns=["question", "answer"])
    atomic_csv(answers, staging / "key_answers.csv")

    # Deterministic sampled real-reverse invariants.
    sampled = negative.sort_values(keys).drop_duplicates(["semantic_group_id", "symbol", "timeframe"]).iloc[::17].head(25)
    mismatches = 0
    for item in sampled.itertuples(index=False):
        path_key = "case_" + hashlib.sha256(f"{item.semantic_group_id}|{item.timeframe}".encode()).hexdigest()[:20]
        root = work / "reverse_validation/shards" / item.symbol / "paths" / path_key
        normal_path = pd.read_parquet(
            root / "NORMAL__DISCOVERY.parquet",
            columns=["event_time_ns", "executed_position"],
        ).rename(columns={"executed_position": "normal"})
        reverse_path = pd.read_parquet(
            root / "STRICT_REVERSE__DISCOVERY.parquet",
            columns=["event_time_ns", "executed_position"],
        ).rename(columns={"executed_position": "reverse"})
        aligned = normal_path.merge(reverse_path, on="event_time_ns", how="inner")
        if len(aligned) < min(len(normal_path), len(reverse_path)) - 1:
            raise ValueError("unexpected reverse-path timestamp divergence")
        mismatches += int(np.count_nonzero(~np.isclose(aligned.reverse, -aligned.normal)))
    if mismatches:
        raise ValueError(f"real reverse target mismatch: {mismatches}")
    top_dirs = {p.name for p in staging.iterdir() if p.is_dir()}
    if "01_FIRST_TICK" in top_dirs or "02_MAKER" in top_dirs:
        raise ValueError("execution-first hierarchy leaked into new delivery")
    if sha256(frozen_path) != freeze_hash:
        raise ValueError("frozen FIRST_TICK manifest changed")
    if len(modes[modes.trading_mode.eq(MODES[0])]) != 878 or len(modes[modes.trading_mode.eq(MODES[1])]) != 878:
        raise ValueError("normal mode case accounting mismatch")
    if len(modes[modes.trading_mode.eq(MODES[2])]) != 676 or len(modes[modes.trading_mode.eq(MODES[3])]) != 676:
        raise ValueError("reverse mode case accounting mismatch")
    summary = {
        "status": "PASSED", "folder_hierarchy": "STRATEGY -> TRADING MODES",
        "originally_selected_strategy_ids": int(frozen.strategy_id.nunique()),
        "originally_selected_cases": len(frozen), "negative_reverse_candidate_cases": len(negative),
        "negative_reverse_candidate_strategies": int(negative.strategy_id.nunique()),
        "first_tick_reverse_cases": 676, "maker_reverse_cases": 676,
        "exploratory_reverse_positive": int(exploratory.sum()),
        "temporally_validated_reverse_positive": len(validated),
        "validated_reverse_strategy_ids": int(validated.strategy_id.nunique()),
        "reverse_positive_under_maker": int(maker_positive.sum()),
        "target_reverse_mismatch": mismatches, "maker_data_unavailable": 0,
        "parameter_search": 0, "parameter_retuning": 0,
        "selection_manifest_sha256": freeze_hash,
    }
    atomic_json(summary, staging / "validation_summary.json")
    if final.exists(): shutil.rmtree(final)
    os.replace(staging, final)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
