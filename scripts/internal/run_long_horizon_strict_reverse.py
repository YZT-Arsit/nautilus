#!/usr/bin/env python3
"""Run actual-accounting STRICT_REVERSE for the frozen canonical-5y manifest."""

from __future__ import annotations

import argparse
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

from results.trade_episode import build_de_risk_episodes  # noqa: E402
from scripts.internal.run_boss_multitimeframe_tick_screen import (  # noqa: E402
    NOTIONAL,
    atomic_json,
    drawdown,
    load_symbol,
    long_horizon_metrics,
    persistence_metrics,
    review_sample_indices,
    tick_wait_metrics,
)
from scripts.internal.run_constant_notional_overlay import calculate_overlay  # noqa: E402


def reconstruct_normal_position(review_path: Path, event_time_ns: np.ndarray) -> np.ndarray:
    """Expand the lossless transition sample back to the complete minute path."""
    review = pd.read_parquet(review_path, columns=["event_time_ns", "executed_position"])
    review = review.sort_values("event_time_ns").drop_duplicates("event_time_ns", keep="last")
    sample_time = review.event_time_ns.to_numpy(dtype=np.int64, copy=False)
    sample_position = review.executed_position.to_numpy(dtype=np.float64, copy=False)
    lookup = np.searchsorted(sample_time, event_time_ns, side="right") - 1
    if len(sample_time) == 0 or np.any(lookup < 0):
        raise ValueError(f"review transition sample cannot cover full path: {review_path}")
    position = sample_position[lookup]
    if np.count_nonzero(sample_position[1:] != sample_position[:-1]) != np.count_nonzero(position[1:] != position[:-1]):
        raise ValueError(f"lossy position transition sample: {review_path}")
    return position


def run_reverse_from_frozen_normal(
    *, row: object, members: list[str], normal_summary: dict, normal_review_path: Path,
    bars: list, funding: pd.DataFrame, tick_prices: np.ndarray, waits: np.ndarray,
) -> tuple[dict, pd.DataFrame]:
    """Negate the frozen target path, then independently rerun execution accounting."""
    event_time = np.fromiter((bar.event_time_ns for bar in bars), dtype=np.int64)
    close = np.fromiter((bar.close for bar in bars), dtype=np.float64)
    normal_direction = reconstruct_normal_position(normal_review_path, event_time)
    reverse_direction = -normal_direction
    mismatch = int(np.count_nonzero(reverse_direction + normal_direction))
    if mismatch:
        raise ValueError(f"STRICT_REVERSE target mismatch count={mismatch}")
    normal_persistence = persistence_metrics(normal_direction)
    for field in ("position_change_count", "sign_switch_count", "long_fraction", "short_fraction", "flat_fraction"):
        if field in normal_summary and not np.isclose(float(normal_summary[field]), float(normal_persistence[field])):
            raise ValueError(f"reconstructed NORMAL position mismatch for {field}")

    result, accounting = calculate_overlay(
        pd.DataFrame({"event_time_ns": event_time, "close": close, "position": reverse_direction}),
        funding, tick_prices, notional_usdt=NOTIONAL, slippage_bps=0.0,
        vip9_fee_bps=0.0, vip0_fee_bps=5.0, position_policy="strict_constant_notional",
    )
    episode_rows, episode_summary = build_de_risk_episodes(
        event_time_ns=result.event_time_ns, executed_position=result.direction,
        turnover_increment=result.turnover, gross_return_increment=result.total_return,
        strategy=str(row.representative_strategy_id), symbol=str(row.symbol), granularity=str(row.timeframe),
        lag="tick_no_added_lag", premium_mode="included", variant="strict_reverse",
    )
    increments = result.total_return.to_numpy(dtype=np.float64, copy=False)
    turnover_increments = result.turnover.to_numpy(dtype=np.float64, copy=False)
    horizon = long_horizon_metrics(event_time, increments, turnover_increments)
    total_return = float(accounting["total_simple_return_fee0"])
    turnover = float(accounting["total_turnover_x"])
    summary = {
        "status": "COMPLETED",
        "representative_strategy_id": str(row.representative_strategy_id),
        "member_strategy_ids": ";".join(members),
        "semantic_execution_hash": str(row.semantic_group_id),
        "semantic_group_id": str(row.semantic_group_id),
        "source_origin": str(row.source_origin),
        "symbol": str(row.symbol), "timeframe": str(row.timeframe),
        "Return_fee0": total_return,
        "Return_no_premium": float(accounting["trading_simple_return"]),
        "Return_5bp": total_return - turnover * 5.0 / 10_000.0,
        "Turnover_raw": turnover, "Turnover_pct": turnover * 100.0,
        "BE_bps": float(accounting["breakeven_fee_bps"]), "MDD": drawdown(increments),
        "episode_count": len(episode_rows),
        "open_episode_count": int(episode_summary.get("open_episode_count", 0)),
        "decision_count": int(normal_summary.get("decision_count", 0)),
        "target_position_change_count": int(normal_summary.get("target_position_change_count", 0)),
        "tick_execution_count": int(normal_summary.get("tick_execution_count", 0)),
        "no_added_lag": True, "tick_source": "official_binance_raw_trades", "funding": "included",
        "max_boundary_notional_error_usdt": float(accounting["max_boundary_notional_error_usdt"]),
        "accounting_identity_max_error": float(accounting["accounting_identity_max_error"]),
        "review_sample_version": 2, "direction_multiplier": -1,
        "execution_variant": "STRICT_REVERSE", "strict_reverse_target_mismatch_count": mismatch,
        "first_tick_lookup_predecision_count": 0,
        "evidence_class": "LONG_HORIZON_EXPLORATORY_REVERSE",
        **persistence_metrics(reverse_direction),
        **tick_wait_metrics(event_time, waits, str(row.timeframe)),
        **horizon,
    }
    cumulative = np.cumsum(increments, dtype=np.float64)
    equity = 1.0 + cumulative
    peak = np.maximum.accumulate(np.r_[1.0, equity])[1:]
    dd = np.divide(equity, peak, out=np.zeros_like(equity), where=peak > 0) - 1.0
    review = pd.DataFrame({
        "event_time_ns": result.event_time_ns,
        "cumulative_return_with_premium": cumulative,
        "cumulative_return_without_premium": np.cumsum(result.trading_return.to_numpy(float)),
        "cumulative_turnover": np.cumsum(turnover_increments),
        "executed_position": result.direction, "drawdown": dd,
    })
    sample = review_sample_indices(result.direction, dd)
    return summary, review.iloc[sample].reset_index(drop=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--market-root", type=Path, required=True)
    parser.add_argument("--shard-count", type=int, default=1)
    parser.add_argument("--shard-index", type=int, default=0)
    args = parser.parse_args()
    if args.shard_count < 1 or not 0 <= args.shard_index < args.shard_count:
        raise ValueError("invalid shard index/count")

    manifest = pd.read_csv(args.root / "selection/long_horizon_negative_reverse_candidates.csv")
    physical = manifest.sort_values(["semantic_group_id", "timeframe", "symbol", "strategy_id"]).drop_duplicates(
        ["semantic_group_id", "timeframe", "symbol"]
    ).reset_index(drop=True)
    physical = physical.iloc[args.shard_index :: args.shard_count]
    start, end_exclusive = "2021-07-01", "2026-07-01"
    end_inclusive = (date.fromisoformat(end_exclusive) - timedelta(days=1)).isoformat()
    bars, funding, _, tick_prices, waits = load_symbol(
        args.market_root, args.root / "tick_execution_index", "BTCUSDT", start, end_inclusive
    )
    complete = failures = 0
    progress = args.root / f"reverse_progress_shard_{args.shard_index}_of_{args.shard_count}.json"
    for row in physical.itertuples(index=False):
        safe_group = str(row.semantic_group_id).replace(":", "__")
        case_root = args.root / "reverse_cases" / f"symbol={row.symbol}" / f"timeframe={row.timeframe}" / f"semantic={safe_group}"
        result_path = case_root / "summary.json"
        review_path = case_root / "review_timeseries.parquet"
        if result_path.is_file() and review_path.is_file():
            saved = json.loads(result_path.read_text(encoding="utf-8-sig"))
            if saved.get("status") == "COMPLETED" and saved.get("review_sample_version") == 2 and saved.get("strict_reverse_target_mismatch_count") == 0 and saved.get("n_daily_observations") == 1826:
                complete += 1
                continue
        try:
            members = sorted(manifest.loc[
                manifest.semantic_group_id.eq(row.semantic_group_id)
                & manifest.timeframe.eq(row.timeframe)
                & manifest.symbol.eq(row.symbol), "strategy_id"
            ].tolist())
            normal_summary_path = Path(str(row.normal_summary_path))
            normal_summary = json.loads(normal_summary_path.read_text(encoding="utf-8-sig"))
            summary, review = run_reverse_from_frozen_normal(
                row=row, members=members, normal_summary=normal_summary,
                normal_review_path=normal_summary_path.parent / "review_timeseries.parquet",
                bars=bars, funding=funding, tick_prices=tick_prices, waits=waits,
            )
            atomic_json(result_path, summary)
            case_root.mkdir(parents=True, exist_ok=True)
            tmp = review_path.with_suffix(review_path.suffix + ".tmp")
            review.to_parquet(tmp, index=False, compression="zstd")
            os.replace(tmp, review_path)
            complete += 1
        except Exception as exc:
            failures += 1
            atomic_json(result_path, {
                "status": "FAILED", "strategy_id": row.representative_strategy_id,
                "semantic_group_id": row.semantic_group_id, "symbol": row.symbol,
                "timeframe": row.timeframe, "error": f"{type(exc).__name__}: {exc}",
            })
        atomic_json(progress, {
            "status": "RUNNING", "physical_planned": len(physical),
            "physical_completed": complete, "physical_failures": failures,
            "current_strategy": row.representative_strategy_id, "current_timeframe": row.timeframe,
        })
    atomic_json(progress, {
        "status": "PASSED" if failures == 0 else "COMPLETED_WITH_FAILURES",
        "physical_planned": len(physical), "physical_completed": complete,
        "physical_failures": failures,
    })
    return 0 if failures == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
