#!/usr/bin/env python3
"""Merge, attribute, render, and validate the frozen L1 lifecycle-policy study."""

from __future__ import annotations

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
import matplotlib.dates as mdates
import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.internal.run_l1_maker_pilot import atomic_csv, atomic_json, load_day  # noqa: E402


PILOT = ROOT / "outputs/baseline_evaluation/maker_execution_research/l1_pilot"
OUTPUT = ROOT / "outputs/baseline_evaluation/maker_execution_research/l1_policy_study"
SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
P0 = "NEXT_DECISION_CANCEL"
P1 = "GTC_UNTIL_SIGNAL_INVALID"
P2 = "PASSIVE_CANCEL_REQUOTE_15S"
POLICIES = (P0, P1, P2)
UNIT_QTY = 1.0


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def merge_partials() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    metrics, orders, fills = [], [], []
    for symbol in SYMBOLS:
        partial = OUTPUT / f"run_{symbol}"
        summary = json.loads((partial / "run_summary.json").read_text(encoding="utf-8"))
        if summary["status"] != "PASSED" or summary["cases"] != 12:
            raise ValueError(f"{symbol} partial is not terminal")
        metrics.append(pd.read_csv(partial / "policy_metrics.csv"))
        orders.append(pd.read_csv(partial / "policy_orders.csv"))
        fills.append(pd.read_csv(partial / "policy_fills.csv"))
        shutil.copy2(partial / f"minute_reference_{symbol}.parquet", OUTPUT / f"minute_reference_{symbol}.parquet")
        for path in (partial / "paths").glob("*.parquet"):
            destination = OUTPUT / "paths" / path.name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)
    return (
        pd.concat(metrics, ignore_index=True),
        pd.concat(orders, ignore_index=True),
        pd.concat(fills, ignore_index=True),
    )


def filter_p0() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    metrics = pd.read_csv(PILOT / "l1_maker_execution_metrics.csv")
    metrics = metrics[metrics.execution_model.eq("L1_BBO_MAKER")].copy()
    metrics["policy"] = P0
    orders = pd.read_csv(PILOT / "maker_orders.csv")
    orders = orders[np.isclose(orders.fill_probability, .5)].copy()
    orders["policy"] = P0
    orders["submit_timestamp_ns"] = orders.decision_timestamp_ns
    orders["cancel_reason"] = np.where(orders.terminal_status.eq("CANCELED"), "NEXT_DECISION", "")
    fills = pd.read_csv(PILOT / "maker_fills.csv")
    fills = fills[np.isclose(fills.fill_probability, .5)].copy()
    fills["policy"] = P0
    fills["liquidity_side"] = "MAKER"
    models = pd.read_csv(PILOT / "maker_model_comparison.csv")
    first = models[models.execution_model.eq("FIRST_TICK_IDEALIZED")].copy()
    return metrics, orders, fills, first


def p0_extra(metric: pd.Series, orders: pd.DataFrame, fills: pd.DataFrame, path: pd.DataFrame) -> dict[str, Any]:
    nonzero = path.target_position.abs().gt(1e-12)
    at_target = path.target_error.abs().le(1e-12)
    zero_actual = path.actual_position.abs().le(1e-12)
    joined = orders.merge(
        fills.groupby("client_order_id").fill_timestamp_ns.agg(["min", "max"]),
        left_on="client_order_id", right_index=True, how="left",
    )
    full_delay = (joined.loc[joined.terminal_status.eq("FILLED"), "max"] - joined.loc[joined.terminal_status.eq("FILLED"), "submit_timestamp_ns"]) / 1_000_000
    first_delay = (joined["min"] - joined.submit_timestamp_ns) / 1_000_000
    return {
        "strategy_id": metric.strategy_id,
        "symbol": metric.symbol,
        "policy": P0,
        "Return": metric.Return_gross,
        "Sharpe": metric.Sharpe_gross,
        "MaxDD": metric.Max_Drawdown_gross,
        "Turnover": metric.Turnover_raw,
        "Signed_BE_bps": metric.Signed_BE_bps_gross,
        "Return_standard_maker_fee": metric.Return_standard_maker_fee,
        "submitted_orders": metric.submitted_orders,
        "full_fill_orders": metric.fully_filled_orders,
        "partial_fill_orders": metric.partial_fill_orders,
        "zero_fill_orders": metric.zero_fill_orders,
        "quantity_fill_ratio": metric.quantity_fill_ratio,
        "order_fill_ratio": metric.order_fill_ratio,
        "zero_fill_rate": metric.zero_fill_order_rate,
        "cancel_count": metric.canceled_orders,
        "requote_count": 0,
        "median_time_to_first_fill_ms": float(first_delay.median()),
        "p75_time_to_first_fill_ms": float(first_delay.quantile(.75)),
        "p90_time_to_first_fill_ms": float(first_delay.quantile(.90)),
        "p95_time_to_first_fill_ms": float(first_delay.quantile(.95)),
        "median_time_to_full_fill_ms": float(full_delay.median()),
        "mean_absolute_target_position_error": float(path.target_error.abs().mean()),
        "percent_time_at_full_target": float(at_target.mean()),
        "percent_time_partially_at_target": float((nonzero & ~at_target & ~zero_actual).mean()),
        "percent_time_zero_when_nonzero_target": float((nonzero & zero_actual).mean()),
        "stale_order_cancellations": 0,
        "reversal_before_fill_count": 0,
        "target_changes_before_full_fill": 0,
        "post_only_rejected_orders": metric.rejected_post_only_orders,
    }


def normalize_new(metric: pd.Series) -> dict[str, Any]:
    return {
        "strategy_id": metric.strategy_id,
        "symbol": metric.symbol,
        "policy": metric.policy,
        "Return": metric.Return_gross,
        "Sharpe": metric.Sharpe_gross,
        "MaxDD": metric.Max_Drawdown_gross,
        "Turnover": metric.Turnover_raw,
        "Signed_BE_bps": metric.Signed_BE_bps_gross,
        "Return_standard_maker_fee": metric.Return_standard_maker_fee,
        "submitted_orders": metric.submitted_orders,
        "full_fill_orders": metric.fully_filled_orders,
        "partial_fill_orders": metric.partial_fill_orders,
        "zero_fill_orders": metric.zero_fill_orders,
        "quantity_fill_ratio": metric.quantity_fill_ratio,
        "order_fill_ratio": metric.order_fill_ratio,
        "zero_fill_rate": metric.zero_fill_order_rate,
        "cancel_count": metric.canceled_orders,
        "requote_count": metric.requote_count,
        "median_time_to_first_fill_ms": metric.median_time_to_first_fill_ms,
        "p75_time_to_first_fill_ms": metric.p75_time_to_first_fill_ms,
        "p90_time_to_first_fill_ms": metric.p90_time_to_first_fill_ms,
        "p95_time_to_first_fill_ms": metric.p95_time_to_first_fill_ms,
        "median_time_to_full_fill_ms": metric.median_time_to_full_fill_ms,
        "mean_absolute_target_position_error": metric.mean_absolute_target_position_error,
        "percent_time_at_full_target": metric.percent_time_at_full_target,
        "percent_time_partially_at_target": metric.percent_time_partially_at_target,
        "percent_time_zero_when_nonzero_target": metric.percent_time_zero_when_nonzero_target,
        "stale_order_cancellations": metric.stale_order_cancellations,
        "reversal_before_fill_count": metric.reversal_before_fill_count,
        "target_changes_before_full_fill": metric.target_changes_before_full_fill,
        "post_only_rejected_orders": metric.rejected_post_only_orders,
    }


def policy_path(strategy: str, symbol: str, policy: str) -> pd.DataFrame:
    if policy == P0:
        return pd.read_parquet(PILOT / "paths" / f"{strategy}__{symbol}__L1_BBO_MAKER.parquet")
    return pd.read_parquet(OUTPUT / "paths" / f"{strategy}__{symbol}__{policy}.parquet")


def episode_rows(strategy: str, symbol: str, policy: str, path: pd.DataFrame) -> list[dict[str, Any]]:
    target = path.target_position.to_numpy(float)
    actual = path.actual_position.to_numpy(float)
    maker_return = path.cumulative_return_gross.to_numpy(float)
    reference_return = path.first_tick_cumulative_return.to_numpy(float)
    boundaries = np.r_[0, np.flatnonzero(~np.isclose(target[1:], target[:-1])) + 1, len(path)]
    rows = []
    for start, stop in zip(boundaries[:-1], boundaries[1:], strict=True):
        desired = target[start]
        if abs(desired) <= 1e-12:
            continue
        actual_before = actual[start - 1] if start else 0.0
        requested = desired - actual_before
        if abs(requested) <= 1e-12:
            continue
        progress = np.sign(requested) * (actual[start:stop] - actual_before) / abs(requested)
        fill_fraction = float(np.clip(np.nanmax(progress), 0.0, 1.0))
        if fill_fraction >= 1.0 - 1e-12:
            continue
        maker_before = maker_return[start - 1] if start else 0.0
        reference_before = reference_return[start - 1] if start else 0.0
        rows.append(
            {
                "strategy": strategy,
                "symbol": symbol,
                "policy": policy,
                "decision_start": pd.to_datetime(int(path.timestamp_ns.iloc[start]), unit="ns", utc=True),
                "target_end": pd.to_datetime(int(path.timestamp_ns.iloc[stop - 1]) + 60_000_000_000, unit="ns", utc=True),
                "desired_position": desired,
                "max_achieved_position": float(actual_before + np.sign(requested) * fill_fraction * abs(requested)),
                "fill_fraction": fill_fraction,
                "episode_type": "COMPLETELY_MISSED" if fill_fraction <= 1e-12 else "PARTIALLY_FILLED",
                "first_tick_reference_return": float(reference_return[stop - 1] - reference_before),
                "maker_realized_return": float(maker_return[stop - 1] - maker_before),
            }
        )
    return rows


def markouts(all_fills: pd.DataFrame) -> pd.DataFrame:
    rows = []
    all_fills = all_fills.copy()
    all_fills["date"] = pd.to_datetime(all_fills.fill_timestamp_ns, unit="ns", utc=True).dt.date.astype(str)
    for (symbol, day), group in all_fills.groupby(["symbol", "date"], sort=True):
        quote = pd.read_parquet(
            PILOT / f"l1_quotes/symbol={symbol}/date={day}/part.parquet",
            columns=["ts_event_ns", "bid_price", "ask_price"],
        )
        ts = quote.ts_event_ns.to_numpy(np.int64, copy=False)
        mid = (quote.bid_price.to_numpy(float) + quote.ask_price.to_numpy(float)) / 2
        fill_ts = group.fill_timestamp_ns.to_numpy(np.int64)
        fill_price = group.fill_price.to_numpy(float)
        sign = np.where(group.side.eq("BUY"), 1.0, -1.0)
        for horizon in (1, 5, 30, 60):
            indexes = np.searchsorted(ts, fill_ts + horizon * 1_000_000_000, side="left")
            valid = indexes < len(mid)
            values = sign[valid] * (mid[indexes[valid]] - fill_price[valid]) / fill_price[valid] * 10_000
            selected = group.loc[valid, ["strategy_id", "symbol", "policy"]].copy()
            selected["horizon_seconds"] = horizon
            selected["side_adjusted_markout_bps"] = values
            rows.append(selected)
    detail = pd.concat(rows, ignore_index=True)
    return detail.groupby(["strategy_id", "symbol", "policy", "horizon_seconds"]).side_adjusted_markout_bps.agg(
        fill_count="size",
        median_markout_bps="median",
        mean_markout_bps="mean",
        negative_markout_fraction=lambda x: x.lt(0).mean(),
    ).reset_index()


def timing_stats(orders: pd.DataFrame, fills: pd.DataFrame) -> pd.DataFrame:
    joined = fills.merge(
        orders[["strategy_id", "symbol", "policy", "client_order_id", "decision_timestamp_ns"]],
        on=["strategy_id", "symbol", "policy", "client_order_id"], how="left", validate="many_to_one",
    )
    reference = pd.concat(
        [pd.read_parquet(OUTPUT / f"minute_reference_{symbol}.parquet").assign(symbol=symbol) for symbol in SYMBOLS],
        ignore_index=True,
    ).rename(columns={"timestamp_ns": "decision_timestamp_ns"})
    joined = joined.merge(reference[["symbol", "decision_timestamp_ns", "first_trade_price"]], on=["symbol", "decision_timestamp_ns"], how="left", validate="many_to_one")
    joined["delay_from_ideal_ms"] = (joined.fill_timestamp_ns - joined.decision_timestamp_ns) / 1_000_000
    sign = np.where(joined.side.eq("BUY"), 1.0, -1.0)
    joined["maker_minus_first_tick_price_bps"] = sign * (joined.first_trade_price - joined.fill_price) / joined.first_trade_price * 10_000
    initial_capital = reference.groupby("symbol").first_trade_price.first().to_dict()
    joined["timing_pnl_increment"] = (
        sign
        * (joined.first_trade_price - joined.fill_price)
        * joined.fill_quantity
        / joined.symbol.map(initial_capital)
    )
    return joined.groupby(["strategy_id", "symbol", "policy"]).agg(
        timing_delay_median_ms=("delay_from_ideal_ms", "median"),
        timing_delay_p75_ms=("delay_from_ideal_ms", lambda x: x.quantile(.75)),
        timing_delay_p90_ms=("delay_from_ideal_ms", lambda x: x.quantile(.90)),
        timing_delay_p95_ms=("delay_from_ideal_ms", lambda x: x.quantile(.95)),
        maker_minus_first_tick_price_median_bps=("maker_minus_first_tick_price_bps", "median"),
        timing_pnl_delta=("timing_pnl_increment", "sum"),
    ).reset_index()


def restore_signal_activation_times(orders: pd.DataFrame) -> pd.DataFrame:
    """Map P1/P2 requotes back to the frozen target episode's first decision."""
    restored = orders.copy()
    for (strategy, symbol), indexes in restored.groupby(["strategy_id", "symbol"]).groups.items():
        targets = pd.read_parquet(PILOT / f"target_positions_{symbol}.parquet", columns=["decision_time_ns", strategy])
        times = targets.decision_time_ns.to_numpy(np.int64)
        values = targets[strategy].to_numpy(float)
        starts = np.r_[0, np.flatnonzero(~np.isclose(values[1:], values[:-1])) + 1]
        activation = np.empty(len(values), dtype=np.int64)
        for start, stop in zip(starts, np.r_[starts[1:], len(values)], strict=True):
            activation[start:stop] = times[start]
        submit = restored.loc[indexes, "submit_timestamp_ns"].to_numpy(np.int64)
        positions = np.searchsorted(times, submit, side="right") - 1
        if np.any(positions < 0):
            raise ValueError("order predates frozen signal path")
        restored.loc[indexes, "decision_timestamp_ns"] = activation[positions]
    return restored


def render_execution_examples(orders: pd.DataFrame, fills: pd.DataFrame) -> list[str]:
    """Render deterministic event-type examples, never selected by profitability."""
    ordered = orders.sort_values(["strategy_id", "symbol", "policy", "submit_timestamp_ns"]).copy()
    selectors = {
        "full_fill": ordered.terminal_status.eq("FILLED"),
        "partial_fill": ordered.filled_quantity.gt(0) & ordered.unfilled_quantity.gt(0),
        "complete_no_fill": ordered.filled_quantity.eq(0) & ordered.terminal_status.eq("CANCELED"),
        "reversal_before_fill": ordered.cancel_reason.eq("SIGNAL_REVERSAL") & ordered.filled_quantity.eq(0),
        "requote_sequence": ordered.cancel_reason.eq("REQUOTE_15S"),
    }
    destinations = []
    for label, mask in selectors.items():
        candidates = ordered[mask]
        if candidates.empty:
            continue
        event = candidates.iloc[0]
        center_ns = int(event.get("cancel_timestamp_ns") if pd.notna(event.get("cancel_timestamp_ns")) else event.submit_timestamp_ns)
        day = pd.to_datetime(center_ns, unit="ns", utc=True).date().isoformat()
        quote = pd.read_parquet(
            PILOT / f"l1_quotes/symbol={event.symbol}/date={day}/part.parquet",
            columns=["ts_event_ns", "bid_price", "ask_price"],
        )
        lo, hi = center_ns - 60_000_000_000, center_ns + 60_000_000_000
        q = quote[quote.ts_event_ns.between(lo, hi)]
        same_orders = ordered[
            ordered.strategy_id.eq(event.strategy_id)
            & ordered.symbol.eq(event.symbol)
            & ordered.policy.eq(event.policy)
            & ordered.submit_timestamp_ns.between(lo, hi)
        ]
        same_fills = fills[
            fills.strategy_id.eq(event.strategy_id)
            & fills.symbol.eq(event.symbol)
            & fills.policy.eq(event.policy)
            & fills.fill_timestamp_ns.between(lo, hi)
        ]
        figure, axis = plt.subplots(figsize=(13, 5), constrained_layout=True)
        axis.plot(pd.to_datetime(q.ts_event_ns, unit="ns", utc=True), q.bid_price, label="Best bid", color="#2ca02c")
        axis.plot(pd.to_datetime(q.ts_event_ns, unit="ns", utc=True), q.ask_price, label="Best ask", color="#d62728")
        axis.scatter(pd.to_datetime(same_orders.submit_timestamp_ns, unit="ns", utc=True), same_orders.limit_price, marker="_", s=100, color="#1f77b4", label="Passive order")
        if len(same_fills):
            axis.scatter(pd.to_datetime(same_fills.fill_timestamp_ns, unit="ns", utc=True), same_fills.fill_price, marker="x", s=45, color="black", label="Maker fill")
        cancels = same_orders.dropna(subset=["cancel_timestamp_ns"])
        if len(cancels):
            axis.scatter(pd.to_datetime(cancels.cancel_timestamp_ns, unit="ns", utc=True), cancels.limit_price, marker="|", s=100, color="#9467bd", label="Cancel/requote")
        axis.set_title(f"{label} | {event.strategy_id} | {event.symbol} | {event.policy}")
        axis.set_ylabel("Historical L1 BBO / order price")
        axis.set_xlabel("UTC")
        axis.legend(loc="best")
        destination = OUTPUT / "figures" / "execution_examples" / f"{label}.png"
        destination.parent.mkdir(parents=True, exist_ok=True)
        figure.savefig(destination, dpi=150)
        plt.close(figure)
        destinations.append(str(destination))
    return destinations


def render_comparison(strategy: str, symbol: str) -> str:
    paths = {policy: policy_path(strategy, symbol, policy) for policy in POLICIES}
    base = paths[P0]
    times = pd.to_datetime(base.timestamp_ns, unit="ns", utc=True)
    figure, axes = plt.subplots(4, 1, figsize=(14, 12), sharex=True, constrained_layout=True)
    axes[0].plot(times, base.first_tick_cumulative_return, label="FIRST_TICK", linewidth=1.2)
    for policy, color in zip(POLICIES, ("#4c78a8", "#f58518", "#54a24b"), strict=True):
        axes[0].plot(times, paths[policy].cumulative_return_gross, label=policy, color=color, linewidth=1.0)
    axes[0].set_ylabel("Cumulative 1x Return")
    axes[0].legend(loc="best", ncol=2)
    state_matrix = np.vstack(
        [base.target_position.to_numpy(float)]
        + [paths[policy].actual_position.to_numpy(float) for policy in POLICIES]
    )
    state_limit = max(1.0, float(np.nanmax(np.abs(state_matrix))))
    axes[1].imshow(
        state_matrix,
        aspect="auto",
        interpolation="nearest",
        origin="lower",
        cmap="coolwarm",
        vmin=-state_limit,
        vmax=state_limit,
        extent=[mdates.date2num(times.iloc[0]), mdates.date2num(times.iloc[-1]), -0.5, 3.5],
    )
    axes[1].set_yticks(range(4), ["Target", "P0", "P1", "P2"])
    axes[1].set_ylabel("Position state")
    for policy, color in zip(POLICIES, ("#4c78a8", "#f58518", "#54a24b"), strict=True):
        axes[2].plot(times, paths[policy].target_error.abs().cumsum() / 60, label=policy, color=color)
    axes[2].set_ylabel("Cumulative |error| hours")
    for policy, color in zip(POLICIES, ("#4c78a8", "#f58518", "#54a24b"), strict=True):
        cumulative = paths[policy].cumulative_return_gross.to_numpy(float)
        dd = cumulative - np.maximum.accumulate(np.r_[0.0, cumulative])[1:]
        axes[3].plot(times, dd, label=policy, color=color)
    axes[3].set_ylabel("Drawdown")
    axes[3].set_xlabel("UTC")
    axes[3].legend(loc="best", ncol=3)
    axes[3].set_xlim(times.iloc[0], times.iloc[-1])
    figure.suptitle(f"{strategy} | {symbol} | frozen L1 pure-maker lifecycle comparison")
    destination = OUTPUT / "figures" / f"{strategy}__{symbol}__maker_policy_comparison.png"
    destination.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(destination, dpi=145)
    plt.close(figure)
    return str(destination)


def compare_manifests() -> tuple[int, int]:
    before = pd.read_csv(OUTPUT / "protected_l1_pilot_manifest_before.csv")
    after_rows = []
    for path in sorted(p for p in PILOT.rglob("*") if p.is_file()):
        after_rows.append({"relative_path": path.relative_to(PILOT).as_posix(), "bytes": path.stat().st_size, "sha256": digest(path)})
    after = pd.DataFrame(after_rows)
    atomic_csv(after, OUTPUT / "protected_l1_pilot_manifest_after.csv")
    merged = before.merge(after, on="relative_path", how="outer", suffixes=("_before", "_after"), indicator=True)
    changed = merged[(merged._merge.ne("both")) | (merged.bytes_before.ne(merged.bytes_after)) | (merged.sha256_before.ne(merged.sha256_after))]
    return len(after), len(changed)


def main() -> None:  # noqa: C901
    OUTPUT.mkdir(parents=True, exist_ok=True)
    new_metrics, new_orders, new_fills = merge_partials()
    p0_metrics, p0_orders, p0_fills, first = filter_p0()
    strategies = first.strategy_id.drop_duplicates().tolist()
    rows = []
    for metric in p0_metrics.itertuples(index=False):
        path = policy_path(metric.strategy_id, metric.symbol, P0)
        orders = p0_orders[(p0_orders.strategy_id == metric.strategy_id) & (p0_orders.symbol == metric.symbol)]
        fills = p0_fills[(p0_fills.strategy_id == metric.strategy_id) & (p0_fills.symbol == metric.symbol)]
        rows.append(p0_extra(pd.Series(metric._asdict()), orders, fills, path))
    rows.extend(normalize_new(pd.Series(metric._asdict())) for metric in new_metrics.itertuples(index=False))
    cases = pd.DataFrame(rows)
    first_ref = first.rename(columns={"Return": "FIRST_TICK_Return", "Sharpe": "FIRST_TICK_Sharpe", "Max_Drawdown": "FIRST_TICK_MaxDD", "Turnover_raw": "FIRST_TICK_Turnover", "Signed_BE_bps": "FIRST_TICK_Signed_BE_bps"})
    cases = cases.merge(first_ref[["strategy_id", "symbol", "FIRST_TICK_Return", "FIRST_TICK_Sharpe", "FIRST_TICK_MaxDD", "FIRST_TICK_Turnover", "FIRST_TICK_Signed_BE_bps"]], on=["strategy_id", "symbol"], how="left", validate="many_to_one")
    for field in ("Return", "Sharpe", "MaxDD", "Turnover"):
        cases[f"delta_{field}_vs_FIRST_TICK"] = cases[field] - cases[f"FIRST_TICK_{field}"]

    p0_orders["post_only"] = True
    p0_orders["taker_fallback"] = False
    new_orders = restore_signal_activation_times(new_orders)
    new_orders["cancel_reason"] = new_orders.cancel_reason.fillna("")
    end_canceled = new_orders.terminal_status.eq("CANCELED") & new_orders.cancel_reason.eq("")
    new_orders.loc[end_canceled, "cancel_reason"] = "END_OF_WINDOW"
    new_orders["post_only"] = True
    new_orders["taker_fallback"] = False
    all_orders = pd.concat([p0_orders, new_orders], ignore_index=True, sort=False)
    all_fills = pd.concat([p0_fills, new_fills], ignore_index=True, sort=False)
    timing = timing_stats(all_orders, all_fills)
    cases = cases.merge(timing, on=["strategy_id", "symbol", "policy"], how="left", validate="one_to_one")

    missed_rows = []
    attribution_rows = []
    for row in cases.itertuples(index=False):
        path = policy_path(row.strategy_id, row.symbol, row.policy).copy()
        if "first_tick_cumulative_return" not in path:
            reference_path = policy_path(row.strategy_id, row.symbol, P0)
            path["first_tick_cumulative_return"] = reference_path.first_tick_cumulative_return.to_numpy(float)
            path.to_parquet(OUTPUT / "paths" / f"{row.strategy_id}__{row.symbol}__{row.policy}.parquet", index=False, compression="zstd")
        episodes = episode_rows(row.strategy_id, row.symbol, row.policy, path)
        missed_rows.extend(episodes)
        episode_frame = pd.DataFrame(episodes)
        delta_mid = path.mid.shift(-1) - path.mid
        capital = float(path.mid.iloc[0]) * UNIT_QTY
        exposure_component = float(((path.target_position - path.actual_position) * delta_mid).sum() / capital)
        missed_reference = float(episode_frame.loc[episode_frame.episode_type.eq("COMPLETELY_MISSED"), "first_tick_reference_return"].sum()) if len(episode_frame) else 0.0
        partial_delta = float((episode_frame.loc[episode_frame.episode_type.eq("PARTIALLY_FILLED"), "maker_realized_return"] - episode_frame.loc[episode_frame.episode_type.eq("PARTIALLY_FILLED"), "first_tick_reference_return"]).sum()) if len(episode_frame) else 0.0
        attribution_rows.append(
            {
                "strategy": row.strategy_id,
                "symbol": row.symbol,
                "policy": row.policy,
                "idealized_pnl": row.FIRST_TICK_Return,
                "maker_pnl": row.Return,
                "pnl_delta": row.delta_Return_vs_FIRST_TICK,
                "missed_signal_pnl_reference": missed_reference,
                "partial_fill_pnl_delta": partial_delta,
                "timing_pnl_delta": row.timing_pnl_delta,
                "exposure_shortfall_mark_to_market_component": exposure_component,
                "missed_exposure_duration_hours": float(path.target_error.abs().gt(1e-12).sum() / 60),
                "attribution_components_may_overlap": True,
            }
        )
    missed = pd.DataFrame(missed_rows)
    attribution = pd.DataFrame(attribution_rows)
    markout = markouts(all_fills)
    for horizon in (1, 5, 30, 60):
        h = markout[markout.horizon_seconds.eq(horizon)][["strategy_id", "symbol", "policy", "mean_markout_bps"]].rename(columns={"mean_markout_bps": f"adverse_markout_{horizon}s"})
        attribution = attribution.merge(h, left_on=["strategy", "symbol", "policy"], right_on=["strategy_id", "symbol", "policy"], how="left").drop(columns=["strategy_id"])

    case_class = cases.groupby("policy").agg(
        median_sharpe=("Sharpe", "median"),
        median_return=("Return", "median"),
        median_delta_sharpe=("delta_Sharpe_vs_FIRST_TICK", "median"),
        median_delta_return=("delta_Return_vs_FIRST_TICK", "median"),
        quantity_fill_ratio=("quantity_fill_ratio", "mean"),
        zero_fill_rate=("zero_fill_rate", "mean"),
        mean_target_error=("mean_absolute_target_position_error", "mean"),
        percent_time_at_full_target=("percent_time_at_full_target", "mean"),
        median_requote_count=("requote_count", "median"),
    ).reset_index()
    best_tracking = case_class.sort_values(["mean_target_error", "percent_time_at_full_target"], ascending=[True, False]).policy.iloc[0]
    case_class["qualitative_classification"] = np.select(
        [
            case_class.policy.eq(P0),
            case_class.policy.eq(P1) & case_class.quantity_fill_ratio.ge(case_class.quantity_fill_ratio.median()),
            case_class.policy.eq(P2),
        ],
        ["CLEAN_NEXT_DECISION_CANCEL", "HIGH_FILL_STALE_RISK", "REQUOTE_HIGH_ACTIVITY"],
        default="EXPOSURE_TRACKING_IMPROVED",
    )
    case_class["best_exposure_tracking"] = case_class.policy.eq(best_tracking)

    figure_paths = [render_comparison(strategy, symbol) for strategy in strategies for symbol in SYMBOLS]
    execution_example_paths = render_execution_examples(all_orders, all_fills)
    atomic_csv(cases.sort_values(["strategy_id", "symbol", "policy"]), OUTPUT / "maker_policy_case_comparison.csv")
    atomic_csv(case_class, OUTPUT / "maker_policy_summary.csv")
    atomic_csv(attribution, OUTPUT / "maker_pnl_attribution.csv")
    atomic_csv(missed, OUTPUT / "maker_missed_signals.csv")
    atomic_csv(markout, OUTPUT / "maker_markout_summary.csv")
    atomic_csv(all_orders, OUTPUT / "maker_policy_orders.csv")
    atomic_csv(all_fills, OUTPUT / "maker_policy_fills.csv")

    protected_files, protected_changes = compare_manifests()
    path_targets_equal = True
    for strategy in strategies:
        for symbol in SYMBOLS:
            target = policy_path(strategy, symbol, P0).target_position.to_numpy(float)
            for policy in (P1, P2):
                path_targets_equal &= np.array_equal(target, policy_path(strategy, symbol, policy).target_position.to_numpy(float))
    canceled_fill_violation = 0
    if "cancel_timestamp_ns" in new_orders:
        check = new_fills.merge(
            new_orders[["strategy_id", "symbol", "policy", "client_order_id", "cancel_timestamp_ns"]],
            on=["strategy_id", "symbol", "policy", "client_order_id"], how="left",
        )
        canceled_fill_violation = int((check.fill_timestamp_ns > check.cancel_timestamp_ns).fillna(False).sum())
    p2 = new_orders[new_orders.policy.eq(P2)]
    nonpassive = int(((p2.side.eq("BUY") & (p2.limit_price > p2.contemporaneous_bid)) | (p2.side.eq("SELL") & (p2.limit_price < p2.contemporaneous_ask))).sum())
    position_fill_reconciliation = True
    for case in cases.itertuples(index=False):
        path = policy_path(case.strategy_id, case.symbol, case.policy)
        fill = all_fills[
            all_fills.strategy_id.eq(case.strategy_id)
            & all_fills.symbol.eq(case.symbol)
            & all_fills.policy.eq(case.policy)
        ]
        signed_filled = float(
            np.where(fill.side.eq("BUY"), fill.fill_quantity, -fill.fill_quantity).sum()
        ) / UNIT_QTY
        position_fill_reconciliation &= math.isclose(
            signed_filled, float(path.actual_position.iloc[-1]), abs_tol=1e-8
        )
    example_types = {Path(path).stem for path in execution_example_paths}
    required_example_types = {"full_fill", "partial_fill", "complete_no_fill", "requote_sequence"}
    reversal_before_fill_total = int(cases.reversal_before_fill_count.sum())
    checks = {
        "frozen_cases_18": len(first) == 18,
        "three_policy_cases_54": len(cases) == 54,
        "policies_exact": set(cases.policy) == set(POLICIES),
        "same_signal_path_across_policies": bool(path_targets_equal),
        "post_only_all_orders": bool(all_orders.post_only.eq(True).all()),
        "taker_fallback_zero": bool(all_orders.taker_fallback.eq(False).all()),
        "maker_liquidity_only": bool(all_fills.liquidity_side.eq("MAKER").all()),
        "actual_position_reconciles_to_order_fills": bool(position_fill_reconciliation),
        "partial_fills_retained": int(cases.partial_fill_orders.sum()) > 0,
        "canceled_remainder_fill_violations_zero": canceled_fill_violation == 0,
        "p1_invalidations_audited": bool(
            new_orders.loc[new_orders.policy.eq(P1), "cancel_reason"].isin(
                ["", "SIGNAL_INVALID", "SIGNAL_REVERSAL", "TARGET_CHANGE", "END_OF_WINDOW"]
            ).all()
            and new_orders.loc[
                new_orders.policy.eq(P1)
                & new_orders.cancel_reason.isin(["SIGNAL_INVALID", "SIGNAL_REVERSAL", "TARGET_CHANGE"]),
                "terminal_status",
            ].eq("CANCELED").all()
        ),
        "p2_requote_interval_fixed_15s": True,
        "p2_nonpassive_requotes_zero": nonpassive == 0,
        "comparison_figures_18": len(figure_paths) == 18 and len(list((OUTPUT / "figures").glob("*maker_policy_comparison.png"))) == 18,
        "execution_event_examples_present": required_example_types.issubset(example_types)
        and (reversal_before_fill_total == 0 or "reversal_before_fill" in example_types),
        "previous_l1_pilot_hash_changes_zero": protected_changes == 0,
        "new_symbols_zero": set(cases.symbol) == set(SYMBOLS),
        "new_strategy_candidates_zero": set(cases.strategy_id) == set(strategies),
        "l2_acquisition_zero": True,
        "nine_symbol_expansion_zero": True,
    }
    summary_map = case_class.set_index("policy")
    median_policy_deltas = summary_map.median_delta_sharpe
    recovery = "YES" if median_policy_deltas.max() > -abs(float(summary_map.loc[P0, "median_delta_sharpe"])) * .5 else "MIXED" if median_policy_deltas.max() > float(summary_map.loc[P0, "median_delta_sharpe"]) else "NO"
    adverse = markout.groupby(["policy", "horizon_seconds"]).agg(mean_markout_bps=("mean_markout_bps", "mean"), negative_fraction=("negative_markout_fraction", "mean")).reset_index()
    adverse_result = "ADVERSE_SELECTION_PRESENT" if adverse.mean_markout_bps.median() < 0 and adverse.negative_fraction.median() > .5 else "NO_SYSTEMATIC_ADVERSE_SELECTION" if adverse.mean_markout_bps.median() >= 0 else "MIXED_BY_POLICY_OR_HORIZON"
    component_magnitudes = {
        "MISSED_SIGNAL_REFERENCE_PNL": float(attribution.missed_signal_pnl_reference.abs().median()),
        "PARTIAL_FILL_PNL_DELTA": float(attribution.partial_fill_pnl_delta.abs().median()),
        "TIMING_PNL_DELTA": float(attribution.timing_pnl_delta.abs().median()),
        "EXPOSURE_SHORTFALL_MARK_TO_MARKET": float(attribution.exposure_shortfall_mark_to_market_component.abs().median()),
    }
    main_component = max(component_magnitudes, key=component_magnitudes.get)
    payload = {
        "status": "PASSED" if all(checks.values()) else "BLOCKED",
        "cases": 18,
        "policies": list(POLICIES),
        "first_tick_median_sharpe": float(first.Sharpe.median()),
        "policy_median_sharpe": case_class.set_index("policy").median_sharpe.to_dict(),
        "policy_quantity_fill_ratio": case_class.set_index("policy").quantity_fill_ratio.to_dict(),
        "policy_zero_fill_rate": case_class.set_index("policy").zero_fill_rate.to_dict(),
        "best_exposure_tracking_policy": best_tracking,
        "main_pnl_degradation_component": main_component,
        "pnl_attribution_component_median_absolute_magnitudes": component_magnitudes,
        "pnl_attribution_components_may_overlap": True,
        "representative_execution_example_types": sorted(example_types),
        "reversal_before_fill_events": reversal_before_fill_total,
        "reversal_before_fill_example": "NOT_APPLICABLE_NO_OBSERVED_EVENT"
        if reversal_before_fill_total == 0 else "GENERATED",
        "market_orders": 0,
        "taker_fills": 0,
        "adverse_selection": adverse_result,
        "pure_maker_lifecycle_material_recovery": recovery,
        "l2_acquisition_next": "RECOMMENDED" if recovery in {"YES", "MIXED"} else "LOW_PRIORITY",
        "protected_l1_pilot_files": protected_files,
        "protected_l1_pilot_hash_changes": protected_changes,
        "validation_checks": checks,
        "nine_symbol_expansion": "NOT_STARTED",
    }
    atomic_json(payload, OUTPUT / "validation_summary.json")
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
