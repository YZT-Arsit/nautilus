#!/usr/bin/env python3
"""Run actual NORMAL and target-sign STRICT_REVERSE on the frozen temporal split."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yaml


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.internal.build_phase4a_baseline_evaluation import drawdown  # noqa: E402
from scripts.internal.run_all_strategy_timeframe_lag import (  # noqa: E402
    build_strategy_clock,
    run_decision_lifecycle,
)
from scripts.internal.run_boss_multitimeframe_tick_screen import (  # noqa: E402
    NOTIONAL,
    load_symbol,
    review_sample_indices,
)
from scripts.internal.run_constant_notional_overlay import calculate_overlay  # noqa: E402
from strategy_framework.registry import get_entry  # noqa: E402


SYMBOLS = (
    "XRPUSDT", "DOGEUSDT", "SUIUSDT", "BNBUSDT", "ETHUSDT",
    "BTCUSDT", "1000PEPEUSDT", "SOLUSDT", "ADAUSDT",
)
START = "2024-07-01"
END_EXCLUSIVE = "2026-06-30"
END_INCLUSIVE = "2026-06-29"
SPLIT = pd.Timestamp("2025-07-01", tz="UTC")
WORK = Path("outputs/baseline_evaluation/execution_method_and_reverse_review")
INDEX_ROOT = Path("outputs/baseline_evaluation/boss_multitimeframe_tick_screen/tick_execution_index")
MARKET_ROOT = Path("historical_data/market_data")


def atomic_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    frame.to_csv(temporary, index=False)
    os.replace(temporary, path)


def atomic_json(payload: dict, path: Path) -> None:
    def clean(value):
        if isinstance(value, dict):
            return {key: clean(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [clean(item) for item in value]
        if isinstance(value, np.generic):
            value = value.item()
        if isinstance(value, float) and not math.isfinite(value):
            return None
        return value

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(clean(payload), indent=2, allow_nan=False, default=str) + "\n")
    os.replace(temporary, path)


def config_for(strategy_id: str, repo: Path) -> dict:
    path = Path(get_entry(strategy_id).default_config_path)
    if not path.is_absolute():
        path = repo / path
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def daily_sharpe(times: np.ndarray, increments: np.ndarray) -> float:
    cumulative = np.cumsum(increments, dtype=float)
    series = pd.Series(cumulative, index=pd.to_datetime(times, unit="ns", utc=True))
    daily = series.resample("1D").last().diff().dropna()
    sd = daily.std(ddof=1)
    return float(daily.mean() / sd * math.sqrt(365)) if len(daily) >= 2 and sd else math.nan


def metrics(result: pd.DataFrame, mask: np.ndarray) -> dict:
    part = result.loc[mask]
    increments = part.total_return.to_numpy(float)
    turnover = float(part.turnover.sum())
    value = float(increments.sum())
    return {
        "Return": value,
        "Sharpe": daily_sharpe(part.event_time_ns.to_numpy(np.int64), increments),
        "Signed_BE_bps": value * 10_000 / turnover if turnover else math.nan,
        "MaxDD": drawdown(increments),
        "Turnover_raw": turnover,
    }


def review_path(result: pd.DataFrame, mask: np.ndarray) -> pd.DataFrame:
    part = result.loc[mask].reset_index(drop=True)
    increments = part.total_return.to_numpy(float)
    cumulative = np.cumsum(increments)
    equity = 1.0 + cumulative
    peak = np.maximum.accumulate(np.r_[1.0, equity])[1:]
    dd = np.divide(equity, peak, out=np.zeros_like(equity), where=peak > 0) - 1.0
    full = pd.DataFrame(
        {
            "event_time_ns": part.event_time_ns,
            "cumulative_return": cumulative,
            "cumulative_turnover": np.cumsum(part.turnover.to_numpy(float)),
            "executed_position": part.direction,
            "drawdown": dd,
        }
    )
    return full.iloc[review_sample_indices(part.direction, dd)].reset_index(drop=True)


def execute(
    direction: np.ndarray,
    event_time: np.ndarray,
    close: np.ndarray,
    funding: pd.DataFrame,
    tick_prices: np.ndarray,
) -> pd.DataFrame:
    result, _ = calculate_overlay(
        pd.DataFrame({"event_time_ns": event_time, "close": close, "position": direction}),
        funding,
        tick_prices,
        notional_usdt=NOTIONAL,
        slippage_bps=0.0,
        vip9_fee_bps=0.0,
        vip0_fee_bps=5.0,
        position_policy="strict_constant_notional",
    )
    return result


def main() -> None:  # noqa: C901
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=ROOT)
    parser.add_argument("--symbol", choices=SYMBOLS, required=True)
    parser.add_argument("--work", type=Path)
    parser.add_argument("--market-root", type=Path)
    parser.add_argument("--index-root", type=Path)
    parser.add_argument(
        "--force-negative-candidate",
        action="append",
        default=[],
        help="Explicit semantic_group_id,timeframe case to execute for same-window exploratory review; discovery eligibility remains unchanged.",
    )
    args = parser.parse_args()
    repo = args.repo.resolve()
    work = (args.work or repo / WORK).resolve()
    market_root = (args.market_root or repo / MARKET_ROOT).resolve()
    index_root = (args.index_root or repo / INDEX_ROOT).resolve()
    output = work / "reverse_validation" / "shards" / args.symbol
    output.mkdir(parents=True, exist_ok=True)
    frozen = pd.read_csv(work / "selection/first_tick_selected_cases.csv")
    logical = frozen[frozen.symbol.eq(args.symbol)].copy()
    physical = (
        logical.sort_values("strategy_id")
        .drop_duplicates(["semantic_group_id", "timeframe"])
        [["semantic_group_id", "timeframe", "strategy_id"]]
    )
    bars, funding, execution_events, tick_prices, _ = load_symbol(
        market_root, index_root, args.symbol, START, END_INCLUSIVE
    )
    event_time = np.fromiter((bar.event_time_ns for bar in bars), dtype=np.int64)
    close = np.fromiter((bar.close for bar in bars), dtype=float)
    discovery = event_time < int(SPLIT.value)
    validation = ~discovery
    rows: list[dict] = []
    candidates: list[dict] = []
    forced_candidates = {
        tuple(value.rsplit(",", 1)) for value in args.force_negative_candidate
    }
    for item in physical.itertuples(index=False):
        forced_candidate = (item.semantic_group_id, item.timeframe) in forced_candidates
        checkpoint_key = "case_" + hashlib.sha256(
            f"{item.semantic_group_id}|{item.timeframe}".encode()
        ).hexdigest()[:20]
        checkpoint_path = output / "physical_metrics" / f"{checkpoint_key}.json"
        if checkpoint_path.exists():
            saved = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            if not saved.get("normal_discovery_negative", False) and not forced_candidate:
                continue
            if forced_candidate and "Return_REVERSE" not in saved:
                checkpoint_path.unlink()
            else:
                members = logical.loc[
                    logical.semantic_group_id.eq(item.semantic_group_id)
                    & logical.timeframe.eq(item.timeframe), "strategy_id"
                ].drop_duplicates()
                for strategy_id in members:
                    rows.append({"strategy_id":strategy_id, **saved})
                continue
        source = config_for(item.strategy_id, repo)
        direction, _, lifecycle = run_decision_lifecycle(
            strategy_name=item.strategy_id,
            source_config=source,
            frequency=item.timeframe,
            lag_minutes=0,
            bars_1m=bars,
            strategy_bars=build_strategy_clock(bars, item.timeframe),
            execution_events=execution_events,
            end_exclusive_ns=int(pd.Timestamp(END_EXCLUSIVE, tz="UTC").value),
        )
        direction = np.asarray(direction, dtype=float)
        reverse_direction = -direction
        if not np.array_equal(reverse_direction, -direction):
            raise ValueError("STRICT_REVERSE target-sign invariant failed")
        normal = execute(direction, event_time, close, funding, tick_prices)
        normal_discovery = metrics(normal, discovery)
        normal_validation = metrics(normal, validation)
        discovery_candidate = normal_discovery["Return"] < 0
        candidate = discovery_candidate or forced_candidate
        base = {
            "semantic_group_id": item.semantic_group_id,
            "representative_strategy_id": item.strategy_id,
            "symbol": args.symbol,
            "timeframe": item.timeframe,
            "validation_type": "INTERNAL_TEMPORAL_VALIDATION",
            "discovery_start": START,
            "discovery_end_exclusive": "2025-07-01",
            "validation_start": "2025-07-01",
            "validation_end_exclusive": END_EXCLUSIVE,
            "normal_discovery_negative": discovery_candidate,
            "forced_full_window_negative_candidate": forced_candidate,
            "normal_discovery_Return_negative": normal_discovery["Return"] < 0,
            "normal_discovery_Sharpe_negative": normal_discovery["Sharpe"] < 0,
            "normal_discovery_BE_negative": normal_discovery["Signed_BE_bps"] < 0,
        }
        if not candidate:
            atomic_json(
                {**base, **{f"{key}_NORMAL_DISCOVERY":value for key, value in normal_discovery.items()}},
                checkpoint_path,
            )
            continue
        reverse = execute(reverse_direction, event_time, close, funding, tick_prices)
        reverse_discovery = metrics(reverse, discovery)
        reverse_validation = metrics(reverse, validation)
        physical_row = {
            **base,
            **{f"{key}_NORMAL_DISCOVERY": value for key, value in normal_discovery.items()},
            **{f"{key}_REVERSE_DISCOVERY": value for key, value in reverse_discovery.items()},
            **{f"{key}_NORMAL": value for key, value in normal_validation.items()},
            **{f"{key}_REVERSE": value for key, value in reverse_validation.items()},
        }
        physical_row["reverse_positive"] = reverse_validation["Return"] > 0
        physical_row["reverse_beats_normal"] = reverse_validation["Return"] > normal_validation["Return"]
        physical_row["reverse_validation_positive"] = bool(
            discovery_candidate
            and reverse_validation["Return"] > 0
            and reverse_validation["Sharpe"] > 0
            and reverse_validation["Signed_BE_bps"] > 0
            and reverse_validation["Return"] > normal_validation["Return"]
        )
        atomic_json(physical_row, checkpoint_path)
        members = logical.loc[
            logical.semantic_group_id.eq(item.semantic_group_id)
            & logical.timeframe.eq(item.timeframe),
            "strategy_id",
        ].drop_duplicates()
        for strategy_id in members:
            rows.append({"strategy_id": strategy_id, **physical_row})
            candidates.append(
                {
                    "strategy_id": strategy_id,
                    "semantic_group_id": item.semantic_group_id,
                    "symbol": args.symbol,
                    "timeframe": item.timeframe,
                    **base,
                    "Return_NORMAL_DISCOVERY": normal_discovery["Return"],
                    "Sharpe_NORMAL_DISCOVERY": normal_discovery["Sharpe"],
                    "Signed_BE_NORMAL_DISCOVERY": normal_discovery["Signed_BE_bps"],
                }
            )
        path_key = "case_" + hashlib.sha256(
            f"{item.semantic_group_id}|{item.timeframe}".encode()
        ).hexdigest()[:20]
        path_root = output / "paths" / path_key
        path_root.mkdir(parents=True, exist_ok=True)
        for window, mask in (("DISCOVERY", discovery), ("VALIDATION", validation)):
            review_path(normal, mask).to_parquet(
                path_root / f"NORMAL__{window}.parquet", index=False, compression="zstd"
            )
            review_path(reverse, mask).to_parquet(
                path_root / f"STRICT_REVERSE__{window}.parquet", index=False, compression="zstd"
            )
        del normal, reverse
    frame = pd.DataFrame(rows)
    atomic_csv(frame, output / "reverse_case_comparison.csv")
    atomic_csv(pd.DataFrame(candidates), output / "reverse_candidate_manifest.csv")
    atomic_json(
        {
            "status": "PASSED",
            "symbol": args.symbol,
            "physical_selected_cases": len(physical),
            "logical_selected_cases": len(logical),
            "physical_negative_discovery_cases": int(frame[["semantic_group_id", "timeframe"]].drop_duplicates().shape[0]) if len(frame) else 0,
            "logical_negative_discovery_cases": len(frame),
            "validated_reverse_cases": int(frame.reverse_validation_positive.sum()) if len(frame) else 0,
            "strict_reverse_definition": "target_position = -normal_target_position",
            "pnl_reused_by_sign_negation": False,
            "validation_type": "INTERNAL_TEMPORAL_VALIDATION",
        },
        output / "run_summary.json",
    )
    print((output / "run_summary.json").read_text())


if __name__ == "__main__":
    main()
