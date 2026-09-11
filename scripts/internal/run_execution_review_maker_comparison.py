#!/usr/bin/env python3
"""Run frozen FIRST_TICK vs P1 L1 maker comparison for selected cases only."""

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

from scripts.internal.run_l1_maker_pilot import (  # noqa: E402
    END,
    START,
    UNIT_QTY,
    eligible_events,
    finalize_runner,
    first_tick_path,
    load_day,
    minute_snapshots,
)
from scripts.internal.run_l1_maker_policy_study import (  # noqa: E402
    FILL_PROBABILITY,
    PolicyRunner,
    append_path,
    enrich_metric,
    quote_tuple,
)
from strategy_framework.backends.nautilus_maker import NativeMakerHarness  # noqa: E402
from strategy_framework.execution.maker_policy import MakerLifecyclePolicy  # noqa: E402


def make_review_instrument(symbol: str, exchange_info: dict):
    """Build the March instrument without changing the validated pilot helper."""
    from decimal import Decimal
    from nautilus_trader.model.enums import AssetClass
    from nautilus_trader.model.identifiers import InstrumentId, Symbol, Venue
    from nautilus_trader.model.instruments import CryptoPerpetual
    from nautilus_trader.model.objects import Currency, Money, Price, Quantity
    from scripts.internal.run_l1_maker_pilot import (
        HISTORICAL_PRICE_INCREMENT, HISTORICAL_SIZE_INCREMENT, PRICE_PRECISION, SIZE_PRECISION,
    )

    raw = next(row for row in exchange_info["symbols"] if row["symbol"] == symbol)
    filters = {row["filterType"]: row for row in raw["filters"]}
    price = filters["PRICE_FILTER"]
    qty = filters["LOT_SIZE"]
    notional = filters.get("MIN_NOTIONAL", {})
    usdt = Currency.from_str("USDT")
    return CryptoPerpetual(
        instrument_id=InstrumentId(symbol=Symbol(f"{symbol}-PERP"), venue=Venue("BINANCE")),
        raw_symbol=Symbol(symbol), base_currency=Currency.from_str(raw["baseAsset"]),
        quote_currency=usdt, settlement_currency=usdt, is_inverse=False,
        price_precision=PRICE_PRECISION.get(symbol, int(raw["pricePrecision"])),
        price_increment=Price.from_str(HISTORICAL_PRICE_INCREMENT.get(symbol, price["tickSize"])),
        size_precision=SIZE_PRECISION.get(symbol, int(raw["quantityPrecision"])),
        size_increment=Quantity.from_str(HISTORICAL_SIZE_INCREMENT.get(symbol, qty["stepSize"])),
        max_quantity=Quantity.from_str(qty["maxQty"]), min_quantity=Quantity.from_str(qty["minQty"]),
        max_notional=None, min_notional=Money(float(notional.get("notional", 0)), usdt),
        max_price=Price.from_str(price["maxPrice"]), min_price=Price.from_str(price["minPrice"]),
        margin_init=Decimal("1.00"), margin_maint=Decimal("0.35"),
        maker_fee=Decimal("0"), taker_fee=Decimal("0"),
        ts_event=int(raw.get("onboardDate", 0))*1_000_000,
        ts_init=int(raw.get("onboardDate", 0))*1_000_000,
    )


SYMBOLS = (
    "XRPUSDT", "DOGEUSDT", "SUIUSDT", "BNBUSDT", "ETHUSDT",
    "BTCUSDT", "1000PEPEUSDT", "SOLUSDT", "ADAUSDT",
)
WORK = Path("outputs/baseline_evaluation/execution_method_and_reverse_review")
PILOT = Path("outputs/baseline_evaluation/maker_execution_research/l1_pilot")


def atomic_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    frame.to_csv(temporary, index=False)
    os.replace(temporary, path)


def atomic_json(payload: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, allow_nan=False, default=str) + "\n")
    os.replace(temporary, path)


def source_root(repo: Path, work: Path, pilot: Path, symbol: str) -> Path:
    candidate = work / "maker_data"
    probe = candidate / f"l1_quotes/symbol={symbol}/date=2024-03-01/part.parquet"
    return candidate if probe.exists() else pilot


def seed_for(case_key: str) -> int:
    return 10_000 + int(hashlib.sha256(case_key.encode()).hexdigest()[:8], 16) % 2_000_000_000


def main() -> None:  # noqa: C901
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=ROOT)
    parser.add_argument("--work", type=Path)
    parser.add_argument("--pilot", type=Path)
    parser.add_argument("--symbols", nargs="*", choices=SYMBOLS, default=list(SYMBOLS))
    parser.add_argument("--result-subdir")
    parser.add_argument("--case-selection", type=Path)
    parser.add_argument("--reverse-targets", action="store_true")
    args = parser.parse_args()
    repo = args.repo.resolve()
    work = (args.work or repo / WORK).resolve()
    pilot = (args.pilot or repo / PILOT).resolve()
    signals = work / "maker_signals"
    result = work / "maker_comparison"
    if args.result_subdir:
        result = result / args.result_subdir
    result.mkdir(parents=True, exist_ok=True)
    exchange_info = json.loads(
        (repo / "outputs/binance_exchange_info_phase6d.json").read_text(encoding="utf-8")
    )
    mapping = pd.read_csv(signals / "maker_case_mapping.csv")
    if args.case_selection:
        requested = pd.read_csv(args.case_selection)[["symbol", "semantic_group_id", "timeframe"]].drop_duplicates()
        mapping = mapping.merge(requested, on=["symbol", "semantic_group_id", "timeframe"], how="inner", validate="one_to_one")
    metric_rows: list[dict] = []
    order_rows: list[dict] = []
    fill_rows: list[dict] = []

    for symbol in args.symbols:
        symbol_mapping = mapping[mapping.symbol.eq(symbol)].copy()
        if symbol_mapping.empty:
            continue
        data_root = source_root(repo, work, pilot, symbol)
        targets = pd.read_parquet(signals / f"target_positions_{symbol}.parquet")
        funding = pd.read_parquet(signals / f"funding_{symbol}.parquet")
        funding_lookup = dict(
            zip(funding.event_time_ns.astype(np.int64), funding.funding_rate.astype(float), strict=True)
        )
        target_times = targets.decision_time_ns.to_numpy(np.int64)
        target_lookup_index = {int(ts): i for i, ts in enumerate(target_times)}
        instrument = make_review_instrument(symbol, exchange_info)
        runners: list[PolicyRunner] = []
        for row in symbol_mapping.itertuples(index=False):
            seed = seed_for(row.case_key)
            target_values = targets[row.case_key].to_numpy(float)
            if args.reverse_targets:
                target_values = -target_values
            runners.append(
                PolicyRunner(
                    strategy_id=row.case_key,
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
                    target=target_values,
                    random_seed=seed,
                    model_label=(
                        "STRICT_REVERSE_GTC_UNTIL_SIGNAL_INVALID"
                        if args.reverse_targets else MakerLifecyclePolicy.GTC_UNTIL_SIGNAL_INVALID.value
                    ),
                    policy=MakerLifecyclePolicy.GTC_UNTIL_SIGNAL_INVALID,
                )
            )
        previous_quote = None
        initial_mid = None
        minute_reference: list[pd.DataFrame] = []
        for day in pd.date_range(START, END - pd.Timedelta(days=1), freq="1D"):
            day_text = day.date().isoformat()
            quotes, trades = load_day(data_root, symbol, day_text)
            day_start = int(day.value)
            minute_ns = np.arange(
                day_start, day_start + 86_400_000_000_000, 60_000_000_000, dtype=np.int64
            )
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
            maker_metric, maker_path = finalize_runner(runner, pd.DataFrame(), float(initial_mid))
            maker_metric = enrich_metric(maker_metric, runner, maker_path)
            maker_metric["case_key"] = runner.strategy_id
            metric_rows.append(maker_metric)
            order_rows.extend({**row, "case_key": runner.strategy_id} for row in runner.orders)
            fill_rows.extend({**row, "case_key": runner.strategy_id, "policy": runner.policy.value} for row in runner.fills)
            path_dir = result / "paths" / symbol
            path_dir.mkdir(parents=True, exist_ok=True)
            maker_path.to_parquet(
                path_dir / f"{runner.strategy_id}__MAKER.parquet", index=False, compression="zstd"
            )
            first_metric, first_path = first_tick_path(
                runner.target, target_times, minute_reference, float(initial_mid), funding_lookup
            )
            first_metric.update(
                {
                    "strategy_id": runner.strategy_id,
                    "case_key": runner.strategy_id,
                    "symbol": symbol,
                    "execution_model": "STRICT_REVERSE_FIRST_TICK" if args.reverse_targets else "FIRST_TICK_IDEALIZED",
                    "policy": "STRICT_REVERSE_FIRST_TICK" if args.reverse_targets else "FIRST_TICK_IDEALIZED",
                }
            )
            metric_rows.append(first_metric)
            first_path.to_parquet(
                path_dir / f"{runner.strategy_id}__FIRST_TICK.parquet", index=False, compression="zstd"
            )
        atomic_csv(pd.DataFrame(metric_rows), result / "execution_metrics.partial.csv")
        atomic_csv(pd.DataFrame(order_rows), result / "maker_orders.partial.csv")
        atomic_csv(pd.DataFrame(fill_rows), result / "maker_fills.partial.csv")
        atomic_json(
            {"status": "RUNNING", "completed_symbols": sorted(set(row["symbol"] for row in metric_rows))},
            result / "run_summary.json",
        )

    atomic_csv(pd.DataFrame(metric_rows), result / "execution_metrics.csv")
    atomic_csv(pd.DataFrame(order_rows), result / "maker_orders.csv")
    atomic_csv(pd.DataFrame(fill_rows), result / "maker_fills.csv")
    expected_physical = len(mapping[mapping.symbol.isin(args.symbols)])
    atomic_json(
        {
            "status": "PASSED",
            "symbols": list(args.symbols),
            "physical_selected_cases": expected_physical,
            "execution_metric_rows": len(metric_rows),
            "maker_policy": MakerLifecyclePolicy.GTC_UNTIL_SIGNAL_INVALID.value,
            "maker_model": "L1_BBO_MAKER",
            "strict_reverse_targets": args.reverse_targets,
            "fill_probability": FILL_PROBABILITY,
            "post_only": True,
            "queue_position": False,
            "taker_fallback": False,
            "maker_selected_new_cases": 0,
        },
        result / "run_summary.json",
    )
    print((result / "run_summary.json").read_text())


if __name__ == "__main__":
    main()
