#!/usr/bin/env python3
"""Run real L1 maker NORMAL and STRICT_REVERSE on the longest public BTC window."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.internal.run_boss_multitimeframe_tick_screen import load_symbol  # noqa: E402
from scripts.internal.run_execution_review_maker_comparison import make_review_instrument  # noqa: E402
from scripts.internal.run_l1_maker_pilot import (  # noqa: E402
    UNIT_QTY, eligible_events, finalize_runner, first_tick_path, minute_snapshots,
)
from scripts.internal.run_l1_maker_policy_study import (  # noqa: E402
    FILL_PROBABILITY, PolicyRunner, enrich_metric, quote_tuple,
)
from strategy_framework.backends.nautilus_maker import NativeMakerHarness  # noqa: E402
from strategy_framework.execution.maker_policy import MakerLifecyclePolicy  # noqa: E402


START = pd.Timestamp("2023-05-16", tz="UTC")
END = pd.Timestamp("2024-03-31", tz="UTC")


def atomic_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    frame.to_csv(temporary, index=False)
    os.replace(temporary, path)


def atomic_json(value: object, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, default=str), encoding="utf-8")
    os.replace(temporary, path)


def case_key(group: str, timeframe: str) -> str:
    return "case_" + hashlib.sha256(f"{group}|{timeframe}".encode()).hexdigest()[:20]


def partition_lookup(manifest: pd.DataFrame, data_type: str) -> dict[str, Path]:
    frame = manifest[
        manifest.data_type.eq(data_type)
        & manifest.validation_status.eq("PASSED")
        & manifest.coverage_days.astype(int).eq(1)
    ].copy()
    frame = frame.drop_duplicates("date", keep="last")
    return {str(row.date): Path(str(row.output_path)) for row in frame.itertuples(index=False)}


def load_day(quote_paths: dict[str, Path], trade_paths: dict[str, Path], day: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    if day not in quote_paths or day not in trade_paths:
        raise FileNotFoundError(f"maker partition unavailable for {day}")
    quotes = pd.read_parquet(quote_paths[day])
    trades = pd.read_parquet(trade_paths[day])
    if quotes.empty or trades.empty:
        raise ValueError(f"empty maker partition for {day}")
    if np.any(np.diff(quotes.ts_event_ns.to_numpy(np.int64)) < 0):
        raise ValueError(f"non-monotonic QuoteTick partition {day}")
    if np.any(np.diff(trades.ts_event_ns.to_numpy(np.int64)) < 0):
        raise ValueError(f"non-monotonic TradeTick partition {day}")
    return quotes, trades


def reconstruct_interval_position(review_path: Path, event_time_ns: np.ndarray) -> np.ndarray:
    """Expand the frozen transition sample over a strict subwindow without leakage."""
    review = pd.read_parquet(review_path, columns=["event_time_ns", "executed_position"])
    review = review.sort_values("event_time_ns").drop_duplicates("event_time_ns", keep="last")
    sample_time = review.event_time_ns.to_numpy(np.int64, copy=False)
    sample_position = review.executed_position.to_numpy(float, copy=False)
    lookup = np.searchsorted(sample_time, event_time_ns, side="right") - 1
    if len(sample_time) == 0 or np.any(lookup < 0):
        raise ValueError(f"review transition sample cannot cover maker interval: {review_path}")
    position = sample_position[lookup]
    transition_times = sample_time[1:][sample_position[1:] != sample_position[:-1]]
    expected = int(np.count_nonzero((transition_times > event_time_ns[0]) & (transition_times <= event_time_ns[-1])))
    actual = int(np.count_nonzero(position[1:] != position[:-1]))
    if expected != actual:
        raise ValueError(f"lossy position transition sample within maker interval: {review_path}")
    return position


def main() -> int:  # noqa: C901
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--research-root", type=Path, required=True)
    parser.add_argument("--market-root", type=Path, required=True)
    parser.add_argument("--acquisition-manifest", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--shard-count", type=int, default=1)
    parser.add_argument("--shard-index", type=int, default=0)
    parser.add_argument("--start", default=START.date().isoformat())
    parser.add_argument("--end-exclusive", default=END.date().isoformat())
    args = parser.parse_args()
    if not 0 <= args.shard_index < args.shard_count:
        raise ValueError("invalid shard")
    start = pd.Timestamp(args.start, tz="UTC")
    end = pd.Timestamp(args.end_exclusive, tz="UTC")
    if start < START or end > END or start >= end:
        raise ValueError(f"requested maker interval must be inside [{START.date()},{END.date()})")
    selected = pd.read_csv(args.research_root / "selection/long_horizon_first_tick_selected_cases.csv")
    physical = selected.sort_values(
        ["semantic_group_id", "timeframe", "symbol", "strategy_id"]
    ).drop_duplicates(["semantic_group_id", "timeframe", "symbol"]).reset_index(drop=True)
    physical = physical.iloc[args.shard_index :: args.shard_count].copy()
    manifest = pd.read_csv(args.acquisition_manifest)
    quote_paths = partition_lookup(manifest, "bookTicker")
    trade_paths = partition_lookup(manifest, "trades")
    required_dates = [day.date().isoformat() for day in pd.date_range(start, end - pd.Timedelta(days=1), freq="1D")]
    missing = [day for day in required_dates if day not in quote_paths or day not in trade_paths]
    if missing:
        raise ValueError(f"partial maker window incomplete: {len(missing)} missing days, first={missing[:3]}")

    bars, funding, _, _, _ = load_symbol(
        args.market_root, args.research_root / "tick_execution_index", "BTCUSDT",
        start.date().isoformat(), (end - pd.Timedelta(days=1)).date().isoformat(),
    )
    target_times = np.fromiter((bar.event_time_ns for bar in bars), dtype=np.int64)
    funding_lookup = dict(zip(
        funding.event_time_ns.astype(np.int64), funding.funding_rate.astype(float), strict=True
    ))
    exchange_info = json.loads(
        (args.repo / "outputs/binance_exchange_info_phase6d.json").read_text(encoding="utf-8")
    )
    instrument = make_review_instrument("BTCUSDT", exchange_info)
    runners: list[PolicyRunner] = []
    mapping_rows: list[dict] = []
    for row in physical.itertuples(index=False):
        key = case_key(str(row.semantic_group_id), str(row.timeframe))
        normal_review = Path(str(row.normal_summary_path)).parent / "review_timeseries.parquet"
        normal_target = reconstruct_interval_position(normal_review, target_times)
        members = sorted(selected.loc[
            selected.semantic_group_id.eq(row.semantic_group_id)
            & selected.timeframe.eq(row.timeframe)
            & selected.symbol.eq(row.symbol), "strategy_id"
        ].tolist())
        mapping_rows.append({
            "case_key": key, "semantic_group_id": row.semantic_group_id,
            "representative_strategy_id": row.representative_strategy_id,
            "member_strategy_ids": ";".join(members), "symbol": row.symbol,
            "timeframe": row.timeframe,
        })
        for variant, target in (("NORMAL", normal_target), ("STRICT_REVERSE", -normal_target)):
            seed = 10_000 + int(hashlib.sha256(f"{key}|{variant}".encode()).hexdigest()[:8], 16) % 2_000_000_000
            runners.append(PolicyRunner(
                strategy_id=f"{key}__{variant}", symbol="BTCUSDT", probability=FILL_PROBABILITY,
                harness=NativeMakerHarness(
                    instrument=instrument, liquidity_consumption=True, queue_position=False,
                    fill_probability=FILL_PROBABILITY, seed=seed, maker_fee_rate=0.0,
                ),
                target=target, random_seed=seed,
                model_label=f"L1_BBO_MAKER_{variant}",
                policy=MakerLifecyclePolicy.GTC_UNTIL_SIGNAL_INVALID,
            ))

    target_lookup_index = {int(ts): index for index, ts in enumerate(target_times)}
    runner_count = len(runners)
    point_count = len(target_times)
    actual_position = np.empty((runner_count, point_count), dtype=np.float32)
    target_error = np.empty((runner_count, point_count), dtype=np.float32)
    cumulative_return_gross = np.empty((runner_count, point_count), dtype=np.float64)
    cumulative_return_fee = np.empty((runner_count, point_count), dtype=np.float64)
    cumulative_turnover = np.empty((runner_count, point_count), dtype=np.float64)
    previous_quote = None
    initial_mid = None
    minute_reference: list[pd.DataFrame] = []
    for day_number, day in enumerate(pd.date_range(start, end - pd.Timedelta(days=1), freq="1D"), start=1):
        day_text = day.date().isoformat()
        quotes, trades = load_day(quote_paths, trade_paths, day_text)
        day_start = int(day.value)
        day_end = int((day + pd.Timedelta(days=1)).value)
        mask = (target_times >= day_start) & (target_times < day_end)
        minute_ns = target_times[mask]
        if len(minute_ns) != 1440:
            raise ValueError(f"expected 1440 decision timestamps for {day_text}, got {len(minute_ns)}")
        seed_quote = quotes.iloc[[0]].copy() if previous_quote is None else previous_quote
        if previous_quote is None:
            seed_quote.loc[:, "ts_event_ns"] = day_start
            seed_quote.loc[:, "ts_init_ns"] = day_start
        snapshots_source = pd.concat([seed_quote, quotes], ignore_index=True)
        qindexes, snapshots = minute_snapshots(snapshots_source, minute_ns)
        if initial_mid is None:
            initial_mid = float(snapshots.mid.iloc[0])
        qts = quotes.ts_event_ns.to_numpy(np.int64, copy=False)
        tts = trades.ts_event_ns.to_numpy(np.int64, copy=False)
        first_trade_indexes = np.searchsorted(tts, minute_ns, side="left")
        if np.any(first_trade_indexes >= len(trades)):
            raise ValueError(f"{day_text}: missing same-day first trade")
        minute_reference.append(pd.DataFrame({
            "timestamp_ns": minute_ns, "mid": snapshots.mid.to_numpy(float),
            "bid": snapshots.bid.to_numpy(float), "ask": snapshots.ask.to_numpy(float),
            "first_trade_price": trades.price.to_numpy(float)[first_trade_indexes],
        }))
        for local_index, timestamp in enumerate(minute_ns):
            target_index = target_lookup_index[int(timestamp)]
            minute_quote = quote_tuple(snapshots_source, int(qindexes[local_index]))
            funding_rate = funding_lookup.get(int(timestamp), 0.0)
            if funding_rate:
                funding_mid = float(snapshots.mid.iloc[local_index])
                for runner in runners:
                    cash = runner.state.actual_position * UNIT_QTY * funding_mid * funding_rate
                    runner.cash_gross -= cash
                    runner.cash_fee -= cash
            for runner in runners:
                runner.on_decision(float(runner.target[target_index]), int(timestamp), minute_quote)
            segment_end = int(timestamp + 60_000_000_000)
            q0 = int(np.searchsorted(qts, timestamp, side="right"))
            q1 = int(np.searchsorted(qts, segment_end, side="left"))
            t0 = int(np.searchsorted(tts, timestamp, side="right"))
            t1 = int(np.searchsorted(tts, segment_end, side="left"))
            interval_quotes = quotes.iloc[q0:q1]
            interval_trades = trades.iloc[t0:t1]
            for runner in runners:
                if runner.order is None or not runner.order.is_open:
                    continue
                submit_ns = int(runner.order_meta["submit_timestamp_ns"])
                for _, kind, event in eligible_events(runner, interval_quotes, interval_trades):
                    if kind == 0:
                        runner.process_quote(event, submit_ns)
                    else:
                        runner.process_trade(event, submit_ns)
                    runner.settle_if_closed()
                    if runner.order is None or not runner.order.is_open:
                        break
            mid = float(snapshots.mid.iloc[local_index])
            capital = float(initial_mid) * UNIT_QTY
            for runner_index, runner in enumerate(runners):
                actual_position[runner_index, target_index] = runner.state.actual_position
                target_error[runner_index, target_index] = runner.state.target_error
                cumulative_return_gross[runner_index, target_index] = (
                    runner.cash_gross + runner.state.actual_position * UNIT_QTY * mid
                ) / capital
                cumulative_return_fee[runner_index, target_index] = (
                    runner.cash_fee + runner.state.actual_position * UNIT_QTY * mid
                ) / capital
                cumulative_turnover[runner_index, target_index] = runner.turnover_notional / capital
        previous_quote = quotes.iloc[[-1]].copy()
        atomic_json({
            "status": "RUNNING", "days_completed": day_number, "days_total": len(required_dates),
            "physical_cases": len(physical), "runner_count": len(runners),
        }, args.output_root / f"progress_shard_{args.shard_index}_of_{args.shard_count}.json")

    reference = pd.concat(minute_reference, ignore_index=True)
    metric_rows: list[dict] = []
    for runner_index, runner in enumerate(runners):
        key, variant = runner.strategy_id.rsplit("__", 1)
        runner.path = pd.DataFrame({
            "timestamp_ns": target_times,
            "mid": reference.mid.to_numpy(float),
            "bid": reference.bid.to_numpy(float),
            "ask": reference.ask.to_numpy(float),
            "target_position": runner.target,
            "actual_position": actual_position[runner_index],
            "target_error": target_error[runner_index],
            "cumulative_return_gross": cumulative_return_gross[runner_index],
            "cumulative_return_standard_fee": cumulative_return_fee[runner_index],
            "cumulative_turnover": cumulative_turnover[runner_index],
        })
        maker_metric, maker_path = finalize_runner(runner, pd.DataFrame(), float(initial_mid))
        maker_metric = enrich_metric(maker_metric, runner, maker_path)
        maker_metric.update({
            "case_key": key, "variant": variant, "execution_model": f"L1_BBO_MAKER_{variant}",
            "evaluation_start": start.date().isoformat(), "evaluation_end_exclusive": end.date().isoformat(),
            "calendar_days": (end - start).days, "data_tier": "L1_BBO_MAKER",
            "post_only": True, "queue_position": False, "taker_fallback": False,
            "OrderFilled_count": len(runner.fills),
        })
        first_metric, first_path = first_tick_path(
            runner.target, target_times, minute_reference, float(initial_mid), funding_lookup
        )
        first_metric.update({
            "strategy_id": runner.strategy_id, "case_key": key, "variant": variant,
            "symbol": "BTCUSDT", "execution_model": f"SAME_WINDOW_FIRST_TICK_{variant}",
            "evaluation_start": start.date().isoformat(), "evaluation_end_exclusive": end.date().isoformat(),
            "calendar_days": (end - start).days, "data_tier": "RAW_TRADES_FIRST_TICK",
        })
        metric_rows.extend([maker_metric, first_metric])
        paths = args.output_root / "paths" / f"shard={args.shard_index}" / key
        paths.mkdir(parents=True, exist_ok=True)
        maker_path.to_parquet(paths / f"{variant}__MAKER.parquet", index=False, compression="zstd")
        first_path.to_parquet(paths / f"{variant}__FIRST_TICK.parquet", index=False, compression="zstd")
    atomic_csv(pd.DataFrame(mapping_rows), args.output_root / f"case_mapping_shard_{args.shard_index}_of_{args.shard_count}.csv")
    atomic_csv(pd.DataFrame(metric_rows), args.output_root / f"metrics_shard_{args.shard_index}_of_{args.shard_count}.csv")
    atomic_json({
        "status": "PASSED", "days_completed": len(required_dates), "days_total": len(required_dates),
        "physical_cases": len(physical), "metric_rows": len(metric_rows),
        "maker_policy": "GTC_UNTIL_SIGNAL_INVALID", "fill_probability": FILL_PROBABILITY,
    }, args.output_root / f"progress_shard_{args.shard_index}_of_{args.shard_count}.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
