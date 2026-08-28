#!/usr/bin/env python3
"""Phase 6D exchange-execution realism falsification.

This is an additive research overlay.  It consumes the frozen Phase 6C signal
and result streams, applies Binance USD-M quantity constraints and the already
validated fee/lag contracts, and never modifies strategy or market-data code.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import os
import shutil
import zipfile
from dataclasses import dataclass
from decimal import Decimal, ROUND_FLOOR
from pathlib import Path
from typing import Any

import matplotlib as mpl

mpl.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from results.trade_episode import build_de_risk_episodes
from scripts.internal.build_phase4a_baseline_evaluation import drawdown
from scripts.internal.build_phase4b_cost_episode_audit import exact_be
from scripts.internal.run_phase4c_cross_symbol import load_symbol
from scripts.internal.run_phase6c_conditional_replication import compare_snapshots
from scripts.internal.run_phase6c_conditional_replication import protected_snapshot


ROOT = Path(__file__).resolve().parents[2]
PHASE6C_LOCAL = ROOT / "outputs/baseline_evaluation/phase6c"
PHASE6C_DELIVERED = (
    ROOT / "outputs/deliverables/phase6c_cross_symbol_falsification"
    / "phase6c_cross_symbol_falsification"
)
OUTPUT = ROOT / "outputs/baseline_evaluation/phase6d"
DELIVERABLES = ROOT / "outputs/deliverables"
SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
CAPITALS = (1_000.0, 10_000.0, 100_000.0, 1_000_000.0)
HEADLINE_CAPITAL = 100_000.0
SCENARIOS = (
    "E0_CONTINUOUS_BASELINE",
    "E1_EXCHANGE_QUANTITY_ROUNDING",
    "E2_QUANTITY_PLUS_PRICE_VALIDATION",
    "E3_PLUS_EXPLICIT_FEES",
    "E4_PLUS_EXISTING_REALISTIC_LAG",
)
FEE_PROFILES = {"FEE0": 0.0, "VIP9_TAKER": 1.7, "VIP0_TAKER": 5.0}
PRIMARY_FEE = "VIP0_TAKER"
TOL = 1e-10


@dataclass(frozen=True)
class InstrumentRule:
    symbol: str
    tick_size: Decimal
    step_size: Decimal
    min_qty: Decimal
    max_qty: Decimal
    min_notional: Decimal
    price_precision: int
    quantity_precision: int
    status: str

    @property
    def tick(self) -> float:
        return float(self.tick_size)

    @property
    def step(self) -> float:
        return float(self.step_size)


def atomic_csv(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    frame.to_csv(temporary, index=False, encoding="utf-8-sig")
    os.replace(temporary, path)


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def phase6d_protected_snapshot(source_root: Path) -> dict[str, Any]:
    """Extend the existing protected set with delivered prior-phase archives and Phase 6C."""
    snapshot = protected_snapshot()
    extra: list[Path] = []
    extra.extend(
        path for path in DELIVERABLES.glob("*.zip")
        if path.is_file() and not path.name.startswith("phase6d_")
    )
    extra.extend(path for path in source_root.rglob("*") if path.is_file())
    for path in sorted(set(extra)):
        relative = path.relative_to(ROOT).as_posix()
        snapshot["files"][relative] = {"size": path.stat().st_size, "sha256": sha256(path)}
    digest = hashlib.sha256()
    for name, metadata in sorted(snapshot["files"].items()):
        digest.update(f"{name}\0{metadata['size']}\0{metadata['sha256']}\n".encode())
    snapshot["content_file_count"] = len(snapshot["files"])
    snapshot["content_digest"] = digest.hexdigest()
    return snapshot


def phase6c_root() -> Path:
    for root in (PHASE6C_LOCAL, PHASE6C_DELIVERED):
        if (root / "phase6c_cross_symbol_master.csv").is_file():
            return root
    raise FileNotFoundError("Phase 6C canonical artifacts not found")


def parse_exchange_info(path: Path) -> tuple[dict[str, InstrumentRule], pd.DataFrame]:
    source = json.loads(path.read_text(encoding="utf-8"))
    by_symbol = {row["symbol"]: row for row in source["symbols"]}
    rules: dict[str, InstrumentRule] = {}
    rows: list[dict[str, Any]] = []
    for symbol in SYMBOLS:
        raw = by_symbol[symbol]
        filters = {item["filterType"]: item for item in raw["filters"]}
        quantity = filters.get("MARKET_LOT_SIZE") or filters["LOT_SIZE"]
        price = filters["PRICE_FILTER"]
        notional = filters.get("MIN_NOTIONAL", {})
        rule = InstrumentRule(
            symbol=symbol,
            tick_size=Decimal(str(price["tickSize"])),
            step_size=Decimal(str(quantity["stepSize"])),
            min_qty=Decimal(str(quantity["minQty"])),
            max_qty=Decimal(str(quantity["maxQty"])),
            min_notional=Decimal(str(notional.get("notional", 0))),
            price_precision=int(raw["pricePrecision"]),
            quantity_precision=int(raw["quantityPrecision"]),
            status=str(raw["status"]),
        )
        rules[symbol] = rule
        rows.append({
            "symbol": symbol,
            "instrument_type": raw.get("contractType", "PERPETUAL"),
            "status": rule.status,
            "tickSize": str(rule.tick_size),
            "stepSize": str(rule.step_size),
            "minQty": str(rule.min_qty),
            "maxQty": str(rule.max_qty),
            "minNotional": str(rule.min_notional),
            "pricePrecision": rule.price_precision,
            "quantityPrecision": rule.quantity_precision,
            "quantity_filter": "MARKET_LOT_SIZE",
            "source": "Binance USD-M official GET /fapi/v1/exchangeInfo",
            "source_snapshot_sha256": sha256(path),
            "source_server_time": source.get("serverTime"),
        })
    return rules, pd.DataFrame(rows)


def round_quantity_toward_zero(quantity: float, step: Decimal) -> float:
    """Conservative sign-safe step rounding; never increases magnitude."""
    if not math.isfinite(quantity):
        raise ValueError("quantity must be finite")
    magnitude = Decimal(str(abs(quantity)))
    units = (magnitude / step).to_integral_value(rounding=ROUND_FLOOR)
    rounded = float(units * step)
    return math.copysign(rounded, quantity) if rounded else 0.0


def price_is_legal(price: float, tick: float, tolerance: float = 1e-7) -> bool:
    units = price / tick
    return math.isfinite(units) and abs(units - round(units)) <= tolerance


def order_legality(delta_qty: float, price: float, rule: InstrumentRule) -> tuple[bool, str]:
    magnitude = abs(delta_qty)
    if magnitude <= TOL:
        return True, "NO_ORDER"
    if magnitude + TOL < float(rule.min_qty):
        return False, "ORDER_REJECTED_MIN_QTY"
    if magnitude - TOL > float(rule.max_qty):
        return False, "ORDER_REJECTED_MAX_QTY"
    if magnitude * price + TOL < float(rule.min_notional):
        return False, "ORDER_REJECTED_MIN_NOTIONAL"
    units = magnitude / rule.step
    if abs(units - round(units)) > 1e-6:
        return False, "ORDER_REJECTED_ILLEGAL_STEP"
    return True, "EXECUTABLE"


def _quantiles(values: list[float]) -> tuple[float, float, float, float]:
    if not values:
        return math.nan, math.nan, math.nan, math.nan
    array = np.asarray(values, dtype=np.float64)
    return tuple(float(np.quantile(array, q)) for q in (.5, .95, .99, 1.0))


def simulate_exchange_mechanics(
    *,
    event_time_ns: np.ndarray,
    direction: np.ndarray,
    market_open: np.ndarray,
    close: np.ndarray,
    quote_volume: np.ndarray,
    funding: pd.DataFrame,
    capital: float,
    rule: InstrumentRule,
    trace_limit: int = 5,
    initial_quantity: float = 0.0,
    previous_close_price: float | None = None,
) -> tuple[pd.DataFrame, dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """Re-account one frozen signal path using legal market-order quantities."""
    n = len(direction)
    if not (len(event_time_ns) == len(market_open) == len(close) == len(quote_volume) == n):
        raise ValueError("mechanics inputs must align")
    desired = np.sign(direction).astype(np.int8)
    close_qty = np.zeros(n, dtype=np.float64)
    price_return = np.zeros(n, dtype=np.float64)
    turnover = np.zeros(n, dtype=np.float64)
    exposure_error = np.zeros(n, dtype=np.float64)
    traces: list[dict[str, Any]] = []
    exceptions: list[dict[str, Any]] = []
    participation: list[float] = []
    previous_qty = float(initial_quantity)
    previous_close = (
        float(previous_close_price)
        if previous_close_price is not None
        else float(market_open[0])
    )
    requested = executed = rejected = 0
    rejects = {"ORDER_REJECTED_MIN_QTY": 0, "ORDER_REJECTED_MIN_NOTIONAL": 0,
               "ORDER_REJECTED_MAX_QTY": 0, "ORDER_REJECTED_ILLEGAL_STEP": 0}
    dust_events = 0
    adds = reductions = reversals = 0
    max_executed_order = 0.0
    raw_open_all = desired.astype(np.float64) * capital / market_open
    raw_close_all = desired.astype(np.float64) * capital / close
    step = rule.step
    target_open_all = np.sign(raw_open_all) * np.floor(np.abs(raw_open_all) / step + 1e-12) * step
    target_close_all = np.sign(raw_close_all) * np.floor(np.abs(raw_close_all) / step + 1e-12) * step

    def execute_delta(
        *, index: int, target: float, current: float, price: float, leg: str
    ) -> tuple[float, float]:
        nonlocal requested, executed, rejected, adds, reductions, reversals, max_executed_order
        delta = target - current
        if abs(delta) <= TOL:
            return current, 0.0
        requested += 1
        legal, reason = order_legality(delta, price, rule)
        if not legal:
            rejected += 1
            rejects[reason] += 1
            if len(exceptions) < 500:
                exceptions.append({
                    "timestamp": pd.Timestamp(event_time_ns[index], unit="ns", tz="UTC").isoformat(),
                    "exception_type": reason, "leg": leg,
                    "current_quantity": current, "requested_target_quantity": target,
                    "requested_delta_quantity": delta, "price": price,
                    "requested_notional": abs(delta) * price,
                })
            return current, 0.0
        executed += 1
        old_abs, new_abs = abs(current), abs(target)
        if old_abs > TOL and new_abs > TOL and math.copysign(1, current) != math.copysign(1, target):
            reversals += 1
        elif new_abs > old_abs + TOL:
            adds += 1
        elif new_abs < old_abs - TOL:
            reductions += 1
        notional = abs(delta) * price
        max_executed_order = max(max_executed_order, abs(delta))
        if quote_volume[index] > 0 and math.isfinite(quote_volume[index]):
            participation.append(notional / quote_volume[index])
        if len(traces) < trace_limit:
            traces.append({
                "timestamp": pd.Timestamp(event_time_ns[index], unit="ns", tz="UTC").isoformat(),
                "leg": leg, "signal_target": int(desired[index]),
                "desired_notional": float(desired[index] * capital),
                "raw_quantity": float(desired[index] * capital / price),
                "rounded_target_quantity": target,
                "quantity_before": current, "filled_delta_quantity": delta,
                "requested_price": price, "legal_price": price,
                "liquidity_role": "TAKER", "fee_profile": PRIMARY_FEE,
                "fee_usdt": notional * FEE_PROFILES[PRIMARY_FEE] / 10_000.0,
                "target_exposure": float(desired[index]),
                "executed_exposure": target * price / capital,
                "exposure_error": target * price / capital - desired[index],
                "resulting_position": target,
            })
        return target, notional

    for i in range(n):
        sign = float(desired[i])
        raw_open = raw_open_all[i]
        target_open = target_open_all[i]
        open_qty, open_notional = execute_delta(
            index=i, target=target_open, current=previous_qty, price=market_open[i], leg="OPEN"
        )
        gap_pnl = previous_qty * (market_open[i] - previous_close)
        intrabar_pnl = open_qty * (close[i] - market_open[i])
        raw_close = raw_close_all[i]
        target_close = target_close_all[i]
        final_qty, close_notional = execute_delta(
            index=i, target=target_close, current=open_qty, price=close[i], leg="CLOSE"
        )
        close_qty[i] = final_qty
        price_return[i] = (gap_pnl + intrabar_pnl) / capital
        turnover[i] = (open_notional + close_notional) / capital
        exposure_error[i] = final_qty * close[i] / capital - sign
        if sign == 0.0 and abs(final_qty) > TOL:
            dust_events += 1
        previous_qty = final_qty
        previous_close = close[i]

    funding_return = np.zeros(n, dtype=np.float64)
    if not funding.empty:
        funding_ts = funding.event_time_ns.to_numpy(np.int64)
        funding_mark = funding.mark_price.to_numpy(float)
        funding_rate = funding.funding_rate.to_numpy(float)
        held_index = np.searchsorted(event_time_ns, funding_ts, side="right") - 1
        report_index = np.searchsorted(event_time_ns, funding_ts, side="left")
        valid = (held_index >= 0) & (report_index < n)
        # Binance Vision's archived funding-rate partitions carry the settled
        # rate but may omit mark price.  The established account convention is
        # then to use the latest available contract market price, not zero.
        effective_mark = np.where(
            funding_mark[valid] > 0.0,
            funding_mark[valid],
            close[held_index[valid]],
        )
        payments = -close_qty[held_index[valid]] * effective_mark * funding_rate[valid]
        np.add.at(funding_return, report_index[valid], payments / capital)

    gross_return = price_return + funding_return
    actual_exposure = close_qty * close / capital
    p50, p95_part, p99_part, max_part = _quantiles(participation)
    abs_error = np.abs(exposure_error)
    metrics = {
        "price_Return": float(price_return.sum()),
        "funding_Return": float(funding_return.sum()),
        "gross_Return": float(gross_return.sum()),
        "executed_turnover": float(turnover.sum()),
        "gross_BE_bps": exact_be(float(gross_return.sum()), float(turnover.sum())),
        "requested_order_count": requested,
        "executed_order_count": executed,
        "rejected_order_count": rejected,
        "minQty_rejects": rejects["ORDER_REJECTED_MIN_QTY"],
        "minNotional_rejects": rejects["ORDER_REJECTED_MIN_NOTIONAL"],
        "maxQty_rejects": rejects["ORDER_REJECTED_MAX_QTY"],
        "quantity_legality_violations": rejects["ORDER_REJECTED_ILLEGAL_STEP"],
        "dust_events": dust_events,
        "adds": adds, "partial_reductions": reductions, "reversals": reversals,
        "mean_abs_exposure_error": float(abs_error.mean()),
        "P95_abs_exposure_error": float(np.quantile(abs_error, .95)),
        "max_abs_exposure_error": float(abs_error.max()),
        "participation_median": p50, "participation_P95": p95_part,
        "participation_P99": p99_part, "participation_max": max_part,
        "raw_quantity_abs_median": float(np.median(np.r_[np.abs(raw_open_all), np.abs(raw_close_all)])),
        "rounded_quantity_abs_median": float(np.median(np.r_[np.abs(target_open_all), np.abs(target_close_all)])),
        "quantity_rounding_error_abs_mean": float(np.mean(np.r_[np.abs(target_open_all - raw_open_all), np.abs(target_close_all - raw_close_all)])),
        "quantity_rounding_error_abs_max": float(np.max(np.r_[np.abs(target_open_all - raw_open_all), np.abs(target_close_all - raw_close_all)])),
        "max_executed_order_quantity": max_executed_order,
        "signal_action_count": int(np.count_nonzero(np.diff(desired, prepend=0))),
        "missed_entry_count": int(np.sum((desired != 0) & (np.sign(actual_exposure) == 0))),
        "delayed_flat_count": int(np.sum((desired == 0) & (np.abs(actual_exposure) > TOL))),
        "signal_stream_sha256": hashlib.sha256(desired.tobytes()).hexdigest(),
        "_gross_return_array": gross_return,
        "_turnover_array": turnover,
    }
    frame = pd.DataFrame({
        "event_time_ns": event_time_ns,
        "desired_direction": desired,
        "executed_quantity": close_qty,
        "executed_exposure": actual_exposure,
        "price_return": price_return,
        "funding_return": funding_return,
        "gross_return": gross_return,
        "turnover": turnover,
    })
    return frame, metrics, traces, exceptions


def e0_metrics(source: pd.DataFrame, master: pd.Series) -> dict[str, Any]:
    gross = source.total_return.to_numpy(float)
    turnover = source.turnover.to_numpy(float)
    return {
        "price_Return": math.nan,
        "funding_Return": math.nan,
        "gross_Return": float(gross.sum()),
        "executed_turnover": float(turnover.sum()),
        "gross_BE_bps": exact_be(float(gross.sum()), float(turnover.sum())),
        "MDD": drawdown(gross),
        "episode_count": int(master.Episode_Count),
        "requested_order_count": math.nan, "executed_order_count": math.nan,
        "rejected_order_count": 0, "minQty_rejects": 0, "minNotional_rejects": 0,
        "maxQty_rejects": 0, "quantity_legality_violations": 0, "dust_events": 0,
        "adds": math.nan, "partial_reductions": math.nan, "reversals": math.nan,
        "mean_abs_exposure_error": 0.0, "P95_abs_exposure_error": 0.0,
        "max_abs_exposure_error": 0.0, "participation_median": math.nan,
        "participation_P95": math.nan, "participation_P99": math.nan,
        "participation_max": math.nan, "signal_action_count": math.nan,
        "missed_entry_count": 0, "delayed_flat_count": 0,
        "signal_stream_sha256": hashlib.sha256(np.sign(source.direction.to_numpy(float)).astype(np.int8).tobytes()).hexdigest(),
    }


def row_for_scenario(
    *, strategy: str, group: str, symbol: str, provenance: str, phase6b: str,
    phase6c_label: str, warnings: str, capital: float, scenario: str,
    fee_profile: str, metrics: dict[str, Any], desired_turnover: float,
    status: str = "COMPLETED",
) -> dict[str, Any]:
    fee_bps = FEE_PROFILES[fee_profile]
    turnover = float(metrics["executed_turnover"])
    fee_return = -turnover * fee_bps / 10_000.0
    gross_return = float(metrics["gross_Return"])
    net = gross_return + fee_return
    if "_gross_return_array" in metrics:
        net_increments = (
            np.asarray(metrics["_gross_return_array"], dtype=np.float64)
            - np.asarray(metrics["_turnover_array"], dtype=np.float64) * fee_bps / 10_000.0
        )
        net_mdd = drawdown(net_increments)
    else:
        net_mdd = metrics.get("MDD", math.nan)
    return {
        "strategy_id": strategy, "semantic_group_id": group, "symbol": symbol,
        "provenance": provenance, "Phase6B_label": phase6b, "Phase6C_label": phase6c_label,
        "warnings": warnings, "capital": capital, "execution_scenario": scenario,
        "fee_profile": fee_profile, "liquidity_role": "TAKER" if fee_bps else "NONE",
        "Return": net, "MDD": net_mdd,
        "price_Return": metrics.get("price_Return", math.nan),
        "funding_Return": metrics.get("funding_Return", math.nan),
        "transaction_fee_Return": fee_return, "execution_friction_Return": 0.0,
        "desired_turnover": desired_turnover, "executed_turnover": turnover,
        "gross_BE_bps": metrics["gross_BE_bps"], "effective_fee_bps": fee_bps,
        "residual_BE_margin_bps": metrics["gross_BE_bps"] - fee_bps,
        "episode_count": metrics.get("episode_count", math.nan),
        "requested_order_count": metrics["requested_order_count"],
        "executed_order_count": metrics["executed_order_count"],
        "rejected_order_count": metrics["rejected_order_count"],
        "signal_action_count": metrics["signal_action_count"],
        "partial_reductions": metrics.get("partial_reductions", math.nan),
        "adds": metrics.get("adds", math.nan), "reversals": metrics.get("reversals", math.nan),
        "mean_exposure_error": metrics["mean_abs_exposure_error"],
        "P95_exposure_error": metrics["P95_abs_exposure_error"],
        "max_exposure_error": metrics["max_abs_exposure_error"],
        "dust_events": metrics["dust_events"],
        "participation_median": metrics["participation_median"],
        "participation_P95": metrics["participation_P95"],
        "participation_P99": metrics["participation_P99"],
        "status": status,
    }


def render_figures(output: Path, master: pd.DataFrame, discretization: pd.DataFrame) -> None:
    root = output / "figures"; root.mkdir(parents=True, exist_ok=True)
    headline = master[(master.capital == HEADLINE_CAPITAL) & (master.fee_profile == "FEE0")]
    e0 = headline[headline.execution_scenario == SCENARIOS[0]].set_index(["strategy_id", "symbol"])
    e1 = headline[headline.execution_scenario == SCENARIOS[1]].set_index(["strategy_id", "symbol"])
    labels = [f"{a}/{b.replace('USDT','')}" for a, b in e0.index]
    x = np.arange(len(labels))
    fig, ax = plt.subplots(figsize=(16, 6)); ax.plot(x, e0.Return, "o", label="Continuous E0"); ax.plot(x, e1.Return, "x", label="Quantity-rounded E1")
    ax.axhline(0, color="black", lw=.8); ax.set_xticks(x, labels, rotation=70, ha="right", fontsize=7); ax.set_ylabel("Final Return (1x)"); ax.legend(); ax.grid(alpha=.2); fig.tight_layout(); fig.savefig(root / "01_continuous_vs_rounded_return.png", dpi=160); plt.close(fig)

    primary = master[(master.capital == HEADLINE_CAPITAL) & (master.execution_scenario == SCENARIOS[4]) & (master.fee_profile == PRIMARY_FEE)].copy()
    labels = [f"{a}/{b.replace('USDT','')}" for a, b in zip(primary.strategy_id, primary.symbol, strict=True)]
    x = np.arange(len(primary))
    fig, ax = plt.subplots(figsize=(16, 6)); ax.bar(x - .18, primary.gross_BE_bps, .36, label="Gross BE"); ax.bar(x + .18, primary.effective_fee_bps, .36, label="Explicit taker fee"); ax.set_xticks(x, labels, rotation=70, ha="right", fontsize=7); ax.set_ylabel("bps of turnover"); ax.legend(); fig.tight_layout(); fig.savefig(root / "02_gross_be_vs_fee.png", dpi=160); plt.close(fig)
    fig, ax = plt.subplots(figsize=(16, 6)); colors = np.where(primary.residual_BE_margin_bps > 0, "#2ca25f", "#de2d26"); ax.bar(x, primary.residual_BE_margin_bps, color=colors); ax.axhline(0, color="black", lw=.8); ax.set_xticks(x, labels, rotation=70, ha="right", fontsize=7); ax.set_ylabel("Residual BE Margin (bps)"); fig.tight_layout(); fig.savefig(root / "03_residual_be_margin.png", dpi=160); plt.close(fig)

    for column, ylabel, filename in (("mean_abs_exposure_error", "Mean absolute exposure error", "04_capital_vs_exposure_error.png"), ("net_Return_primary_fee", "Net Return (1x), VIP0 taker", "05_capital_vs_net_return.png")):
        fig, ax = plt.subplots(figsize=(11, 7))
        for (strategy, symbol), child in discretization.groupby(["strategy_id", "symbol"]):
            ax.plot(child.capital, child[column], alpha=.45, lw=.8)
        ax.set_xscale("log"); ax.set_xlabel("Research capital (USDT)"); ax.set_ylabel(ylabel); ax.grid(alpha=.2); fig.tight_layout(); fig.savefig(root / filename, dpi=160); plt.close(fig)

    fig, ax = plt.subplots(figsize=(13, 6)); ax.scatter(np.arange(len(primary)), primary.participation_P95, s=18); ax.set_yscale("log"); ax.set_xticks(np.arange(len(primary)), labels, rotation=70, ha="right", fontsize=7); ax.set_ylabel("P95 order notional / source bar quote volume"); fig.tight_layout(); fig.savefig(root / "06_order_participation.png", dpi=160); plt.close(fig)

    pivot = primary.pivot(index="strategy_id", columns="symbol", values="Return").reindex(columns=SYMBOLS)
    fig, ax = plt.subplots(figsize=(7, 6)); image = ax.imshow(pivot.to_numpy(float), cmap="RdYlGn", aspect="auto"); ax.set_xticks(range(3), [x.replace("USDT", "") for x in SYMBOLS]); ax.set_yticks(range(len(pivot)), pivot.index, fontsize=8); fig.colorbar(image, ax=ax, label="Net Return (1x), VIP0 taker"); fig.tight_layout(); fig.savefig(root / "07_execution_survival_heatmap.png", dpi=160); plt.close(fig)


def package(output: Path) -> tuple[Path, str, int, int]:
    target = DELIVERABLES / "phase6d_execution_realism.zip"
    temporary = target.with_suffix(".zip.tmp")
    with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(output.rglob("*")):
            if path.is_file() and not path.name.endswith(".tmp") and path.name != "phase6d_delivery.json":
                archive.write(path, Path("phase6d_execution_realism") / path.relative_to(output))
    os.replace(temporary, target)
    with zipfile.ZipFile(target) as archive:
        bad = archive.testzip(); members = len(archive.infolist())
    if bad:
        raise RuntimeError(f"ZIP integrity failed: {bad}")
    return target, sha256(target), members, target.stat().st_size


def write_review(output: Path, summary: pd.DataFrame, counts: dict[str, int], slippage: str) -> None:
    rows = "".join(
        f"<tr><td>{html.escape(str(r.strategy_id))}</td><td>{html.escape(str(r.Phase6D_status))}</td><td>{html.escape(str(r.warnings))}</td></tr>"
        for r in summary.itertuples(index=False)
    )
    document = f"""<!doctype html><meta charset='utf-8'><title>Phase 6D Execution Realism</title>
<style>body{{font-family:system-ui;margin:2rem;max-width:1200px}}table{{border-collapse:collapse;width:100%}}th,td{{border:1px solid #ccc;padding:.4rem;text-align:left}}code{{background:#eee;padding:.1rem .3rem}}</style>
<h1>Phase 6D — Exchange Execution Realism</h1>
<p>Candidates: 7 | Markets: 3 | headline capital: 100,000 USDT</p>
<p>Survive quantity rounding on BTC/ETH/SOL: {counts['rounding']} | survive VIP0 taker fee on all markets: {counts['fees']}</p>
<p>Execution resilient: {counts['resilient']} | conditional: {counts['conditional']}</p>
<p>Slippage empirically modeled: NO — <code>{slippage}</code></p>
<h2>Candidate status</h2><table><tr><th>Strategy</th><th>Status</th><th>Warnings</th></tr>{rows}</table>
<h2>Figures</h2>{''.join(f"<p><img src='figures/{p.name}' style='max-width:100%'></p>" for p in sorted((output/'figures').glob('*.png')))}
"""
    temporary = output / "phase6d_execution_realism_review.html.tmp"
    temporary.write_text(document, encoding="utf-8")
    os.replace(temporary, output / "phase6d_execution_realism_review.html")


def main() -> int:  # noqa: C901 - one explicit frozen research workflow
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market-root", type=Path, default=ROOT / "historical_data/market_data")
    parser.add_argument("--exchange-info", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, default=OUTPUT)
    args = parser.parse_args(); output = args.output_root; output.mkdir(parents=True, exist_ok=True)
    source_root = phase6c_root()
    before = phase6d_protected_snapshot(source_root); atomic_json(output / "phase6d_protected_hashes_before.json", before)
    rules, metadata = parse_exchange_info(args.exchange_info)
    atomic_csv(output / "phase6d_instrument_execution_metadata.csv", metadata)
    shutil.copy2(args.exchange_info, output / "binance_usdm_exchange_info_snapshot.json")

    audit = {
        "status": "AUDITED", "source_phase": "Phase 6C",
        "target_position": {"classification": "CONTINUOUS_RESEARCH_APPROXIMATION", "definition": "sign × fixed notional / each bar open and close"},
        "quantity": {"classification": "CONTINUOUS_RESEARCH_APPROXIMATION", "precision": "binary float", "step_rounding": False},
        "signal_price": {"classification": "REALISTIC", "definition": "historical 1m market open/close"},
        "generated_order_price": {"classification": "UNMODELLED", "reason": "market-like immediate execution has no generated limit price"},
        "latency": {"classification": "SIMPLIFIED", "definition": "canonical completed-bar lag1m; no stochastic physical latency"},
        "transaction_fee": {"classification": "SIMPLIFIED", "phase6c_primary": "fee zero", "configured_profiles": FEE_PROFILES},
        "funding": {"classification": "REALISTIC", "definition": "canonical Binance funding-rate stream; payment from held position"},
        "slippage": {"classification": "UNMODELLED", "phase6c_bps": 0.0},
        "partial_fill": {"classification": "UNMODELLED", "assumption": "legal orders fill completely"},
        "minimum_quantity": {"classification": "UNMODELLED"}, "minimum_notional": {"classification": "UNMODELLED"},
        "position_averaging": {"classification": "SIMPLIFIED", "definition": "net quantity; no lot-level cost basis"},
        "reversal": {"classification": "SIMPLIFIED", "definition": "immediate full net-position transition"},
        "turnover": {"classification": "REALISTIC_ACCOUNTING", "definition": "sum absolute executed notional changes / capital; reversal pays full transition"},
        "phase6d_mode": "additive execution-research overlay; existing execution path unchanged",
    }
    atomic_json(output / "phase6d_current_execution_model_audit.json", audit)
    fee_audit = pd.DataFrame([
        {"fee_profile": name, "maker_bps": math.nan, "taker_bps": bps,
         "source_config_path": "scripts/internal/run_constant_notional_overlay.py; scripts/internal/build_all_strategy_timeframe_lag.py",
         "version": "repository canonical configured profile", "liquidity_role": "TAKER" if bps else "NONE",
         "notes": "maker schedule is not used because historical execution is immediate/marketable"}
        for name, bps in FEE_PROFILES.items()
    ])
    atomic_csv(output / "phase6d_fee_schedule_audit.csv", fee_audit)
    slippage_rows = [{"symbol": symbol, "data_available": False, "data_type": "bar/funding/trade only; no historical bid-ask/depth partition", "coverage": "none for empirical spread/depth model", "model_permitted": False, "reason": "SLIPPAGE_NOT_EMPIRICALLY_MODELLED"} for symbol in SYMBOLS]
    atomic_csv(output / "phase6d_slippage_data_availability.csv", pd.DataFrame(slippage_rows))
    atomic_csv(output / "phase6d_slippage_audit.csv", pd.DataFrame(slippage_rows))

    p6master = pd.read_csv(source_root / "phase6c_cross_symbol_master.csv")
    candidates = pd.read_csv(source_root / "phase6c_phase6d_candidates.csv")
    candidates = candidates[candidates.phase6d_high_priority.astype(bool)].copy()
    if len(candidates) != 7:
        raise ValueError(f"expected 7 high-priority candidates, got {len(candidates)}")
    selected = set(candidates.strategy_id)
    p6master = p6master[p6master.representative_strategy_id.isin(selected)].copy()
    if len(p6master) != 21:
        raise ValueError(f"expected 21 strategy/symbol Phase6C rows, got {len(p6master)}")

    bars_by_symbol: dict[str, tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, pd.DataFrame]] = {}
    for symbol in SYMBOLS:
        bars, funding, _ = load_symbol(args.market_root, symbol)
        bars_by_symbol[symbol] = (
            np.fromiter((b.event_time_ns for b in bars), dtype=np.int64),
            np.fromiter((b.open for b in bars), dtype=float),
            np.fromiter((b.close for b in bars), dtype=float),
            np.fromiter(((b.quote_volume or 0.0) for b in bars), dtype=float),
            funding,
        )

    master_rows: list[dict[str, Any]] = []
    discretization_rows: list[dict[str, Any]] = []
    trace_rows: list[dict[str, Any]] = []
    exception_rows: list[dict[str, Any]] = []
    e0_residuals: list[dict[str, Any]] = []
    signal_hashes: dict[tuple[str, str], set[str]] = {}
    episode_summaries: dict[tuple[str, str, float], dict[str, Any]] = {}

    for source_row in p6master.itertuples(index=False):
        strategy, symbol = source_row.representative_strategy_id, source_row.symbol
        path = Path(str(source_row.physical_run_id)) / "timeseries.parquet"
        source = pd.read_parquet(path)
        times, opens, closes, quote_volume, funding = bars_by_symbol[symbol]
        if not np.array_equal(source.event_time_ns.to_numpy(np.int64), times):
            raise ValueError(f"{strategy}/{symbol}: market/result timestamps differ")
        direction = source.direction.to_numpy(float)
        e0 = e0_metrics(source, pd.Series(source_row._asdict()))
        e0_residuals.append({
            "strategy_id": strategy, "symbol": symbol,
            "Return_residual": e0["gross_Return"] - float(source_row.Return),
            "Turnover_residual": e0["executed_turnover"] - float(source_row.Turnover),
            "BE_residual": e0["gross_BE_bps"] - float(source_row.BE),
            "MDD_residual": e0["MDD"] - float(source_row.MDD),
            "episode_count_residual": e0["episode_count"] - int(source_row.Episode_Count),
        })
        candidate = candidates.set_index("strategy_id").loc[strategy]
        phase6c_label = candidate.replication_label
        desired_turnover = float(source_row.Turnover)
        for capital in CAPITALS:
            master_rows.append(row_for_scenario(
                strategy=strategy, group=source_row.semantic_group_id, symbol=symbol,
                provenance=source_row.provenance_tier, phase6b=source_row.phase6b_label,
                phase6c_label=phase6c_label, warnings=candidate.warnings, capital=capital,
                scenario=SCENARIOS[0], fee_profile="FEE0", metrics=e0,
                desired_turnover=desired_turnover,
            ))
            mechanics, metrics, traces, exceptions = simulate_exchange_mechanics(
                event_time_ns=times, direction=direction, market_open=opens, close=closes,
                quote_volume=quote_volume, funding=funding, capital=capital, rule=rules[symbol],
            )
            net_primary = metrics["gross_Return"] - metrics["executed_turnover"] * FEE_PROFILES[PRIMARY_FEE] / 10_000.0
            metrics["MDD"] = drawdown(mechanics.gross_return.to_numpy(float))
            if capital == HEADLINE_CAPITAL:
                episodes, episode_summary = build_de_risk_episodes(
                    event_time_ns=mechanics.event_time_ns,
                    # The canonical Phase 6 episode contract segments the
                    # executed directional lifecycle.  Using raw rebalance
                    # quantity here would incorrectly classify every
                    # constant-notional micro-resize as a strategy de-risk.
                    executed_position=np.sign(mechanics.executed_quantity.to_numpy(float)),
                    turnover_increment=mechanics.turnover,
                    gross_return_increment=mechanics.gross_return,
                    strategy=strategy, symbol=symbol, granularity="1m", lag="lag1m",
                    premium_mode="included", variant="original",
                )
                metrics["episode_count"] = len(episodes)
                episode_summaries[(strategy, symbol, capital)] = episode_summary
            else:
                metrics["episode_count"] = math.nan
            signal_hashes.setdefault((strategy, symbol), set()).add(metrics["signal_stream_sha256"])
            for item in traces:
                trace_rows.append({"strategy_id": strategy, "symbol": symbol, "capital": capital, **item})
            for item in exceptions:
                exception_rows.append({"strategy_id": strategy, "symbol": symbol, "capital": capital, **item})
            discretization_rows.append({
                "strategy_id": strategy, "symbol": symbol, "capital": capital,
                **{key: value for key, value in metrics.items() if key not in {"signal_stream_sha256", "_gross_return_array", "_turnover_array"}},
                "desired_turnover": desired_turnover,
                "turnover_degradation": metrics["executed_turnover"] - desired_turnover,
                "Return_degradation_fee0": metrics["gross_Return"] - e0["gross_Return"],
                "BE_degradation": metrics["gross_BE_bps"] - e0["gross_BE_bps"],
                "MDD_degradation_fee0": metrics["MDD"] - e0["MDD"],
                "price_legality_violations": int(np.sum(~np.isclose(opens / rules[symbol].tick, np.rint(opens / rules[symbol].tick), atol=1e-7, rtol=0.0)) + np.sum(~np.isclose(closes / rules[symbol].tick, np.rint(closes / rules[symbol].tick), atol=1e-7, rtol=0.0))),
                "net_Return_primary_fee": net_primary,
                "residual_BE_margin_primary_fee": metrics["gross_BE_bps"] - FEE_PROFILES[PRIMARY_FEE],
            })
            for scenario in SCENARIOS[1:]:
                profiles = ("FEE0",) if scenario in SCENARIOS[1:3] else tuple(FEE_PROFILES)
                for profile in profiles:
                    master_rows.append(row_for_scenario(
                        strategy=strategy, group=source_row.semantic_group_id, symbol=symbol,
                        provenance=source_row.provenance_tier, phase6b=source_row.phase6b_label,
                        phase6c_label=phase6c_label, warnings=candidate.warnings, capital=capital,
                        scenario=scenario, fee_profile=profile, metrics=metrics,
                        desired_turnover=desired_turnover,
                    ))

    master = pd.DataFrame(master_rows)
    discretization = pd.DataFrame(discretization_rows)
    e0check = pd.DataFrame(e0_residuals)
    atomic_csv(output / "phase6d_execution_master.csv", master)
    atomic_csv(output / "phase6d_discretization_audit.csv", discretization)
    atomic_csv(output / "phase6d_capital_scale_sensitivity.csv", discretization[[c for c in discretization.columns if c not in {"raw_quantity_abs_median", "rounded_quantity_abs_median"}]])
    atomic_csv(output / "phase6d_representative_order_traces.csv", pd.DataFrame(trace_rows))
    atomic_csv(output / "phase6d_execution_exceptions.csv", pd.DataFrame(exception_rows) if exception_rows else pd.DataFrame(columns=["strategy_id", "symbol", "capital", "exception_type"]))
    atomic_csv(output / "phase6d_e0_invariance.csv", e0check)
    fee_impact = master[(master.capital == HEADLINE_CAPITAL) & (master.execution_scenario == SCENARIOS[4])].copy()
    atomic_csv(output / "phase6d_fee_impact.csv", fee_impact)
    latency = master[(master.capital == HEADLINE_CAPITAL) & (master.execution_scenario == SCENARIOS[4]) & (master.fee_profile == PRIMARY_FEE)].copy()
    latency.insert(latency.columns.get_loc("execution_scenario") + 1, "canonical_lag", "lag1m")
    latency.insert(latency.columns.get_loc("canonical_lag") + 1, "additional_latency", "0 (no extra stress; existing realistic lag only)")
    atomic_csv(output / "phase6d_latency_sensitivity.csv", latency)

    headline = master[(master.capital == HEADLINE_CAPITAL) & (master.execution_scenario == SCENARIOS[4]) & (master.fee_profile == PRIMARY_FEE)]
    rounding = master[(master.capital == HEADLINE_CAPITAL) & (master.execution_scenario == SCENARIOS[1]) & (master.fee_profile == "FEE0")]
    summary_rows = []
    for strategy in sorted(selected):
        h = headline[headline.strategy_id == strategy].set_index("symbol")
        r = rounding[rounding.strategy_id == strategy].set_index("symbol")
        original = candidates.set_index("strategy_id").loc[strategy]
        all_rounding = bool((r.Return > 0).all())
        all_fees = bool((h.Return > 0).all() and (h.residual_BE_margin_bps > 0).all())
        if strategy == "xlsx_s2_0265":
            status = "SMALL_SAMPLE_ONLY"
        elif not all_rounding:
            status = "DISCRETIZATION_FRAGILE"
        elif not all_fees:
            status = "FEE_FRAGILE"
        elif str(original.warnings) not in {"NONE", "nan", ""} or str(original.provenance) == "P4_MODELLED_MEDIUM":
            status = "EXECUTION_CONDITIONAL"
        else:
            status = "EXECUTION_RESILIENT_RESEARCH_CANDIDATE"
        row: dict[str, Any] = {
            "strategy_id": strategy, "semantic_group_id": original.semantic_group_id,
            "provenance": original.provenance, "Phase6B_label": original.phase6b_label,
            "Phase6C_label": original.replication_label, "warnings": original.warnings,
            "survives_quantity_rounding_all_symbols": all_rounding,
            "survives_VIP0_taker_all_symbols": all_fees,
            "Phase6D_status": status,
        }
        for symbol in SYMBOLS:
            prefix = symbol.replace("USDT", "")
            row[f"{prefix}_net_Return"] = h.loc[symbol, "Return"]
            row[f"{prefix}_residual_BE_margin_bps"] = h.loc[symbol, "residual_BE_margin_bps"]
            row[f"{prefix}_rounding_degradation"] = r.loc[symbol, "Return"] - float(p6master[(p6master.representative_strategy_id == strategy) & (p6master.symbol == symbol)].Return.iloc[0])
            row[f"{prefix}_participation_P95"] = h.loc[symbol, "participation_P95"]
            row[f"{prefix}_rejected_orders"] = h.loc[symbol, "rejected_order_count"]
            row[f"{prefix}_dust_events"] = h.loc[symbol, "dust_events"]
        summary_rows.append(row)
    strategy_summary = pd.DataFrame(summary_rows)
    atomic_csv(output / "phase6d_strategy_execution_summary.csv", strategy_summary)
    priority_plus = strategy_summary[strategy_summary.strategy_id.isin({"xlsx_s2_0285", "xlsx_s2_0435", "xlsx_s2_0669"})]
    atomic_csv(output / "phase6d_priority_plus_summary.csv", priority_plus)
    phase6e = strategy_summary[strategy_summary.Phase6D_status.isin({"EXECUTION_RESILIENT_RESEARCH_CANDIDATE", "EXECUTION_CONDITIONAL"})].copy()
    phase6e["followup_scope"] = "Phase 6E candidate only; Phase 6E not executed"
    atomic_csv(output / "phase6d_phase6e_candidates.csv", phase6e)

    render_figures(output, master, discretization)
    counts = {
        "rounding": int(strategy_summary.survives_quantity_rounding_all_symbols.sum()),
        "fees": int(strategy_summary.survives_VIP0_taker_all_symbols.sum()),
        "resilient": int((strategy_summary.Phase6D_status == "EXECUTION_RESILIENT_RESEARCH_CANDIDATE").sum()),
        "conditional": int((strategy_summary.Phase6D_status == "EXECUTION_CONDITIONAL").sum()),
    }
    write_review(output, strategy_summary, counts, "SLIPPAGE_NOT_EMPIRICALLY_MODELLED")

    after = phase6d_protected_snapshot(source_root); atomic_json(output / "phase6d_protected_hashes_after.json", after)
    protected_changes = compare_snapshots(before, after)
    max_e0 = float(e0check[["Return_residual", "Turnover_residual", "BE_residual", "MDD_residual", "episode_count_residual"]].abs().to_numpy().max())
    fee_residual = float(np.max(np.abs(master.Return - (master.price_Return.fillna(0) + master.funding_Return.fillna(master.Return) + master.transaction_fee_Return))))
    validation = {
        "status": "PHASE6D_PASSED" if not protected_changes and max_e0 < 1e-9 and all(len(v) == 1 for v in signal_hashes.values()) and int(discretization.quantity_legality_violations.sum()) == 0 else "PHASE6D_FAILED",
        "candidate_groups_terminal": len(strategy_summary), "markets_terminal": len(SYMBOLS),
        "capital_grid": list(CAPITALS), "headline_capital": HEADLINE_CAPITAL,
        "execution_scenarios": list(SCENARIOS), "fee_profiles": FEE_PROFILES,
        "master_rows": len(master), "max_phase6c_e0_residual": max_e0,
        "signal_stream_unique_hashes_per_strategy_symbol_max": max(map(len, signal_hashes.values())),
        "quantity_legality_violations": int(discretization.quantity_legality_violations.sum()),
        "max_fee_identity_residual": fee_residual,
        "slippage_status": "SLIPPAGE_NOT_EMPIRICALLY_MODELLED",
        "maker_execution_simulated": False, "partial_fill_simulated": False,
        "protected_artifact_changes": protected_changes,
        "parameter_optimization_runs": 0, "strategy_semantic_changes": 0,
        "new_symbols": 0, "production_configs_generated": 0, "live_orders": 0,
        "phase6e_started": False, "phase6e_candidates": len(phase6e),
        "status_counts": strategy_summary.Phase6D_status.value_counts().to_dict(),
        "additional_latency_stress": "not performed; canonical lag1m retained",
        "price_policy": "market-observed prices validated only; not rounded",
        "quantity_rounding_policy": "absolute quantity floors to MARKET_LOT_SIZE step, sign restored",
    }
    atomic_json(output / "phase6d_validation_summary.json", validation)
    if validation["status"] != "PHASE6D_PASSED":
        raise RuntimeError(json.dumps(validation, ensure_ascii=False))
    archive, digest, members, size = package(output)
    delivery = {"server_zip": str(archive), "sha256": digest, "member_count": members, "size_bytes": size, "integrity": "PASSED"}
    atomic_json(output / "phase6d_delivery.json", delivery)
    print(json.dumps({**validation, **delivery}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
