#!/usr/bin/env python3
"""Run the frozen 18-case L1 BBO maker pilot from validated local partitions."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

import matplotlib as mpl

mpl.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from strategy_framework.backends.nautilus_maker import NativeMakerHarness  # noqa: E402
from strategy_framework.execution.maker_policy import NextDecisionCancelState  # noqa: E402


SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
PROBABILITIES = (1.0, 0.5, 0.25)
START = pd.Timestamp("2024-03-01", tz="UTC")
END = pd.Timestamp("2024-03-31", tz="UTC")
UNIT_QTY = 1.0
MAKER_FEE_RATE = 0.0002
PRICE_PRECISION = {"BTCUSDT": 2, "ETHUSDT": 2, "SOLUSDT": 3}
SIZE_PRECISION = {"BTCUSDT": 3, "ETHUSDT": 3, "SOLUSDT": 1}
HISTORICAL_PRICE_INCREMENT = {"BTCUSDT": "0.01", "ETHUSDT": "0.01", "SOLUSDT": "0.001"}
HISTORICAL_SIZE_INCREMENT = {"BTCUSDT": "0.001", "ETHUSDT": "0.001", "SOLUSDT": "0.1"}
OUTPUT = Path("outputs/baseline_evaluation/maker_execution_research/l1_pilot")
PLAN = Path("outputs/baseline_evaluation/maker_execution_research/data_pilot/maker_pilot_scope.csv")


def atomic_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    frame.to_csv(temporary, index=False)
    os.replace(temporary, path)


def atomic_json(payload: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, allow_nan=False, default=str) + "\n")
    os.replace(temporary, path)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def make_instrument(symbol: str, exchange_info: dict, maker_fee: float = 0.0):
    from nautilus_trader.model.enums import AssetClass
    from nautilus_trader.model.identifiers import InstrumentId, Symbol, Venue
    from nautilus_trader.model.instruments import CryptoPerpetual
    from nautilus_trader.model.objects import Currency, Money, Price, Quantity

    raw = next(row for row in exchange_info["symbols"] if row["symbol"] == symbol)
    filters = {row["filterType"]: row for row in raw["filters"]}
    price = filters["PRICE_FILTER"]
    qty = filters["LOT_SIZE"]
    notional = filters.get("MIN_NOTIONAL", {})
    base = raw["baseAsset"]
    usdt = Currency.from_str("USDT")
    return CryptoPerpetual(
        instrument_id=InstrumentId(symbol=Symbol(f"{symbol}-PERP"), venue=Venue("BINANCE")),
        raw_symbol=Symbol(symbol),
        base_currency=Currency.from_str(base),
        quote_currency=usdt,
        settlement_currency=usdt,
        is_inverse=False,
        # The pilot is a March-2024 replay.  SOL's contract precision changed
        # after this window, so current exchangeInfo cannot be used for these
        # two historical fields.  Values below are validated against the raw
        # archive decimal precision before execution.
        price_precision=PRICE_PRECISION[symbol],
        price_increment=Price.from_str(HISTORICAL_PRICE_INCREMENT[symbol]),
        size_precision=SIZE_PRECISION[symbol],
        size_increment=Quantity.from_str(HISTORICAL_SIZE_INCREMENT[symbol]),
        max_quantity=Quantity.from_str(qty["maxQty"]),
        min_quantity=Quantity.from_str(qty["minQty"]),
        max_notional=None,
        min_notional=Money(float(notional.get("notional", 0)), usdt),
        max_price=Price.from_str(price["maxPrice"]),
        min_price=Price.from_str(price["minPrice"]),
        margin_init=Decimal("1.00"),
        margin_maint=Decimal("0.35"),
        maker_fee=Decimal(str(maker_fee)),
        taker_fee=Decimal(str(maker_fee)),
        ts_event=int(raw.get("onboardDate", 0)) * 1_000_000,
        ts_init=int(raw.get("onboardDate", 0)) * 1_000_000,
    )


def daily_sharpe(index: pd.DatetimeIndex, cumulative_return: np.ndarray) -> float:
    series = pd.Series(cumulative_return, index=index)
    daily = series.resample("1D").last().diff().dropna()
    standard = daily.std(ddof=1)
    return float(daily.mean() / standard * math.sqrt(365)) if len(daily) >= 2 and standard else math.nan


def accepted_order_quantity(instrument: Any, requested: float) -> float:
    """Return the native venue-precision quantity, or zero when unorderable."""
    try:
        return float(str(instrument.make_qty(requested)))
    except ValueError as error:
        if "rounded to zero" not in str(error):
            raise
        return 0.0


@dataclass
class Runner:
    strategy_id: str
    symbol: str
    probability: float
    harness: NativeMakerHarness
    target: np.ndarray
    state: NextDecisionCancelState = field(default_factory=NextDecisionCancelState)
    order: Any | None = None
    order_meta: dict[str, Any] | None = None
    order_count: int = 0
    cash_gross: float = 0.0
    cash_fee: float = 0.0
    fills: list[dict[str, Any]] = field(default_factory=list)
    orders: list[dict[str, Any]] = field(default_factory=list)
    path: list[dict[str, Any]] = field(default_factory=list)
    missed_signals: int = 0
    random_seed: int = 0
    turnover_notional: float = 0.0
    model_label: str = "L1_BBO_MAKER"

    def apply_new_fills(self, before: int, decision_ns: int) -> None:
        from nautilus_trader.model.enums import OrderSide
        from nautilus_trader.model.events import OrderFilled

        for event in self.harness.messages[before:]:
            if not isinstance(event, OrderFilled):
                continue
            quantity = float(str(event.last_qty))
            sign = 1.0 if event.order_side == OrderSide.BUY else -1.0
            signed_target_units = sign * quantity / UNIT_QTY
            self.state.apply_fill(signed_target_units)
            price = float(event.last_px)
            signed_quantity = sign * quantity
            self.cash_gross -= signed_quantity * price
            fee = abs(quantity * price) * MAKER_FEE_RATE
            self.cash_fee -= signed_quantity * price + fee
            self.turnover_notional += abs(quantity * price)
            self.fills.append(
                {
                    "strategy_id": self.strategy_id,
                    "symbol": self.symbol,
                    "fill_probability": self.probability,
                    "client_order_id": str(event.client_order_id),
                    "fill_timestamp_ns": int(event.ts_event),
                    "fill_price": price,
                    "fill_quantity": quantity,
                    "side": "BUY" if sign > 0 else "SELL",
                    "liquidity_side": str(event.liquidity_side),
                    "commission_gross": 0.0,
                    "commission_standard_maker": fee,
                    "time_to_first_fill_ms": (int(event.ts_event) - decision_ns) / 1_000_000,
                }
            )

    def process_quote(self, row: tuple, decision_ns: int) -> None:
        before = len(self.harness.messages)
        self.harness.quote(
            bid=float(row[1]),
            ask=float(row[3]),
            bid_size=float(row[2]),
            ask_size=float(row[4]),
            ts_event=int(row[5]),
            ts_init=int(row[6]),
        )
        self.apply_new_fills(before, decision_ns)

    def process_trade(self, row: tuple, decision_ns: int) -> None:
        before = len(self.harness.messages)
        self.harness.trade(
            price=float(row[1]),
            size=float(row[2]),
            aggressor="SELLER" if bool(row[4]) else "BUYER",
            ts=int(row[3]),
            trade_id=str(int(row[0])),
        )
        self.apply_new_fills(before, decision_ns)

    def finalize_order(self) -> None:
        if self.order_meta is None or self.order is None:
            return
        self.order_meta["terminal_status"] = str(getattr(self.order.status, "name", self.order.status))
        self.order_meta["filled_quantity"] = float(str(self.order.filled_qty))
        self.order_meta["unfilled_quantity"] = float(str(self.order.leaves_qty))

    def decision(self, minute_index: int, timestamp_ns: int, quote: tuple) -> None:
        # Events at this exact boundary were already processed by the preceding
        # right-closed interval. Cancel before replaying the latest BBO snapshot
        # so a duplicate snapshot cannot create a second touch attempt.
        if self.order is not None and self.order.is_open:
            self.harness.cancel(self.order)
        self.finalize_order()
        self.order = None
        self.order_meta = None
        self.process_quote(quote, timestamp_ns)
        desired = float(self.target[minute_index])
        delta = self.state.next_decision(desired)
        if abs(delta) <= 1e-12:
            return
        self.order_count += 1
        side = "BUY" if delta > 0 else "SELL"
        limit = float(quote[1] if side == "BUY" else quote[3])
        requested = abs(delta) * UNIT_QTY
        native_requested = accepted_order_quantity(self.harness.instrument, requested)
        if native_requested == 0.0:
            self.state.align_resting_quantity(0.0)
            self.missed_signals += 1
            return
        self.harness.clock.set_time(timestamp_ns)
        before = len(self.harness.messages)
        self.order = self.harness.limit(
            side=side,
            price=limit,
            quantity=native_requested,
            post_only=True,
            client_order_id=f"M-{self.order_count}",
        )
        accepted_quantity = float(str(self.order.quantity))
        self.state.align_resting_quantity(
            (accepted_quantity if side == "BUY" else -accepted_quantity)
            if self.order.is_open
            else 0.0
        )
        from nautilus_trader.model.events import OrderRejected

        rejected = any(isinstance(event, OrderRejected) for event in self.harness.messages[before:])
        self.order_meta = {
            "strategy_id": self.strategy_id,
            "symbol": self.symbol,
            "fill_probability": self.probability,
            "decision_timestamp_ns": timestamp_ns,
            "client_order_id": str(self.order.client_order_id),
            "side": side,
            "limit_price": limit,
            "contemporaneous_bid": float(quote[1]),
            "contemporaneous_ask": float(quote[3]),
            "requested_quantity": accepted_quantity,
            "target_position": desired,
            "actual_position_before": self.state.actual_position,
            "post_only_rejected": rejected,
        }
        self.orders.append(self.order_meta)


def eligible_events(runner: Runner, quotes: pd.DataFrame, trades: pd.DataFrame) -> list[tuple[int, int, tuple]]:
    if runner.order is None or not runner.order.is_open:
        return []
    limit = float(runner.order.price)
    side = runner.order_meta["side"]
    if side == "BUY":
        qmask = quotes.ask_price.to_numpy(float) <= limit
        tmask = trades.is_buyer_maker.to_numpy(bool) & (trades.price.to_numpy(float) <= limit)
    else:
        qmask = quotes.bid_price.to_numpy(float) >= limit
        tmask = (~trades.is_buyer_maker.to_numpy(bool)) & (trades.price.to_numpy(float) >= limit)
    qrows = quotes[qmask]
    trows = trades[tmask]
    events = [
        (
            int(row.ts_event_ns),
            0,
            (
                int(row.update_id), float(row.bid_price), float(row.bid_size),
                float(row.ask_price), float(row.ask_size), int(row.ts_event_ns), int(row.ts_init_ns),
            ),
        )
        for row in qrows.itertuples(index=False)
    ]
    events.extend(
        (
            int(row.ts_event_ns),
            1,
            (int(row.trade_id), float(row.price), float(row.quantity), int(row.ts_event_ns), bool(row.is_buyer_maker)),
        )
        for row in trows.itertuples(index=False)
    )
    events.sort(key=lambda item: (item[0], item[1], int(item[2][0])))
    return events


def trade_only_decision(
    runner: Runner,
    minute_index: int,
    timestamp_ns: int,
    last_trade: tuple,
    tick_size: float,
) -> None:
    if runner.order is not None and runner.order.is_open:
        runner.harness.cancel(runner.order)
    runner.finalize_order()
    runner.order = None
    runner.order_meta = None
    runner.process_trade(last_trade, timestamp_ns)
    desired = float(runner.target[minute_index])
    delta = runner.state.next_decision(desired)
    if abs(delta) <= 1e-12:
        return
    runner.order_count += 1
    side = "BUY" if delta > 0 else "SELL"
    last_price = float(last_trade[1])
    limit = last_price - tick_size if side == "BUY" else last_price + tick_size
    native_requested = accepted_order_quantity(
        runner.harness.instrument, abs(delta) * UNIT_QTY
    )
    if native_requested == 0.0:
        runner.state.align_resting_quantity(0.0)
        runner.missed_signals += 1
        return
    runner.harness.clock.set_time(timestamp_ns)
    before = len(runner.harness.messages)
    runner.order = runner.harness.limit(
        side=side,
        price=limit,
        quantity=native_requested,
        post_only=True,
        client_order_id=f"M-{runner.order_count}",
    )
    accepted_quantity = float(str(runner.order.quantity))
    runner.state.align_resting_quantity(
        (accepted_quantity if side == "BUY" else -accepted_quantity)
        if runner.order.is_open
        else 0.0
    )
    from nautilus_trader.model.events import OrderRejected

    rejected = any(isinstance(event, OrderRejected) for event in runner.harness.messages[before:])
    runner.order_meta = {
        "strategy_id": runner.strategy_id,
        "symbol": runner.symbol,
        "fill_probability": runner.probability,
        "decision_timestamp_ns": timestamp_ns,
        "client_order_id": str(runner.order.client_order_id),
        "side": side,
        "limit_price": limit,
        "contemporaneous_bid": last_price - tick_size,
        "contemporaneous_ask": last_price + tick_size,
        "requested_quantity": accepted_quantity,
        "target_position": desired,
        "actual_position_before": runner.state.actual_position,
        "post_only_rejected": rejected,
    }
    runner.orders.append(runner.order_meta)


def eligible_trade_only_events(runner: Runner, trades: pd.DataFrame) -> list[tuple]:
    if runner.order is None or not runner.order.is_open:
        return []
    limit = float(runner.order.price)
    if runner.order_meta["side"] == "BUY":
        mask = trades.is_buyer_maker.to_numpy(bool) & (trades.price.to_numpy(float) <= limit)
    else:
        mask = (~trades.is_buyer_maker.to_numpy(bool)) & (trades.price.to_numpy(float) >= limit)
    return [
        (int(row.trade_id), float(row.price), float(row.quantity), int(row.ts_event_ns), bool(row.is_buyer_maker))
        for row in trades[mask].itertuples(index=False)
    ]


def minute_snapshots(quotes: pd.DataFrame, minute_ns: np.ndarray) -> tuple[np.ndarray, pd.DataFrame]:
    ts = quotes.ts_event_ns.to_numpy(np.int64, copy=False)
    indexes = np.searchsorted(ts, minute_ns, side="right") - 1
    if np.any(indexes < 0):
        raise ValueError("no contemporaneous BBO at one or more decision boundaries")
    selected = quotes.iloc[indexes].reset_index(drop=True)
    spread_bps = (selected.ask_price - selected.bid_price) / (
        (selected.ask_price + selected.bid_price) / 2
    ) * 10_000
    return indexes, pd.DataFrame(
        {
            "decision_time_ns": minute_ns,
            "bid": selected.bid_price,
            "ask": selected.ask_price,
            "mid": (selected.bid_price + selected.ask_price) / 2,
            "spread_bps": spread_bps,
        }
    )


def finalize_runner(runner: Runner, minute_marks: pd.DataFrame, initial_mid: float) -> tuple[dict, pd.DataFrame]:
    if runner.order is not None and runner.order.is_open:
        runner.harness.cancel(runner.order)
    runner.finalize_order()
    orders = pd.DataFrame(runner.orders)
    fills = pd.DataFrame(runner.fills)
    path = pd.DataFrame(runner.path)
    if path.empty:
        raise ValueError("maker path is empty")
    cumulative = path.cumulative_return_gross.to_numpy(float)
    peak = np.maximum.accumulate(np.r_[0.0, cumulative])[1:]
    path["drawdown_gross"] = cumulative - peak
    fee_cumulative = path.cumulative_return_standard_fee.to_numpy(float)
    fee_peak = np.maximum.accumulate(np.r_[0.0, fee_cumulative])[1:]
    path["drawdown_standard_fee"] = fee_cumulative - fee_peak
    requested = float(orders.requested_quantity.sum()) if len(orders) else 0.0
    filled_qty = float(fills.fill_quantity.sum()) if len(fills) else 0.0
    fully = int(orders.terminal_status.eq("FILLED").sum()) if len(orders) else 0
    partial = int(((orders.filled_quantity > 0) & (orders.unfilled_quantity > 0)).sum()) if len(orders) else 0
    zero = int(orders.filled_quantity.eq(0).sum()) if len(orders) else 0
    turnover = float(path.cumulative_turnover.iloc[-1])
    result = {
        "strategy_id": runner.strategy_id,
        "symbol": runner.symbol,
        "timeframe": "1m",
        "execution_model": (
            runner.model_label
            if runner.model_label != "L1_BBO_MAKER"
            else ("L1_BBO_MAKER" if runner.probability == 0.5 else f"L1_FILL_P{int(runner.probability*100):03d}")
        ),
        "fill_probability": runner.probability,
        "random_seed": runner.random_seed,
        "maker_policy": "NEXT_DECISION_CANCEL",
        "post_only": True,
        "trade_execution": True,
        "queue_position": False,
        "maker_fee_rate_gross": 0.0,
        "standard_maker_fee_rate": MAKER_FEE_RATE,
        "submitted_orders": len(orders),
        "filled_orders": fully + partial,
        "fully_filled_orders": fully,
        "partial_fill_orders": partial,
        "zero_fill_orders": zero,
        "canceled_orders": int(orders.terminal_status.eq("CANCELED").sum()) if len(orders) else 0,
        "rejected_post_only_orders": int(orders.post_only_rejected.sum()) if len(orders) else 0,
        "quantity_fill_ratio": filled_qty / requested if requested else math.nan,
        "order_fill_ratio": (fully + partial) / len(orders) if len(orders) else math.nan,
        "zero_fill_order_rate": zero / len(orders) if len(orders) else math.nan,
        "partial_fill_order_rate": partial / len(orders) if len(orders) else math.nan,
        "median_time_to_first_fill_ms": float(fills.time_to_first_fill_ms.median()) if len(fills) else math.nan,
        "p95_time_to_first_fill_ms": float(fills.time_to_first_fill_ms.quantile(.95)) if len(fills) else math.nan,
        "mean_absolute_target_position_error": float(path.target_error.abs().mean()),
        "percent_time_at_full_target": float(path.target_error.abs().le(1e-12).mean()),
        "percent_time_below_target": float(path.target_error.abs().gt(1e-12).mean()),
        "missed_signal_count": zero + runner.missed_signals,
        "Return_gross": float(path.cumulative_return_gross.iloc[-1]),
        "Return_standard_maker_fee": float(path.cumulative_return_standard_fee.iloc[-1]),
        "Sharpe_gross": daily_sharpe(pd.to_datetime(path.timestamp_ns, unit="ns", utc=True), cumulative),
        "Sharpe_standard_maker_fee": daily_sharpe(
            pd.to_datetime(path.timestamp_ns, unit="ns", utc=True), fee_cumulative
        ),
        "Max_Drawdown_gross": float(path.drawdown_gross.min()),
        "Max_Drawdown_standard_maker_fee": float(path.drawdown_standard_fee.min()),
        "Turnover_raw": turnover,
        "Signed_BE_bps_gross": float(cumulative[-1] * 10_000 / turnover) if turnover else math.nan,
        "Signed_BE_bps_standard_maker_fee": float(fee_cumulative[-1] * 10_000 / turnover)
        if turnover else math.nan,
        "post_only_rejection_rate": float(orders.post_only_rejected.mean()) if len(orders) else math.nan,
        "maker_order_distance_from_opposite_bbo_median_bps": float(
            (((orders.contemporaneous_ask - orders.limit_price) / orders.limit_price * 10_000).where(orders.side.eq("BUY"),
              (orders.limit_price - orders.contemporaneous_bid) / orders.limit_price * 10_000)).median()
        ) if len(orders) else math.nan,
    }
    return result, path


def render_case(
    output: Path,
    comparison: pd.DataFrame,
    path: pd.DataFrame,
    strategy: str,
    symbol: str,
    orders: pd.DataFrame,
    fills: pd.DataFrame,
) -> str:
    result = comparison.set_index("execution_model")
    first = result.loc["FIRST_TICK_IDEALIZED"]
    maker = result.loc["L1_BBO_MAKER"]
    times = pd.to_datetime(path.timestamp_ns, unit="ns", utc=True)
    figure, axes = plt.subplots(4, 1, figsize=(13, 11), sharex=False, constrained_layout=True)
    axes[0].plot(times, path.cumulative_return_gross, label="L1 BBO maker", color="#1f77b4")
    if "first_tick_cumulative_return" in path:
        axes[0].plot(times, path.first_tick_cumulative_return, label="First tick", color="#ff7f0e")
    axes[0].set_ylabel("1x return")
    axes[0].legend(loc="upper left")
    axes[1].step(times, path.target_position, where="post", label="Target", alpha=.8)
    axes[1].step(times, path.actual_position, where="post", label="Actual maker", alpha=.8)
    axes[1].set_ylabel("Position")
    axes[1].legend(loc="upper left")
    center_ns = int(fills.fill_timestamp_ns.iloc[0]) if len(fills) else int(path.timestamp_ns.iloc[0])
    zoom_mask = path.timestamp_ns.between(
        center_ns - 30 * 60_000_000_000, center_ns + 30 * 60_000_000_000
    )
    zoom_times = times[zoom_mask]
    axes[2].plot(zoom_times, path.loc[zoom_mask, "bid"], label="Best bid", color="#2ca02c")
    axes[2].plot(zoom_times, path.loc[zoom_mask, "ask"], label="Best ask", color="#d62728")
    if len(orders):
        shown_orders = orders[
            orders.decision_timestamp_ns.between(
                center_ns - 30 * 60_000_000_000, center_ns + 30 * 60_000_000_000
            )
        ]
        axes[2].scatter(
            pd.to_datetime(shown_orders.decision_timestamp_ns, unit="ns", utc=True),
            shown_orders.limit_price,
            marker="_", s=65, color="#1f77b4", label="Passive order",
        )
    if len(fills):
        shown_fills = fills[
            fills.fill_timestamp_ns.between(
                center_ns - 30 * 60_000_000_000, center_ns + 30 * 60_000_000_000
            )
        ]
        axes[2].scatter(
            pd.to_datetime(shown_fills.fill_timestamp_ns, unit="ns", utc=True),
            shown_fills.fill_price,
            marker="x", s=35, color="black", label="Maker fill",
        )
    axes[2].set_ylabel("BBO diagnostic")
    axes[2].legend(loc="upper left")
    zoom_start = pd.to_datetime(center_ns - 30 * 60_000_000_000, unit="ns", utc=True)
    zoom_end = pd.to_datetime(center_ns + 30 * 60_000_000_000, unit="ns", utc=True)
    axes[2].set_xlim(zoom_start, zoom_end)
    axes[3].plot(times, path.drawdown_gross, color="#c44e52")
    trough = int(path.drawdown_gross.argmin())
    axes[3].scatter(times[trough], path.drawdown_gross.iloc[trough], color="black", s=25)
    axes[3].set_ylabel("Drawdown")
    axes[3].set_xlabel("UTC")
    for axis in (axes[0], axes[1], axes[3]):
        axis.set_xlim(times.iloc[0], times.iloc[-1])
    figure.suptitle(
        f"{strategy} | {symbol} | L1_BBO_MAKER\n"
        f"FirstTick Sharpe={first.Sharpe:.2f} | L1 Sharpe={maker.Sharpe:.2f} | "
        f"Return={maker.Return:.2%} | MDD={maker.Max_Drawdown:.2%} | "
        f"Fill={maker.quantity_fill_ratio:.1%} | Zero={maker.zero_fill_order_rate:.1%}"
    )
    destination = output / "figures" / f"{strategy}__{symbol}__l1_maker_comparison.png"
    destination.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(destination, dpi=150)
    plt.close(figure)
    return str(destination)


def load_day(output: Path, symbol: str, day: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    quotes = pd.read_parquet(output / f"l1_quotes/symbol={symbol}/date={day}/part.parquet")
    trades = pd.read_parquet(output / f"raw_trades/symbol={symbol}/date={day}/part.parquet")
    # Conversion preserves source order and the ingest gate already proves
    # chronological ordering for every partition.  Avoiding redundant sorts
    # changes no event semantics and substantially reduces memory/CPU.
    return quotes, trades


def first_tick_path(
    target: np.ndarray,
    target_times: np.ndarray,
    days: list[pd.DataFrame],
    initial_mid: float,
    funding_lookup: dict[int, float],
) -> tuple[dict, pd.DataFrame]:
    position = 0.0
    cash = 0.0
    turnover_notional = 0.0
    rows = []
    target_lookup = dict(zip(target_times, target, strict=True))
    for daily in days:
        for row in daily.itertuples(index=False):
            timestamp = int(row.timestamp_ns)
            mid = float(row.mid)
            cash -= position * UNIT_QTY * mid * funding_lookup.get(timestamp, 0.0)
            desired = float(target_lookup.get(int(timestamp), 0.0))
            delta = desired - position
            price = float(row.first_trade_price)
            if abs(delta) > 1e-12:
                cash -= delta * UNIT_QTY * price
                turnover_notional += abs(delta * UNIT_QTY * price)
                position = desired
            rows.append({"timestamp_ns": int(timestamp), "cumulative_return": (cash + position*UNIT_QTY*mid)/(initial_mid*UNIT_QTY), "position": position, "turnover": turnover_notional/(initial_mid*UNIT_QTY)})
    path = pd.DataFrame(rows)
    cumulative = path.cumulative_return.to_numpy(float)
    peak = np.maximum.accumulate(np.r_[0.0, cumulative])[1:]
    turnover = float(path.turnover.iloc[-1])
    return {
        "Return": float(cumulative[-1]),
        "Sharpe": daily_sharpe(pd.to_datetime(path.timestamp_ns, unit="ns", utc=True), cumulative),
        "Max_Drawdown": float((cumulative-peak).min()),
        "Turnover_raw": turnover,
        "Signed_BE_bps": float(cumulative[-1]*10_000/turnover) if turnover else math.nan,
    }, path


def main() -> None:  # noqa: C901
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path, default=ROOT / OUTPUT)
    parser.add_argument("--symbols", nargs="*", choices=SYMBOLS, default=list(SYMBOLS))
    parser.add_argument("--start", default="2024-03-01")
    parser.add_argument("--end-exclusive", default="2024-03-31")
    parser.add_argument("--strategy-limit", type=int)
    parser.add_argument("--result-subdir")
    args = parser.parse_args()
    repo = args.repo.resolve()
    output = args.output.resolve()
    result_root = output / args.result_subdir if args.result_subdir else output
    start = pd.Timestamp(args.start, tz="UTC")
    end = pd.Timestamp(args.end_exclusive, tz="UTC")
    plan = pd.read_csv(repo / PLAN)
    strategies = plan.strategy_id.drop_duplicates().tolist()
    if len(strategies) != 6 or len(plan) != 18:
        raise ValueError("frozen 18-case scope changed")
    if args.strategy_limit:
        strategies = strategies[: args.strategy_limit]
    exchange_info_path = repo / "outputs/binance_exchange_info_phase6d.json"
    exchange_info = json.loads(exchange_info_path.read_text(encoding="utf-8"))
    all_metrics, trade_only_metrics, all_orders, all_fills, all_spreads, model_rows = [], [], [], [], [], []

    for symbol in args.symbols:
        targets = pd.read_parquet(output / f"target_positions_{symbol}.parquet")
        funding = pd.read_parquet(output / f"funding_{symbol}.parquet")
        funding_lookup = dict(
            zip(funding.event_time_ns.astype(np.int64), funding.funding_rate.astype(float), strict=True)
        )
        target_times = targets.decision_time_ns.to_numpy(np.int64)
        instrument = make_instrument(symbol, exchange_info, 0.0)
        runners = []
        trade_runners = []
        for strategy_index, strategy in enumerate(strategies):
            for probability_index, probability in enumerate(PROBABILITIES):
                seed = 10_000 + strategy_index * 10 + probability_index
                runners.append(
                    Runner(
                        strategy_id=strategy,
                        symbol=symbol,
                        probability=probability,
                        harness=NativeMakerHarness(
                            instrument=instrument,
                            liquidity_consumption=True,
                            queue_position=False,
                            fill_probability=probability,
                            seed=seed,
                            maker_fee_rate=0.0,
                        ),
                        target=targets[strategy].to_numpy(float),
                        random_seed=seed,
                    )
                )
            trade_runners.append(
                Runner(
                    strategy_id=strategy,
                    symbol=symbol,
                    probability=1.0,
                    harness=NativeMakerHarness(
                        instrument=instrument,
                        liquidity_consumption=True,
                        queue_position=False,
                        fill_probability=1.0,
                        seed=20_000 + strategy_index,
                        maker_fee_rate=0.0,
                    ),
                    target=targets[strategy].to_numpy(float),
                    random_seed=20_000 + strategy_index,
                    model_label="TRADE_ONLY_MAKER_APPROXIMATION",
                )
            )
        day_summaries = []
        initial_mid = None
        previous_quote = None
        target_lookup_index = {int(ts): index for index, ts in enumerate(target_times)}
        for day in pd.date_range(start, end - pd.Timedelta(days=1), freq="1D"):
            day_text = day.date().isoformat()
            quotes, trades = load_day(output, symbol, day_text)
            day_start = int(day.value)
            minute_ns = np.arange(day_start, day_start + 86_400_000_000_000, 60_000_000_000, dtype=np.int64)
            if previous_quote is None:
                seed_quote = quotes.iloc[[0]].copy()
                seed_quote.loc[:, "ts_event_ns"] = day_start
                seed_quote.loc[:, "ts_init_ns"] = day_start
            else:
                seed_quote = previous_quote
            snapshot_quotes = pd.concat([seed_quote, quotes], ignore_index=True)
            qindexes, snapshots = minute_snapshots(snapshot_quotes, minute_ns)
            if initial_mid is None:
                initial_mid = float(snapshots.mid.iloc[0])
            all_spreads.append(
                snapshots.assign(symbol=symbol, date=day_text)[["symbol", "date", "decision_time_ns", "spread_bps"]]
            )
            qts = quotes.ts_event_ns.to_numpy(np.int64)
            tts = trades.ts_event_ns.to_numpy(np.int64)
            first_trade_indexes = np.searchsorted(tts, minute_ns, side="left")
            if np.any(first_trade_indexes >= len(trades)):
                raise ValueError(f"{symbol} {day_text}: no same-day first trade for a minute")
            day_summaries.append(
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
                qi = int(qindexes[local_index])
                quote_row = (
                    int(snapshot_quotes.update_id.iat[qi]), float(snapshot_quotes.bid_price.iat[qi]),
                    float(snapshot_quotes.bid_size.iat[qi]), float(snapshot_quotes.ask_price.iat[qi]),
                    float(snapshot_quotes.ask_size.iat[qi]), int(snapshot_quotes.ts_event_ns.iat[qi]),
                    int(snapshot_quotes.ts_init_ns.iat[qi]),
                )
                next_time = int(timestamp + 60_000_000_000)
                q0 = int(np.searchsorted(qts, timestamp, side="right"))
                # NEXT_DECISION_CANCEL gives the decision boundary precedence:
                # an event stamped exactly at the next minute belongs to the
                # next decision cycle, after the resting remainder is canceled.
                q1 = int(np.searchsorted(qts, next_time, side="left"))
                t0 = int(np.searchsorted(tts, timestamp, side="right"))
                t1 = int(np.searchsorted(tts, next_time, side="left"))
                interval_quotes = quotes.iloc[q0:q1]
                interval_trades = trades.iloc[t0:t1]
                funding_rate = funding_lookup.get(int(timestamp), 0.0)
                if funding_rate:
                    funding_mid = float(snapshots.mid.iloc[local_index])
                    for runner in [*runners, *trade_runners]:
                        funding_cash = runner.state.actual_position * UNIT_QTY * funding_mid * funding_rate
                        runner.cash_gross -= funding_cash
                        runner.cash_fee -= funding_cash
                for runner in runners:
                    runner.decision(target_index, int(timestamp), quote_row)
                    for _, kind, event in eligible_events(runner, interval_quotes, interval_trades):
                        if kind == 0:
                            runner.process_quote(event, int(timestamp))
                        else:
                            runner.process_trade(event, int(timestamp))
                        if runner.order is None or not runner.order.is_open:
                            break
                    mid = float(snapshots.mid.iloc[local_index])
                    capital = float(initial_mid) * UNIT_QTY
                    runner.path.append(
                        {
                            "timestamp_ns": int(timestamp),
                            "mid": mid, "bid": float(snapshots.bid.iloc[local_index]),
                            "ask": float(snapshots.ask.iloc[local_index]),
                            "target_position": float(runner.target[target_index]),
                            "actual_position": runner.state.actual_position,
                            "target_error": runner.state.target_error,
                            "cumulative_return_gross": (runner.cash_gross + runner.state.actual_position*UNIT_QTY*mid)/capital,
                            "cumulative_return_standard_fee": (runner.cash_fee + runner.state.actual_position*UNIT_QTY*mid)/capital,
                            "cumulative_turnover": runner.turnover_notional/capital,
                        }
                    )
                last_trade_index = int(np.searchsorted(tts, timestamp, side="right") - 1)
                if last_trade_index < 0:
                    last_trade_index = int(first_trade_indexes[local_index])
                last_trade_row = trades.iloc[last_trade_index]
                last_trade = (
                    int(last_trade_row.trade_id), float(last_trade_row.price),
                    float(last_trade_row.quantity), int(last_trade_row.ts_event_ns),
                    bool(last_trade_row.is_buyer_maker),
                )
                for runner in trade_runners:
                    trade_only_decision(
                        runner, target_index, int(timestamp), last_trade,
                        float(instrument.price_increment),
                    )
                    for event in eligible_trade_only_events(runner, interval_trades):
                        runner.process_trade(event, int(timestamp))
                        if runner.order is None or not runner.order.is_open:
                            break
                    mid = float(snapshots.mid.iloc[local_index])
                    capital = float(initial_mid) * UNIT_QTY
                    runner.path.append(
                        {
                            "timestamp_ns": int(timestamp), "mid": mid,
                            "bid": float(snapshots.bid.iloc[local_index]),
                            "ask": float(snapshots.ask.iloc[local_index]),
                            "target_position": float(runner.target[target_index]),
                            "actual_position": runner.state.actual_position,
                            "target_error": runner.state.target_error,
                            "cumulative_return_gross": (runner.cash_gross + runner.state.actual_position*UNIT_QTY*mid)/capital,
                            "cumulative_return_standard_fee": (runner.cash_fee + runner.state.actual_position*UNIT_QTY*mid)/capital,
                            "cumulative_turnover": runner.turnover_notional/capital,
                        }
                    )
            previous_quote = quotes.iloc[[-1]].copy()
        for strategy in strategies:
            first_metrics, first_path = first_tick_path(
                targets[strategy].to_numpy(float), target_times, day_summaries, float(initial_mid), funding_lookup
            )
            model_rows.append({"strategy_id": strategy, "symbol": symbol, "execution_model": "FIRST_TICK_IDEALIZED", **first_metrics})
        for runner in runners:
            metric, path = finalize_runner(runner, pd.DataFrame(), float(initial_mid))
            all_metrics.append(metric)
            all_orders.extend(runner.orders)
            all_fills.extend(runner.fills)
            model_rows.append(
                {
                    "strategy_id": runner.strategy_id,
                    "symbol": symbol,
                    "execution_model": metric["execution_model"],
                    "Return": metric["Return_gross"],
                    "Sharpe": metric["Sharpe_gross"],
                    "Max_Drawdown": metric["Max_Drawdown_gross"],
                    "Turnover_raw": metric["Turnover_raw"],
                    "Signed_BE_bps": metric["Signed_BE_bps_gross"],
                    "quantity_fill_ratio": metric["quantity_fill_ratio"],
                    "zero_fill_order_rate": metric["zero_fill_order_rate"],
                }
            )
            if runner.probability == 0.5:
                first = next(row for row in model_rows if row["strategy_id"] == runner.strategy_id and row["symbol"] == symbol and row["execution_model"] == "FIRST_TICK_IDEALIZED")
                first_path_local = first_tick_path(
                    targets[runner.strategy_id].to_numpy(float), target_times,
                    day_summaries, float(initial_mid), funding_lookup,
                )[1]
                path["first_tick_cumulative_return"] = first_path_local.cumulative_return.to_numpy(float)
                comparison = pd.DataFrame([first, model_rows[-1]])
                render_case(
                    result_root, comparison, path, runner.strategy_id, symbol,
                    pd.DataFrame(runner.orders), pd.DataFrame(runner.fills),
                )
                path_destination = result_root / f"paths/{runner.strategy_id}__{symbol}__L1_BBO_MAKER.parquet"
                path_destination.parent.mkdir(parents=True, exist_ok=True)
                path.to_parquet(path_destination, index=False, compression="zstd")
        for runner in trade_runners:
            metric, _ = finalize_runner(runner, pd.DataFrame(), float(initial_mid))
            trade_only_metrics.append(metric)
            model_rows.append(
                {
                    "strategy_id": runner.strategy_id, "symbol": symbol,
                    "execution_model": "TRADE_ONLY_MAKER_APPROXIMATION",
                    "Return": metric["Return_gross"], "Sharpe": metric["Sharpe_gross"],
                    "Max_Drawdown": metric["Max_Drawdown_gross"],
                    "Turnover_raw": metric["Turnover_raw"],
                    "Signed_BE_bps": metric["Signed_BE_bps_gross"],
                    "quantity_fill_ratio": metric["quantity_fill_ratio"],
                    "zero_fill_order_rate": metric["zero_fill_order_rate"],
                }
            )
        # Release the very large daily frames before the next symbol.
        del day_summaries

    metrics = pd.DataFrame(all_metrics)
    trade_metrics_frame = pd.DataFrame(trade_only_metrics)
    models = pd.DataFrame(model_rows)
    orders = pd.DataFrame(all_orders)
    fills = pd.DataFrame(all_fills)
    spreads = pd.concat(all_spreads, ignore_index=True)
    atomic_csv(metrics, result_root / "l1_maker_execution_metrics.csv")
    atomic_csv(trade_metrics_frame, result_root / "trade_only_maker_execution_metrics.csv")
    atomic_csv(
        metrics[[
            "strategy_id", "symbol", "execution_model", "fill_probability", "random_seed",
            "Return_gross", "Sharpe_gross", "Max_Drawdown_gross", "Turnover_raw",
            "Signed_BE_bps_gross", "quantity_fill_ratio", "zero_fill_order_rate",
        ]],
        result_root / "l1_fill_model_sensitivity.csv",
    )
    atomic_csv(models, result_root / "maker_model_comparison.csv")
    atomic_csv(orders, result_root / "maker_orders.csv")
    atomic_csv(fills, result_root / "maker_fills.csv")
    spread_stats = spreads.groupby("symbol").spread_bps.agg(
        median_spread_bps="median", p95_spread_bps=lambda x: x.quantile(.95), max_spread_bps="max"
    ).reset_index()
    atomic_csv(spread_stats, result_root / "spread_statistics.csv")
    # Exact-event markouts use the already-converted BBO partitions.
    markout_rows = []
    fills_for_markout = fills.copy()
    fills_for_markout["date"] = pd.to_datetime(
        fills_for_markout.fill_timestamp_ns, unit="ns", utc=True
    ).dt.date.astype(str)
    for (symbol, day), group in fills_for_markout.groupby(["symbol", "date"]):
        quote = pd.read_parquet(output / f"l1_quotes/symbol={symbol}/date={day}/part.parquet", columns=["ts_event_ns", "bid_price", "ask_price"])
        ts = quote.ts_event_ns.to_numpy(np.int64)
        for fill in group.itertuples(index=False):
            for seconds in (1, 5, 30, 60):
                index = int(np.searchsorted(ts, int(fill.fill_timestamp_ns) + seconds*1_000_000_000, side="left"))
                if index >= len(quote):
                    continue
                mid = (float(quote.bid_price.iloc[index]) + float(quote.ask_price.iloc[index])) / 2
                sign = 1 if fill.side == "BUY" else -1
                markout_rows.append({"strategy_id": fill.strategy_id, "symbol": fill.symbol, "fill_probability": fill.fill_probability, "horizon_seconds": seconds, "side_adjusted_markout_bps": sign*(mid-float(fill.fill_price))/float(fill.fill_price)*10_000})
    markouts = pd.DataFrame(markout_rows)
    markout_stats = markouts.groupby(["strategy_id", "symbol", "fill_probability", "horizon_seconds"]).side_adjusted_markout_bps.agg(fill_count="size", median_markout_bps="median", mean_markout_bps="mean").reset_index()
    atomic_csv(markout_stats, result_root / "markout_statistics.csv")
    atomic_json(
        {
            "status": "PASSED",
            "pilot_cases": int(models.loc[models.execution_model.eq("L1_BBO_MAKER")].shape[0]),
            "expected_pilot_cases": len(strategies) * len(args.symbols),
            "symbols": list(args.symbols),
            "period": {"start": str(start), "end_exclusive": str(end)},
            "queue_position": False,
            "l1_label": "L1_BBO_MAKER_NOT_QUEUE_AWARE",
            "probabilities": list(PROBABILITIES),
            "maker_fee_scenario": {"gross": 0.0, "standard_sourced": MAKER_FEE_RATE},
            "exchange_info_sha256": sha256(exchange_info_path),
            "nine_symbol_expansion": "NOT_STARTED",
            "all_698_cases_started": False,
        },
        result_root / "pilot_execution_summary.json",
    )


if __name__ == "__main__":
    main()
