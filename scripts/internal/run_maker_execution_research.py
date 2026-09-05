#!/usr/bin/env python3
"""Audit and bounded native maker prototype without changing canonical results."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import sys
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
from strategy_framework.backends.nautilus_maker import run_native_micro_tests  # noqa: E402
from strategy_framework.execution.maker_policy import NextDecisionCancelState  # noqa: E402
from strategy_framework.execution.maker_policy import passive_trade_only_price  # noqa: E402


SYMBOLS = [
    "XRPUSDT",
    "DOGEUSDT",
    "SUIUSDT",
    "BNBUSDT",
    "ETHUSDT",
    "BTCUSDT",
    "1000PEPEUSDT",
    "SOLUSDT",
    "ADAUSDT",
]
COMMON_START = "2024-07-01"
COMMON_END_EXCLUSIVE = "2026-06-30"
STAGE_A = Path("outputs/deliverables/tick_review_stageA_9symbols")
MARKET = Path(
    "historical_data/market_data/asset_class=crypto/exchange=BINANCE/venue_type=futures_um"
)
WORKBOOK_CASES = Path("outputs/baseline_evaluation/boss_multitimeframe_tick_screen/matrix_cases")
PRE_CASES = Path("outputs/baseline_evaluation/tick_review_stageA_9symbols_preworkbook/matrix_cases")


def atomic_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    frame.to_csv(temporary, index=False)
    os.replace(temporary, path)


def atomic_json(payload: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def partition_dates(path: Path) -> tuple[str, str, int, int]:
    files = sorted(path.glob("**/*.parquet")) if path.exists() else []
    dates = sorted(
        {
            part.name.split("=", 1)[1]
            for file in files
            for part in file.parents
            if part.name.startswith("date=")
        }
    )
    return (
        (dates[0], dates[-1], len(files), sum(file.stat().st_size for file in files))
        if dates
        else ("", "", 0, 0)
    )


def audit_data(repo: Path) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for symbol in SYMBOLS:
        base = repo / MARKET / f"symbol={symbol}"
        bar = base / "data_type=bar"
        trade = base / "data_type=trade"
        funding = base / "data_type=funding_rate"
        b0, b1, bn, _ = partition_dates(bar)
        t0, t1, tn, ts = partition_dates(trade)
        f0, f1, fn, _ = partition_dates(funding)
        has_trades = tn > 0
        rows.append(
            {
                "symbol": symbol,
                "bars": bn > 0,
                "trades": has_trades,
                "quotes_l1": False,
                "depth_l2": False,
                "mbo_l3": False,
                "funding": fn > 0,
                "first_timestamp": max(
                    value for value in [b0, f0, t0 if has_trades else b0] if value
                ),
                "last_timestamp": min(
                    value for value in [b1, f1, t1 if has_trades else b1] if value
                ),
                "bar_first_date": b0,
                "bar_last_date": b1,
                "trade_first_date": t0,
                "trade_last_date": t1,
                "trade_partition_count": tn,
                "trade_storage_gb": ts / 1e9,
                "funding_first_date": f0,
                "funding_last_date": f1,
                "maker_realism_tier": "TIER_4_TRADE_ONLY" if has_trades else "DATA_BLOCKED",
                "recommended_execution_model": "TRADE_ONLY_MAKER_APPROXIMATION"
                if has_trades
                else "NO_MAKER_VALIDATION_WITH_CURRENT_DATA",
            }
        )
    return pd.DataFrame(rows)


def candidate_scope(repo: Path, data: pd.DataFrame) -> pd.DataFrame:
    source = pd.read_csv(repo / STAGE_A / "qualifying_1m_sharpe_cases.csv")
    if (
        len(source) != 698
        or not source.timeframe.eq("1m").all()
        or not source.Sharpe.abs().gt(1.5).all()
    ):
        raise ValueError("frozen Stage-A 1m candidate scope no longer reconciles to 698")
    result = source.copy()
    tier = data.set_index("symbol")["maker_realism_tier"]
    result["data_tier"] = result.symbol.map(tier)
    result["execution_model"] = np.where(
        result.data_tier.eq("TIER_4_TRADE_ONLY"),
        "TRADE_ONLY_MAKER_APPROXIMATION",
        "DATA_BLOCKED",
    )
    result["m2_status"] = np.where(
        result.data_tier.eq("TIER_4_TRADE_ONLY"),
        "REALISTIC_MAKER_BLOCKED_NO_BBO;BOUNDED_M1_ELIGIBLE",
        "DATA_BLOCKED_NO_TRADES_OR_BBO",
    )
    return result


def series_path(repo: Path, row: pd.Series) -> Path:
    if row.source_origin == "WORKBOOK":
        return (
            repo
            / WORKBOOK_CASES
            / "symbol=BTCUSDT"
            / "timeframe=1m"
            / f"semantic={row.semantic_group_id}"
            / "review_timeseries.parquet"
        )
    return (
        repo
        / PRE_CASES
        / "symbol=BTCUSDT"
        / "timeframe=1m"
        / f"strategy={row.strategy_id}"
        / "review_timeseries.parquet"
    )


def daily_sharpe_from_equity(times: pd.DatetimeIndex, equity: np.ndarray) -> float:
    series = pd.Series(equity, index=times)
    daily = series.resample("1D").last().diff().dropna()
    if len(daily) < 2 or daily.std(ddof=1) == 0:
        return math.nan
    return float(daily.mean() / daily.std(ddof=1) * math.sqrt(365.0))


def enum_name(value: Any) -> str:
    return str(getattr(value, "name", value))


def run_trade_only_prototype(  # noqa: C901
    repo: Path, row: pd.Series, days: int
) -> tuple[dict[str, Any], pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    Replay a deterministic BTC window through native order matching.

    Raw data are scanned exactly.  Only crossing trades and the minute's final
    trade are materialized as Nautilus ``TradeTick`` objects; this preserves the
    first eligible passive fill while keeping the bounded probe tractable.
    """
    from nautilus_trader.model.enums import OrderSide
    from nautilus_trader.model.events import OrderFilled
    from nautilus_trader.model.events import OrderRejected

    start = pd.Timestamp(COMMON_START, tz="UTC")
    end = start + pd.Timedelta(f"{days}D")
    review = pd.read_parquet(series_path(repo, row)).sort_values("event_time_ns")
    event_ns = review.event_time_ns.to_numpy(np.int64)
    targets = review.executed_position.to_numpy(float)
    state = NextDecisionCancelState()
    harness = NativeMakerHarness(liquidity_consumption=True, maker_fee_rate=0.0)
    tick_size = float(harness.instrument.price_increment)
    unit_qty = 0.01
    order_counter = 0
    orders: list[dict[str, Any]] = []
    fills: list[dict[str, Any]] = []
    minute_rows: list[dict[str, Any]] = []
    cash = 0.0
    initial_price: float | None = None
    last_trade: float | None = None
    open_order = None
    open_meta: dict[str, Any] | None = None

    for day in pd.date_range(start, end - pd.Timedelta("1D"), freq="1D"):
        path = (
            repo
            / MARKET
            / "symbol=BTCUSDT"
            / "data_type=trade"
            / "freq=tick"
            / f"date={day.date()}"
            / "part-0.parquet"
        )
        trades = pd.read_parquet(path, columns=["ts", "price", "quantity", "side"])
        trades["ts"] = pd.to_datetime(trades.ts, utc=True)
        for minute, group in trades.groupby(trades.ts.dt.floor("min"), sort=True):
            boundary_ns = int(minute.value)
            if last_trade is None:
                first = group.iloc[0]
                harness.trade(
                    float(first.price),
                    float(first.quantity),
                    "SELLER" if first.side == "SELL" else "BUYER",
                    int(first.ts.value),
                )
                last_trade = float(first.price)
                initial_price = last_trade
            if open_order is not None and open_order.is_open:
                harness.cancel(open_order)
                if open_meta is not None:
                    open_meta["terminal_status"] = enum_name(open_order.status)
                    open_meta["canceled_remainder"] = float(str(open_order.leaves_qty))
                open_order = None
            target_index = np.searchsorted(event_ns, boundary_ns, side="right") - 1
            target = float(targets[target_index]) if target_index >= 0 else 0.0
            delta = state.next_decision(target)
            open_meta = None
            feed_indexes: list[int] = []
            if abs(delta) > 1e-12:
                order_counter += 1
                side = "BUY" if delta > 0 else "SELL"
                limit = passive_trade_only_price(float(last_trade), tick_size, delta)
                requested = abs(delta) * unit_qty
                before = len(harness.messages)
                harness.clock.set_time(boundary_ns)
                open_order = harness.limit(
                    side=side,
                    price=limit,
                    quantity=requested,
                    post_only=True,
                    client_order_id=f"M1-{order_counter}",
                )
                open_meta = {
                    "strategy_id": row.strategy_id,
                    "source_origin": row.source_origin,
                    "symbol": "BTCUSDT",
                    "decision_timestamp": minute,
                    "client_order_id": str(open_order.client_order_id),
                    "side": side,
                    "limit_price": limit,
                    "requested_quantity": requested,
                    "target_position": target,
                    "actual_position_before": state.actual_position,
                    "post_only": True,
                    "terminal_status": enum_name(open_order.status),
                    "post_only_rejected": any(
                        isinstance(x, OrderRejected) for x in harness.messages[before:]
                    ),
                }
                orders.append(open_meta)
                if open_order.is_open:
                    if side == "BUY":
                        eligible = group.index[(group.side.eq("SELL")) & (group.price.le(limit))]
                    else:
                        eligible = group.index[(group.side.eq("BUY")) & (group.price.ge(limit))]
                    feed_indexes.extend(int(index) for index in eligible)
            last_index = int(group.index[-1])
            if last_index not in feed_indexes:
                feed_indexes.append(last_index)
            for index in sorted(set(feed_indexes)):
                trade = trades.loc[index]
                before = len(harness.messages)
                harness.trade(
                    float(trade.price),
                    float(trade.quantity),
                    "SELLER" if trade.side == "SELL" else "BUYER",
                    int(trade.ts.value),
                )
                new_fills = [
                    event for event in harness.messages[before:] if isinstance(event, OrderFilled)
                ]
                for event in new_fills:
                    signed = (
                        float(str(event.last_qty))
                        / unit_qty
                        * (1.0 if event.order_side == OrderSide.BUY else -1.0)
                    )
                    state.apply_fill(signed)
                    fill_price = float(event.last_px)
                    actual_qty = float(str(event.last_qty)) * (1.0 if signed > 0 else -1.0)
                    cash -= actual_qty * fill_price
                    fills.append(
                        {
                            "strategy_id": row.strategy_id,
                            "symbol": "BTCUSDT",
                            "client_order_id": str(event.client_order_id),
                            "fill_timestamp_ns": int(event.ts_event),
                            "fill_price": fill_price,
                            "fill_quantity": abs(actual_qty),
                            "signed_target_units": signed,
                            "liquidity_side": str(event.liquidity_side),
                            "commission": float(event.commission.as_decimal()),
                            "time_to_fill_ms": (int(event.ts_event) - boundary_ns) / 1_000_000,
                        }
                    )
                last_trade = float(trade.price)
                if open_order is not None and not open_order.is_open:
                    break
            if open_meta is not None:
                open_meta["terminal_status"] = enum_name(open_order.status)
                open_meta["filled_quantity"] = float(str(open_order.filled_qty))
                open_meta["unfilled_quantity"] = float(str(open_order.leaves_qty))
            capital = float(initial_price) * unit_qty
            equity_return = (cash + state.actual_position * unit_qty * float(last_trade)) / capital
            minute_rows.append(
                {
                    "timestamp": minute,
                    "mark_price": last_trade,
                    "target_position": target,
                    "actual_position": state.actual_position,
                    "cumulative_return": equity_return,
                    "target_error": state.target_error,
                }
            )
    if open_order is not None and open_order.is_open:
        harness.cancel(open_order)
        if open_meta is not None:
            open_meta["terminal_status"] = enum_name(open_order.status)
            open_meta["canceled_remainder"] = float(str(open_order.leaves_qty))
    order_frame = pd.DataFrame(orders)
    fill_frame = pd.DataFrame(fills)
    path_frame = pd.DataFrame(minute_rows)
    times = pd.DatetimeIndex(path_frame.timestamp)
    returns = path_frame.cumulative_return.to_numpy(float)
    drawdown = returns - np.maximum.accumulate(returns)
    path_frame["drawdown"] = drawdown
    requested = float(order_frame.requested_quantity.sum()) if len(order_frame) else 0.0
    filled = float(fill_frame.fill_quantity.sum()) if len(fill_frame) else 0.0
    fully = (
        int(order_frame.terminal_status.astype(str).eq("FILLED").sum()) if len(order_frame) else 0
    )
    partial = (
        int(
            (
                order_frame.get("filled_quantity", 0).fillna(0).gt(0)
                & order_frame.get("unfilled_quantity", 0).fillna(0).gt(0)
            ).sum()
        )
        if len(order_frame)
        else 0
    )
    zero = (
        int(order_frame.get("filled_quantity", 0).fillna(0).eq(0).sum()) if len(order_frame) else 0
    )
    baseline_start = review.loc[
        review.event_time_ns.le(int(start.value)), "cumulative_return_with_premium"
    ]
    baseline_window = review.loc[
        review.event_time_ns.ge(int(start.value)) & review.event_time_ns.lt(int(end.value))
    ].copy()
    baseline_end = baseline_window.cumulative_return_with_premium
    baseline_return = (
        float(baseline_end.iloc[-1] - baseline_start.iloc[-1])
        if len(baseline_start) and len(baseline_end)
        else math.nan
    )
    baseline_relative = baseline_window.cumulative_return_with_premium - float(
        baseline_start.iloc[-1]
    )
    baseline_times = pd.to_datetime(baseline_window.event_time_ns, unit="ns", utc=True)
    baseline_values = baseline_relative.to_numpy(float)
    baseline_peak = np.maximum.accumulate(baseline_values) if len(baseline_values) else np.array([])
    baseline_mdd = (
        float(np.min(baseline_values - baseline_peak)) if len(baseline_values) else math.nan
    )
    baseline_turnover_start = review.loc[
        review.event_time_ns.le(int(start.value)), "cumulative_turnover"
    ]
    baseline_turnover = (
        float(baseline_window.cumulative_turnover.iloc[-1] - baseline_turnover_start.iloc[-1])
        if len(baseline_window) and len(baseline_turnover_start)
        else math.nan
    )
    summary = {
        "strategy_id": row.strategy_id,
        "source_origin": row.source_origin,
        "semantic_group_id": row.semantic_group_id,
        "symbol": "BTCUSDT",
        "timeframe": "1m",
        "prototype_start": str(start),
        "prototype_end_exclusive": str(end),
        "execution_model": "TRADE_ONLY_MAKER_APPROXIMATION",
        "maker_policy": "NEXT_DECISION_CANCEL",
        "post_only": True,
        "trade_execution": True,
        "queue_position": False,
        "fill_model": "OPTIMISTIC_TOUCH_FILL",
        "maker_fee_rate": 0.0,
        "order_count": len(order_frame),
        "fully_filled_order_count": fully,
        "partially_filled_order_count": partial,
        "zero_fill_order_count": zero,
        "canceled_order_count": int(
            order_frame.terminal_status.astype(str).str.contains("CANCELED").sum()
        )
        if len(order_frame)
        else 0,
        "expired_order_count": 0,
        "rejected_order_count": int(
            order_frame.terminal_status.astype(str).str.contains("REJECTED").sum()
        )
        if len(order_frame)
        else 0,
        "post_only_reject_count": int(order_frame.post_only_rejected.sum())
        if len(order_frame)
        else 0,
        "fill_rate_orders": (fully + partial) / len(order_frame) if len(order_frame) else math.nan,
        "requested_quantity": requested,
        "filled_quantity": filled,
        "quantity_fill_ratio": filled / requested if requested else math.nan,
        "unfilled_quantity": max(requested - filled, 0.0),
        "cancel_count": int(order_frame.terminal_status.astype(str).str.contains("CANCELED").sum())
        if len(order_frame)
        else 0,
        "requote_count": 0,
        "median_time_to_first_fill_ms": float(fill_frame.time_to_fill_ms.median())
        if len(fill_frame)
        else math.nan,
        "P95_time_to_first_fill_ms": float(fill_frame.time_to_fill_ms.quantile(0.95))
        if len(fill_frame)
        else math.nan,
        "median_time_to_full_fill_ms": float(fill_frame.time_to_fill_ms.median())
        if fully and len(fill_frame)
        else math.nan,
        "median_partial_fill_fraction": float(
            (order_frame.filled_quantity / order_frame.requested_quantity)
            .loc[(order_frame.filled_quantity.gt(0)) & (order_frame.unfilled_quantity.gt(0))]
            .median()
        )
        if partial
        else math.nan,
        "mean_abs_target_position_error": float(path_frame.target_error.mean()),
        "median_abs_target_position_error": float(path_frame.target_error.median()),
        "percent_time_at_full_target": float(path_frame.target_error.le(1e-12).mean()),
        "percent_time_partially_at_target": float(
            (
                (path_frame.target_error.gt(1e-12)) & (path_frame.actual_position.abs().gt(1e-12))
            ).mean()
        ),
        "percent_time_missed_target": float(
            (
                (path_frame.target_error.gt(1e-12)) & (path_frame.actual_position.abs().le(1e-12))
            ).mean()
        ),
        "missed_signal_count": zero,
        "maker_return": float(returns[-1]),
        "maker_sharpe": daily_sharpe_from_equity(times, returns),
        "maker_max_drawdown": float(drawdown.min()),
        "maker_turnover": filled
        * float(path_frame.mark_price.mean())
        / (float(initial_price) * unit_qty),
        "maker_signed_be_bps": float(
            returns[-1]
            * 10000
            / (filled * float(path_frame.mark_price.mean()) / (float(initial_price) * unit_qty))
        )
        if filled
        else math.nan,
        "maker_commissions": float(fill_frame.commission.sum()) if len(fill_frame) else 0.0,
        "maker_rebates": 0.0,
        "first_tick_full_window_return": float(row.Return),
        "first_tick_full_window_sharpe": float(row.Sharpe),
        "first_tick_prototype_window_return": baseline_return,
        "first_tick_prototype_window_sharpe": daily_sharpe_from_equity(
            pd.DatetimeIndex(baseline_times), baseline_values
        ),
        "first_tick_prototype_window_max_drawdown": baseline_mdd,
        "first_tick_prototype_window_turnover": baseline_turnover,
        "first_tick_prototype_window_signed_be_bps": baseline_return * 10000 / baseline_turnover
        if baseline_turnover
        else math.nan,
        "markout_status": "MARKOUT_NOT_AVAILABLE_WITH_CURRENT_DATA",
    }
    return summary, order_frame, fill_frame, path_frame


def render_prototype(
    path: Path,
    summary: dict[str, Any],
    frame: pd.DataFrame,
    orders: pd.DataFrame,
    fills: pd.DataFrame,
) -> None:
    fig, axes = plt.subplots(4, 1, figsize=(14, 10), sharex=True, constrained_layout=True)
    x = pd.to_datetime(frame.timestamp, utc=True)
    axes[0].plot(x, frame.cumulative_return, label="MAKER_ONLY (trade-only approximation)")
    axes[0].axhline(
        summary["first_tick_prototype_window_return"],
        color="grey",
        ls="--",
        label="FIRST_TICK window endpoint",
    )
    axes[0].legend(loc="best")
    axes[0].set_ylabel("1x Return")
    axes[1].step(x, frame.target_position, where="post", label="Target", alpha=0.7)
    axes[1].step(x, frame.actual_position, where="post", label="Actual filled", alpha=0.8)
    axes[1].set_ylabel("Position")
    axes[1].legend(loc="best")
    order_time = pd.to_datetime(orders.decision_timestamp, utc=True)
    axes[2].scatter(order_time, np.full(len(orders), 3), s=4, label="Submitted", alpha=0.5)
    if len(fills):
        fill_time = pd.to_datetime(fills.fill_timestamp_ns, unit="ns", utc=True)
        axes[2].scatter(fill_time, np.full(len(fills), 2), s=7, label="Filled/partial", alpha=0.7)
    canceled = orders.terminal_status.astype(str).eq("CANCELED")
    rejected = orders.terminal_status.astype(str).eq("REJECTED")
    axes[2].scatter(order_time[canceled], np.full(int(canceled.sum()), 1), s=6, label="Canceled")
    axes[2].scatter(order_time[rejected], np.full(int(rejected.sum()), 0), s=6, label="Rejected")
    axes[2].set_yticks([0, 1, 2, 3], ["Rejected", "Canceled", "Fill", "Submit"])
    axes[2].set_ylabel("Order lifecycle")
    axes[2].legend(loc="upper right", ncol=4, fontsize=8)
    axes[3].plot(x, frame.drawdown, color="#b91c1c")
    trough = int(frame.drawdown.argmin())
    axes[3].scatter([x.iloc[trough]], [frame.drawdown.iloc[trough]], color="black", zorder=5)
    axes[3].annotate(
        f"MaxDD={frame.drawdown.iloc[trough]:.2%}\n{x.iloc[trough].date()}",
        (x.iloc[trough], frame.drawdown.iloc[trough]),
        xytext=(8, 8),
        textcoords="offset points",
        fontsize=8,
    )
    axes[3].set_ylabel("Maker DD")
    axes[3].set_title(
        f"fill ratio={summary['quantity_fill_ratio']:.3f} | zero-fill={summary['zero_fill_order_count'] / max(summary['order_count'], 1):.3f} | "
        f"Sharpe={summary['maker_sharpe']:.2f} | MDD={summary['maker_max_drawdown']:.2%}"
    )
    fig.suptitle(f"{summary['strategy_id']} | BTCUSDT | 1m | TRADE_ONLY_MAKER_APPROXIMATION")
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=150)
    plt.close(fig)


def no_fill_table() -> pd.DataFrame:
    return pd.DataFrame(
        [
            [
                "NEXT_DECISION_CANCEL",
                True,
                "Cancel remainder at next 1m decision and recompute from filled position",
                "Lowest",
                "Low",
                "IMPLEMENTED",
                "L1+trades preferred; trade-only labelled approximation",
                "PRIMARY",
            ],
            [
                "GTC_UNTIL_SIGNAL_INVALID",
                True,
                "Rest until fill or target invalidation",
                "Higher",
                "Medium",
                "NATIVE_SUPPORTED",
                "L1+trades",
                "LATER_SENSITIVITY",
            ],
            [
                "TIMEOUT_CANCEL",
                True,
                "Cancel after fixed wall-clock timeout",
                "Depends on timeout",
                "Low",
                "NATIVE_SUPPORTED_GTD/TIMER",
                "L1+trades",
                "LATER_SENSITIVITY",
            ],
            [
                "CANCEL_REQUOTE",
                True,
                "Cancel and repost at current passive BBO",
                "Higher",
                "Low",
                "NATIVE_SUPPORTED",
                "Continuous BBO+trades",
                "REQUIRES_L1",
            ],
            [
                "TAKER_FALLBACK",
                False,
                "Cross after timeout",
                "Highest",
                "Low",
                "NATIVE_SUPPORTED_BUT_NOT_MAKER_ONLY",
                "BBO/trades",
                "EXCLUDED_FROM_HEADLINE",
            ],
        ],
        columns=[
            "policy",
            "maker_only",
            "behavior",
            "fill_expectation",
            "stale_order_risk",
            "implementation_support",
            "data_requirement",
            "recommended_role",
        ],
    )


def research_report(version: str) -> str:
    return f"""# Nautilus maker-execution capability audit

Installed server version: `{version}` (Python 3.13 build). Installed source/API, not current online signatures, is authoritative.

## Answers

1. Native `LimitOrder(post_only=True)` is supported.
2. A marketable post-only order is rejected with `OrderRejected(due_post_only=True)`.
3. `TradeTick` can fill a resting passive order when aggressor side and price cross the limit.
4. This requires `trade_execution=True`; with it disabled trades update market state but do not match resting orders.
5. Trades alone are Tier 4 and cannot establish BBO or queue. L1 quotes+trades support passive-price checks; L2 MBP+trades supports displayed depth; L3 MBO+trades is strongest.
6. `queue_position=True` exists and requires `trade_execution=True`; meaningful historical use requires quote/depth/order-book state. The project data block its use.
7. Native orders expose `PARTIALLY_FILLED`, `FILLED`, `CANCELED`, `EXPIRED`, and `REJECTED`; `OrderFilled` carries last quantity, price, commission, and liquidity side.
8. GTC rests until filled/canceled/invalidation. GTD uses `expire_time_ns`; the primary experiment uses explicit next-decision cancellation.
9. `MakerTakerFeeModel` reads instrument maker/taker fee metadata.
10. A negative maker rate returns negative commission and therefore represents a rebate mechanically; no account-specific rate was supplied.
11. Installed `FillModel(prob_fill_on_limit=1.0)` defaults to optimistic eligible-touch fills and is not queue realism.
12. All nine symbols have bars and funding. Only BTCUSDT has raw trades. No symbol has historical BBO/L1, L2, or L3 in the project.

## Architecture and decision

The existing native adapter replays target intents as market orders. The additive maker module uses the installed native `OrderMatchingEngine` for post-only lifecycle and derives position only from `OrderFilled`. The primary policy is `NEXT_DECISION_CANCEL`: cancel any remainder at the next completed 1m decision, retain partial fills, recompute delta from actual filled position, and never fall back to taker.

With current data, no result qualifies as realistic maker validation. BTC permits a separately labelled `TRADE_ONLY_MAKER_APPROXIMATION`, using last observed trade minus one price tick for BUY and plus one tick for SELL. This explicit weak-tier rule is not BBO. The other eight symbols are data-blocked. Mid-price markout is unavailable.

## Data acquisition implication

Realistic Stage M2 requires at least historical BBO/L1 quotes plus raw trades for all frozen symbols; queue/depth research requires L2 or L3. Large acquisition was not authorized, so no new market data were downloaded.

## Sources

- [Orders and post-only](https://nautilustrader.io/docs/latest/concepts/orders/)
- [Limit orders](https://nautilustrader.io/docs/latest/concepts/orders/limit/)
- [Trade-based execution](https://nautilustrader.io/docs/latest/concepts/backtesting/trade-execution/)
- [Fill models](https://nautilustrader.io/docs/latest/concepts/backtesting/fill-models/)
- [Backtest data and venues](https://nautilustrader.io/docs/latest/concepts/backtesting/data-and-venues/)
- Installed source: `nautilus_trader/backtest/engine.pyx`, `models/fill.pyx`, `models/fee.pyx`.
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=ROOT)
    parser.add_argument(
        "--output", type=Path, default=Path("outputs/baseline_evaluation/maker_execution_research")
    )
    parser.add_argument("--prototype-days", type=int, default=7)
    args = parser.parse_args()
    repo = args.repo.resolve()
    output = args.output if args.output.is_absolute() else repo / args.output
    output.mkdir(parents=True, exist_ok=True)

    import nautilus_trader

    protected = [
        repo / STAGE_A / "all_1m10m15m_results.csv",
        repo / STAGE_A / "qualifying_1m_sharpe_cases.csv",
        repo
        / "outputs/deliverables/boss_multitimeframe_final_delivery/02_full_results/boss_multitimeframe_tick_master.csv",
    ]
    before = {str(path): sha256(path) for path in protected}
    data = audit_data(repo)
    atomic_csv(data, output / "maker_data_availability.csv")
    scope = candidate_scope(repo, data)
    atomic_csv(scope, output / "maker_candidate_scope.csv")
    atomic_csv(no_fill_table(), output / "research/no_fill_policy_comparison.csv")
    report_path = output / "research/nautilus_maker_execution_research.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(research_report(nautilus_trader.__version__), encoding="utf-8")

    micro = pd.DataFrame([item.__dict__ for item in run_native_micro_tests()])
    atomic_csv(micro, output / "micro_tests/micro_test_results.csv")

    btc = scope[(scope.symbol == "BTCUSDT") & (scope.timeframe == "1m")]
    selected = pd.concat(
        [
            btc[btc.source_origin.eq("WORKBOOK")].sort_values("strategy_id").head(1),
            btc[btc.source_origin.eq("PRE_WORKBOOK")].sort_values("strategy_id").head(1),
        ],
        ignore_index=True,
    )
    summaries: list[dict[str, Any]] = []
    all_orders: list[pd.DataFrame] = []
    all_fills: list[pd.DataFrame] = []
    for row in selected.itertuples(index=False):
        series = pd.Series(row._asdict())
        summary, orders, fills, path = run_trade_only_prototype(repo, series, args.prototype_days)
        summaries.append(summary)
        all_orders.append(orders)
        all_fills.append(fills)
        figure_path = output / "prototype/figures" / f"{row.strategy_id}__BTCUSDT__1m__maker.png"
        render_prototype(figure_path, summary, path, orders, fills)
        stage_figure = output / "stageA_9symbols/figures" / figure_path.name
        stage_figure.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(figure_path, stage_figure)
        path.to_parquet(
            output / "prototype" / f"{row.strategy_id}__BTCUSDT__1m__path.parquet", index=False
        )
    metrics = pd.DataFrame(summaries)
    orders = pd.concat(all_orders, ignore_index=True) if all_orders else pd.DataFrame()
    fills = pd.concat(all_fills, ignore_index=True) if all_fills else pd.DataFrame()
    atomic_csv(metrics, output / "prototype/maker_execution_metrics.csv")
    comparison = metrics[
        [
            "strategy_id",
            "source_origin",
            "semantic_group_id",
            "symbol",
            "timeframe",
            "execution_model",
            "first_tick_full_window_return",
            "first_tick_full_window_sharpe",
            "first_tick_prototype_window_return",
            "first_tick_prototype_window_sharpe",
            "first_tick_prototype_window_max_drawdown",
            "first_tick_prototype_window_turnover",
            "first_tick_prototype_window_signed_be_bps",
            "maker_return",
            "maker_sharpe",
            "maker_max_drawdown",
            "maker_turnover",
            "maker_signed_be_bps",
            "quantity_fill_ratio",
            "percent_time_missed_target",
        ]
    ].copy()
    comparison["delta_return_prototype_window"] = (
        comparison.maker_return - comparison.first_tick_prototype_window_return
    )
    comparison["delta_sharpe_prototype_window"] = (
        comparison.maker_sharpe - comparison.first_tick_prototype_window_sharpe
    )
    comparison["delta_max_drawdown_prototype_window"] = (
        comparison.maker_max_drawdown - comparison.first_tick_prototype_window_max_drawdown
    )
    comparison["delta_turnover_prototype_window"] = (
        comparison.maker_turnover - comparison.first_tick_prototype_window_turnover
    )
    comparison["delta_signed_be_bps_prototype_window"] = (
        comparison.maker_signed_be_bps - comparison.first_tick_prototype_window_signed_be_bps
    )
    atomic_csv(comparison, output / "prototype/maker_vs_first_tick.csv")
    atomic_csv(orders, output / "prototype/maker_orders.csv")
    atomic_csv(fills, output / "prototype/maker_fills.csv")

    stage = scope[
        [
            "strategy_id",
            "source_origin",
            "semantic_group_id",
            "symbol",
            "timeframe",
            "Return",
            "Sharpe",
            "Signed_BE_bps",
            "data_tier",
            "execution_model",
            "m2_status",
        ]
    ].copy()
    stage["maker_run_status"] = "NOT_RUN_REALISTIC_MAKER_DATA_BLOCKED"
    stage.loc[
        stage.strategy_id.isin(metrics.strategy_id) & stage.symbol.eq("BTCUSDT"), "maker_run_status"
    ] = "M1_BOUNDED_TRADE_ONLY_APPROXIMATION_COMPLETED"
    atomic_csv(stage, output / "stageA_9symbols/maker_results.csv")
    atomic_csv(comparison, output / "stageA_9symbols/maker_vs_first_tick.csv")
    atomic_csv(metrics, output / "stageA_9symbols/order_execution_summary.csv")
    figures = output / "stageA_9symbols/figures"
    figures.mkdir(parents=True, exist_ok=True)

    after = {str(path): sha256(path) for path in protected}
    changed = [path for path in before if before[path] != after[path]]
    validation = {
        "status": "PARTIAL",
        "reason": "REALISTIC_MAKER_VALIDATION_DATA_BLOCKED_NO_BBO_L1_L2_L3",
        "installed_nautilus_version": nautilus_trader.__version__,
        "native_post_only_support": True,
        "trade_triggered_passive_fill_support": True,
        "queue_position_api_support": True,
        "queue_position_project_data_status": "DATA_BLOCKED",
        "micro_tests_passed": int(micro.status.eq("PASSED").sum()),
        "micro_tests_total": len(micro),
        "candidate_scope_count": len(scope),
        "candidate_scope_expected": 698,
        "prototype_case_count": len(metrics),
        "realistic_stage_m2_case_count": 0,
        "trade_only_candidate_count": int(scope.data_tier.eq("TIER_4_TRADE_ONLY").sum()),
        "data_blocked_candidate_count": int(scope.data_tier.eq("DATA_BLOCKED").sum()),
        "first_tick_hashes_before": before,
        "first_tick_hashes_after": after,
        "protected_hash_changes": changed,
        "old_results_unchanged": not changed,
        "all_symbol_expansion": "NOT_STARTED",
        "large_data_downloads": 0,
        "canonical_strategy_changes": 0,
        "canonical_parameter_changes": 0,
        "markout": "MARKOUT_NOT_AVAILABLE_WITH_CURRENT_DATA",
    }
    atomic_json(validation, output / "validation_summary.json")
    return 0 if not changed and micro.status.eq("FAILED").sum() == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
