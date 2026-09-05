#!/usr/bin/env python3
"""Run frozen P1/P2 pure-maker lifecycle policies for the 18-case L1 pilot."""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.internal.run_l1_maker_pilot import (  # noqa: E402
    END,
    HISTORICAL_PRICE_INCREMENT,
    MAKER_FEE_RATE,
    OUTPUT as PILOT_OUTPUT,
    PLAN,
    START,
    SYMBOLS,
    UNIT_QTY,
    Runner,
    accepted_order_quantity,
    atomic_csv,
    atomic_json,
    eligible_events,
    finalize_runner,
    load_day,
    make_instrument,
    minute_snapshots,
)
from strategy_framework.backends.nautilus_maker import NativeMakerHarness  # noqa: E402
from strategy_framework.execution.maker_policy import MakerLifecyclePolicy  # noqa: E402
from strategy_framework.execution.maker_policy import PureMakerLifecycleState  # noqa: E402


OUTPUT = Path("outputs/baseline_evaluation/maker_execution_research/l1_policy_study")
POLICIES = (
    MakerLifecyclePolicy.GTC_UNTIL_SIGNAL_INVALID,
    MakerLifecyclePolicy.PASSIVE_CANCEL_REQUOTE_15S,
)
FILL_PROBABILITY = 0.5
REQUOTE_NS = 15_000_000_000


@dataclass
class PolicyRunner(Runner):
    policy: MakerLifecyclePolicy = MakerLifecyclePolicy.GTC_UNTIL_SIGNAL_INVALID
    state: PureMakerLifecycleState = field(default_factory=PureMakerLifecycleState)
    requote_count: int = 0
    stale_order_cancellations: int = 0
    reversal_before_fill_count: int = 0
    target_changes_before_full_fill: int = 0
    target_activation_ns: int = 0

    def apply_new_fills(self, before: int, decision_ns: int) -> None:
        from nautilus_trader.model.enums import LiquiditySide
        from nautilus_trader.model.events import OrderFilled

        native_fills = [
            event for event in self.harness.messages[before:] if isinstance(event, OrderFilled)
        ]
        if any(event.liquidity_side != LiquiditySide.MAKER for event in native_fills):
            raise ValueError("pure-maker policy produced a non-maker fill")
        old_count = len(self.fills)
        super().apply_new_fills(before, decision_ns)
        for row in self.fills[old_count:]:
            row["liquidity_side"] = "MAKER"
        if self.order_meta is not None and len(self.fills) > old_count:
            new_fills = self.fills[old_count:]
            self.order_meta.setdefault("first_fill_timestamp_ns", new_fills[0]["fill_timestamp_ns"])
            self.order_meta["last_fill_timestamp_ns"] = new_fills[-1]["fill_timestamp_ns"]

    def settle_if_closed(self) -> None:
        if self.order is None or self.order.is_open:
            return
        self.finalize_order()
        self.order = None
        self.order_meta = None

    def cancel_active(self, timestamp_ns: int, reason: str) -> None:
        if self.order is None or not self.order.is_open:
            self.settle_if_closed()
            self.state.cancel_remainder()
            return
        filled_before = float(str(self.order.filled_qty))
        self.harness.cancel(self.order)
        self.state.cancel_remainder()
        if self.order_meta is not None:
            self.order_meta["cancel_timestamp_ns"] = timestamp_ns
            self.order_meta["cancel_reason"] = reason
        self.finalize_order()
        if reason in {"SIGNAL_INVALID", "SIGNAL_REVERSAL", "TARGET_CHANGE"}:
            self.stale_order_cancellations += 1
            self.target_changes_before_full_fill += 1
        if reason == "SIGNAL_REVERSAL" and filled_before <= 1e-12:
            self.reversal_before_fill_count += 1
        if reason == "REQUOTE_15S":
            self.requote_count += 1
        self.order = None
        self.order_meta = None

    def submit(self, timestamp_ns: int, quote: tuple, target_activation_ns: int) -> None:
        delta = self.state.required_delta
        if abs(delta) <= 1e-12:
            return
        self.order_count += 1
        side = "BUY" if delta > 0 else "SELL"
        limit = float(quote[1] if side == "BUY" else quote[3])
        requested = accepted_order_quantity(self.harness.instrument, abs(delta) * UNIT_QTY)
        if requested == 0.0:
            self.state.align_resting_quantity(0.0)
            self.missed_signals += 1
            return
        self.harness.clock.set_time(timestamp_ns)
        before = len(self.harness.messages)
        self.order = self.harness.limit(
            side=side,
            price=limit,
            quantity=requested,
            post_only=True,
            client_order_id=f"{self.policy.value[:3]}-{self.order_count}",
        )
        accepted = float(str(self.order.quantity))
        self.state.align_resting_quantity(
            (accepted if side == "BUY" else -accepted) if self.order.is_open else 0.0
        )
        from nautilus_trader.model.events import OrderRejected

        rejected = any(isinstance(event, OrderRejected) for event in self.harness.messages[before:])
        self.order_meta = {
            "strategy_id": self.strategy_id,
            "symbol": self.symbol,
            "policy": self.policy.value,
            "fill_probability": self.probability,
            "decision_timestamp_ns": target_activation_ns,
            "submit_timestamp_ns": timestamp_ns,
            "client_order_id": str(self.order.client_order_id),
            "side": side,
            "limit_price": limit,
            "contemporaneous_bid": float(quote[1]),
            "contemporaneous_ask": float(quote[3]),
            "requested_quantity": accepted,
            "target_position": self.state.desired_target,
            "actual_position_before": self.state.actual_position,
            "post_only_rejected": rejected,
            "cancel_reason": "",
        }
        self.orders.append(self.order_meta)
        self.settle_if_closed()

    def process_snapshot(self, quote: tuple, timestamp_ns: int) -> None:
        submit_ns = (
            int(self.order_meta["submit_timestamp_ns"])
            if self.order_meta is not None
            else timestamp_ns
        )
        self.process_quote(quote, submit_ns)
        self.settle_if_closed()

    def on_decision(self, desired: float, timestamp_ns: int, quote: tuple) -> None:
        prior = self.state.desired_target
        changed = not math.isclose(float(desired), prior, abs_tol=1e-12)
        if changed:
            self.target_activation_ns = timestamp_ns
        if self.policy == MakerLifecyclePolicy.PASSIVE_CANCEL_REQUOTE_15S:
            if self.order is not None and self.order.is_open:
                if changed:
                    reason = "SIGNAL_REVERSAL" if prior * desired < 0 else "SIGNAL_INVALID" if desired == 0 else "TARGET_CHANGE"
                else:
                    reason = "REQUOTE_15S"
                self.cancel_active(timestamp_ns, reason)
            self.state.set_target(desired)
            self.process_snapshot(quote, timestamp_ns)
            self.submit(timestamp_ns, quote, self.target_activation_ns or timestamp_ns)
            return

        if changed:
            if self.order is not None and self.order.is_open:
                reason = "SIGNAL_REVERSAL" if prior * desired < 0 else "SIGNAL_INVALID" if desired == 0 else "TARGET_CHANGE"
                self.cancel_active(timestamp_ns, reason)
            else:
                self.state.cancel_remainder()
            self.state.set_target(desired)
            self.process_snapshot(quote, timestamp_ns)
            self.submit(timestamp_ns, quote, self.target_activation_ns or timestamp_ns)
        elif self.order is None or not self.order.is_open:
            self.settle_if_closed()
            self.process_snapshot(quote, timestamp_ns)
            self.submit(timestamp_ns, quote, self.target_activation_ns or timestamp_ns)
        elif int(quote[5]) == timestamp_ns:
            self.process_snapshot(quote, timestamp_ns)

    def on_requote(self, timestamp_ns: int, quote: tuple, target_activation_ns: int) -> None:
        if self.policy != MakerLifecyclePolicy.PASSIVE_CANCEL_REQUOTE_15S:
            return
        if self.order is not None and self.order.is_open:
            self.cancel_active(timestamp_ns, "REQUOTE_15S")
        self.process_snapshot(quote, timestamp_ns)
        self.submit(timestamp_ns, quote, self.target_activation_ns or target_activation_ns)


def quote_tuple(frame: pd.DataFrame, index: int) -> tuple:
    return (
        int(frame.update_id.iat[index]),
        float(frame.bid_price.iat[index]),
        float(frame.bid_size.iat[index]),
        float(frame.ask_price.iat[index]),
        float(frame.ask_size.iat[index]),
        int(frame.ts_event_ns.iat[index]),
        int(frame.ts_init_ns.iat[index]),
    )


def append_path(runner: PolicyRunner, timestamp_ns: int, mid: float, bid: float, ask: float, capital: float) -> None:
    runner.path.append(
        {
            "timestamp_ns": timestamp_ns,
            "mid": mid,
            "bid": bid,
            "ask": ask,
            "target_position": runner.state.desired_target,
            "actual_position": runner.state.actual_position,
            "target_error": runner.state.target_error,
            "cumulative_return_gross": (runner.cash_gross + runner.state.actual_position * UNIT_QTY * mid) / capital,
            "cumulative_return_standard_fee": (runner.cash_fee + runner.state.actual_position * UNIT_QTY * mid) / capital,
            "cumulative_turnover": runner.turnover_notional / capital,
        }
    )


def enrich_metric(metric: dict[str, Any], runner: PolicyRunner, path: pd.DataFrame) -> dict[str, Any]:
    orders = pd.DataFrame(runner.orders)
    fills = pd.DataFrame(runner.fills)
    nonzero_target = path.target_position.abs().gt(1e-12)
    at_target = path.target_error.abs().le(1e-12)
    zero_actual = path.actual_position.abs().le(1e-12)
    partial = nonzero_target & ~at_target & ~zero_actual
    zero_when_desired = nonzero_target & zero_actual
    first_delay = []
    full_delay = []
    if len(orders):
        first_delay = (
            (orders.first_fill_timestamp_ns - orders.submit_timestamp_ns) / 1_000_000
            if "first_fill_timestamp_ns" in orders else pd.Series(dtype=float)
        )
        fully = orders.terminal_status.eq("FILLED")
        full_delay = (
            (orders.loc[fully, "last_fill_timestamp_ns"] - orders.loc[fully, "submit_timestamp_ns"]) / 1_000_000
            if "last_fill_timestamp_ns" in orders else pd.Series(dtype=float)
        )
    metric.update(
        {
            "policy": runner.policy.value,
            "requote_count": runner.requote_count,
            "stale_order_cancellations": runner.stale_order_cancellations,
            "reversal_before_fill_count": runner.reversal_before_fill_count,
            "target_changes_before_full_fill": runner.target_changes_before_full_fill,
            "median_time_to_full_fill_ms": float(pd.Series(full_delay).median()) if len(full_delay) else math.nan,
            "p75_time_to_first_fill_ms": float(pd.Series(first_delay).quantile(.75)) if len(first_delay) else math.nan,
            "p90_time_to_first_fill_ms": float(pd.Series(first_delay).quantile(.90)) if len(first_delay) else math.nan,
            "percent_time_partially_at_target": float(partial.mean()),
            "percent_time_zero_when_nonzero_target": float(zero_when_desired.mean()),
        }
    )
    return metric


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=ROOT)
    parser.add_argument("--pilot-output", type=Path, default=ROOT / PILOT_OUTPUT)
    parser.add_argument("--output", type=Path, default=ROOT / OUTPUT)
    parser.add_argument("--symbols", nargs="*", choices=SYMBOLS, default=list(SYMBOLS))
    parser.add_argument("--result-subdir")
    parser.add_argument("--strategy-limit", type=int)
    parser.add_argument("--start", default="2024-03-01")
    parser.add_argument("--end-exclusive", default="2024-03-31")
    args = parser.parse_args()
    repo = args.repo.resolve()
    pilot = args.pilot_output.resolve()
    output = args.output.resolve()
    result = output / args.result_subdir if args.result_subdir else output
    result.mkdir(parents=True, exist_ok=True)
    start = pd.Timestamp(args.start, tz="UTC")
    end = pd.Timestamp(args.end_exclusive, tz="UTC")
    if start < START or end > END or start >= end:
        raise ValueError("requested interval is outside the frozen pilot window")
    plan = pd.read_csv(repo / PLAN)
    strategies = plan.strategy_id.drop_duplicates().tolist()
    if len(plan) != 18 or len(strategies) != 6:
        raise ValueError("frozen pilot scope changed")
    if args.strategy_limit:
        strategies = strategies[: args.strategy_limit]
    exchange_info = json.loads(
        (repo / "outputs/binance_exchange_info_phase6d.json").read_text(encoding="utf-8")
    )
    metric_rows: list[dict[str, Any]] = []
    order_rows: list[dict[str, Any]] = []
    fill_rows: list[dict[str, Any]] = []

    for symbol in args.symbols:
        targets = pd.read_parquet(pilot / f"target_positions_{symbol}.parquet")
        funding = pd.read_parquet(pilot / f"funding_{symbol}.parquet")
        funding_lookup = dict(zip(funding.event_time_ns.astype(np.int64), funding.funding_rate.astype(float), strict=True))
        target_times = targets.decision_time_ns.to_numpy(np.int64)
        target_lookup_index = {int(ts): i for i, ts in enumerate(target_times)}
        instrument = make_instrument(symbol, exchange_info, 0.0)
        runners: list[PolicyRunner] = []
        for strategy_index, strategy in enumerate(strategies):
            for policy in POLICIES:
                seed = 10_000 + strategy_index * 10 + 1
                runners.append(
                    PolicyRunner(
                        strategy_id=strategy,
                        symbol=symbol,
                        probability=FILL_PROBABILITY,
                        harness=NativeMakerHarness(
                            instrument=instrument,
                            liquidity_consumption=True,
                            queue_position=False,
                            fill_probability=FILL_PROBABILITY,
                            seed=seed,
                            maker_fee_rate=0.0,
                        ),
                        target=targets[strategy].to_numpy(float),
                        random_seed=seed,
                        model_label=policy.value,
                        policy=policy,
                    )
                )
        previous_quote = None
        initial_mid = None
        minute_reference: list[pd.DataFrame] = []
        for day in pd.date_range(start, end - pd.Timedelta(days=1), freq="1D"):
            day_text = day.date().isoformat()
            quotes, trades = load_day(pilot, symbol, day_text)
            day_start = int(day.value)
            minute_ns = np.arange(day_start, day_start + 86_400_000_000_000, 60_000_000_000, dtype=np.int64)
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
                raise ValueError(f"{symbol} {day_text}: missing same-day first trade")
            minute_reference.append(
                pd.DataFrame(
                    {
                        "timestamp_ns": minute_ns,
                        "mid": snapshots.mid.to_numpy(float),
                        "first_trade_price": trades.price.to_numpy(float)[first_trade_indexes],
                    }
                )
            )
            fifteen_ns = np.arange(day_start, day_start + 86_400_000_000_000, REQUOTE_NS, dtype=np.int64)
            fifteen_indexes = np.searchsorted(snapshots_source.ts_event_ns.to_numpy(np.int64), fifteen_ns, side="right") - 1
            if np.any(fifteen_indexes < 0):
                raise ValueError("missing BBO at requote boundary")
            for local_index, timestamp in enumerate(minute_ns):
                target_index = target_lookup_index[int(timestamp)]
                minute_quote = quote_tuple(snapshots_source, int(qindexes[local_index]))
                funding_rate = funding_lookup.get(int(timestamp), 0.0)
                if funding_rate:
                    funding_mid = float(snapshots.mid.iloc[local_index])
                    for runner in runners:
                        funding_cash = runner.state.actual_position * UNIT_QTY * funding_mid * funding_rate
                        runner.cash_gross -= funding_cash
                        runner.cash_fee -= funding_cash
                for runner in runners:
                    runner.on_decision(float(runner.target[target_index]), int(timestamp), minute_quote)
                for segment in range(4):
                    segment_start = int(timestamp + segment * REQUOTE_NS)
                    segment_end = int(segment_start + REQUOTE_NS)
                    if segment:
                        fifteen_index = (local_index * 4) + segment
                        rq_quote = quote_tuple(snapshots_source, int(fifteen_indexes[fifteen_index]))
                        for runner in runners:
                            runner.on_requote(segment_start, rq_quote, int(timestamp))
                    q0 = int(np.searchsorted(qts, segment_start, side="right"))
                    q1 = int(np.searchsorted(qts, segment_end, side="left"))
                    t0 = int(np.searchsorted(tts, segment_start, side="right"))
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
                for runner in runners:
                    append_path(
                        runner,
                        int(timestamp),
                        mid,
                        float(snapshots.bid.iloc[local_index]),
                        float(snapshots.ask.iloc[local_index]),
                        capital,
                    )
            previous_quote = quotes.iloc[[-1]].copy()
        reference = pd.concat(minute_reference, ignore_index=True)
        reference.to_parquet(result / f"minute_reference_{symbol}.parquet", index=False, compression="zstd")
        for runner in runners:
            metric, path = finalize_runner(runner, pd.DataFrame(), float(initial_mid))
            metric_rows.append(enrich_metric(metric, runner, path))
            order_rows.extend(runner.orders)
            fill_rows.extend({**row, "policy": runner.policy.value} for row in runner.fills)
            destination = result / "paths" / f"{runner.strategy_id}__{symbol}__{runner.policy.value}.parquet"
            destination.parent.mkdir(parents=True, exist_ok=True)
            path.to_parquet(destination, index=False, compression="zstd")

    atomic_csv(pd.DataFrame(metric_rows), result / "policy_metrics.csv")
    atomic_csv(pd.DataFrame(order_rows), result / "policy_orders.csv")
    atomic_csv(pd.DataFrame(fill_rows), result / "policy_fills.csv")
    atomic_json(
        {
            "status": "PASSED",
            "symbols": list(args.symbols),
            "policies": [policy.value for policy in POLICIES],
            "fill_probability": FILL_PROBABILITY,
            "requote_interval_seconds": 15,
            "cases": len(metric_rows),
            "period": {"start": str(start), "end_exclusive": str(end)},
            "queue_position": False,
            "post_only": True,
            "taker_fallback": False,
        },
        result / "run_summary.json",
    )


if __name__ == "__main__":
    main()
