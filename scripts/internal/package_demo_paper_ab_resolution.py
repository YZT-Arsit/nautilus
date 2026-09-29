#!/usr/bin/env python3
"""
Package one isolated local A/B from the frozen production-data paper run.

This module is intentionally read-only with respect to the source experiment.
It never constructs an authenticated exchange client and cannot submit orders.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
import subprocess
import sys
import time
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
import yaml


STRATEGY = "dynamic_breakout_short"
SYMBOL = "BTCUSDT"
TIMEFRAME = "1m"
VARIANT = "STRICT_REVERSE"
CANDIDATE_ID = "pc_2fe14acb95eac19f88d4"
DIRECT = "FIRST_TICK_SHADOW"
MAKER = "L1_BBO_PAPER_MAKER"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_csv(path: Path, rows: list[dict], columns: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows, columns=columns).to_csv(path, index=False)


def _source_paths(source: Path) -> tuple[Path, Path]:
    worker = source / "workers" / SYMBOL
    manifest = source / "manifest" / "paper_candidate_manifest_9symbols.csv"
    return worker, manifest


def _candidate_row(manifest: Path) -> pd.Series:
    frame = pd.read_csv(manifest)
    mask = (
        frame.strategy_id.astype(str).eq(STRATEGY)
        & frame.symbol.astype(str).eq(SYMBOL)
        & frame.timeframe.astype(str).eq(TIMEFRAME)
        & frame.direction_variant.astype(str).eq(VARIANT)
    )
    selected = frame.loc[mask]
    if len(selected) != 1:
        raise RuntimeError(f"expected one frozen candidate, found {len(selected)}")
    row = selected.iloc[0]
    if str(row.experiment_candidate_id) != CANDIDATE_ID:
        raise RuntimeError("frozen candidate identity changed")
    return row


def _prepare_layout(source: Path, output: Path) -> Path:
    worker, manifest = _source_paths(source)
    row = _candidate_row(manifest)
    case = output / STRATEGY / f"{SYMBOL}_{TIMEFRAME}_{VARIANT}"
    for directory in ("01_DIRECT", "02_MAKER", "comparison"):
        (case / directory).mkdir(parents=True, exist_ok=True)
    source_reference = {
        "source_experiment": str(source),
        "source_worker": str(worker),
        "candidate_id": CANDIDATE_ID,
        "strategy_id": STRATEGY,
        "symbol": SYMBOL,
        "timeframe": TIMEFRAME,
        "direction_variant": VARIANT,
        "target_definition": "target_reverse(t)=-target_normal(t)",
        "source_manifest_sha256": _sha256(manifest),
        "strategy_code_hash": row.strategy_code_hash,
        "parameter_hash": row.parameter_hash,
        "config_hash": row.config_hash,
        "source_manifest_immutable": True,
        "production_market_data": "READ_ONLY",
        "production_trading_client": "NOT_INITIALIZED",
        "production_exchange_orders": 0,
        "portfolio_isolation": "TWO_INDEPENDENT_LOCAL_PAPER_ACCOUNTS",
    }
    _write_csv(output / "source_experiment_reference.csv", [source_reference])
    engineering = output / "exchange_demo_engineering"
    credentials_status = {
        "credentials_present": False,
        "required_env_vars": ["BINANCE_DEMO_API_KEY", "BINANCE_DEMO_API_SECRET"],
        "production_credentials_not_required": True,
        "production_credentials_forbidden": True,
        "resume_scope": [
            "DEMO_MARKET_ORDER_SMOKE",
            "DEMO_POST_ONLY_RESTING_CANCEL_SMOKE",
            "DEMO_USER_DATA_RECONCILIATION",
        ],
        "local_ab_rerun_required": False,
    }
    engineering.mkdir(parents=True, exist_ok=True)
    (engineering / "demo_credentials_status.json").write_text(
        json.dumps(credentials_status, indent=2) + "\n",
        encoding="utf-8",
    )
    demo_validation_path = engineering / "demo_environment_validation.json"
    if not demo_validation_path.exists():
        demo_status = "BLOCKED_DEMO_CREDENTIALS_UNAVAILABLE"
        for name in (
            "market_order_test.csv",
            "post_only_order_test.csv",
            "order_state_events.csv",
            "account_reconciliation.csv",
        ):
            _write_csv(engineering / name, [{
                "environment": "BINANCE_DEMO",
                "status": demo_status,
                "exchange_orders": 0,
                "reason": "NO_ACCESSIBLE_DEMO_CREDENTIALS",
            }])
        demo_validation = {
            "status": "BLOCKED",
            "reason": "DEMO_CREDENTIALS_UNAVAILABLE",
            "environment": "BINANCE_DEMO",
            "production_exchange_orders": 0,
        }
        demo_validation_path.write_text(json.dumps(demo_validation, indent=2) + "\n")
    else:
        demo_validation = json.loads(demo_validation_path.read_text())
    heartbeat = worker / "health" / "heartbeat.json"
    running = json.loads(heartbeat.read_text()) if heartbeat.exists() else {}
    _write_csv(case / "01_DIRECT" / "RUNNING.csv", [{"status": "RUNNING", "source": DIRECT}])
    _write_csv(case / "02_MAKER" / "RUNNING.csv", [{"status": "RUNNING", "source": MAKER}])
    status = {
        "status": "RUNNING",
        "independent_binance_demo_accounts_available": False,
        "two_api_keys_same_account": False,
        "exchange_native_simultaneous_ab": "UNAVAILABLE",
        "demo_market_engineering_test": demo_validation.get("market_test", "BLOCKED"),
        "demo_post_only_engineering_test": demo_validation.get("post_only_test", "BLOCKED"),
        "local_isolated_simultaneous_ab": "RUNNING",
        "portfolio_isolation": True,
        "source_phase": running.get("phase"),
        "source_alive": running.get("alive"),
        "source_time_ns": running.get("time_ns"),
        "source_counts": running.get("counts", {}),
        "production_exchange_orders": 0,
        "existing_production_data_paper": "UNCHANGED_RUNNING",
        "result": str(output),
    }
    (output / "isolation_resolution_status.json").write_text(json.dumps(status, indent=2) + "\n")
    return case


def _load_market_prices(worker: Path) -> pd.DataFrame:
    prices: dict[int, tuple[int, float]] = {}
    for path in sorted((worker / "market_data" / f"symbol={SYMBOL}").rglob("events.jsonl")):
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                row = json.loads(line)
                payload = row["payload"]
                if payload.get("event_class") != "TradeEvent":
                    continue
                ts = int(payload["event_time_ns"])
                minute = ts // 60_000_000_000
                prices[minute] = (ts, float(payload["price"]))
    if not prices:
        raise RuntimeError("source paper run contains no BTCUSDT trades")
    return pd.DataFrame(
        [{"timestamp_ns": ts, "price": price} for ts, price in prices.values()]
    ).sort_values("timestamp_ns", ignore_index=True)


def _performance(
    prices: pd.DataFrame,
    fills: pd.DataFrame,
    funding: pd.DataFrame,
    decisions: pd.DataFrame,
    mode: str,
    initial_capital: float,
    target_notional: float,
) -> pd.DataFrame:
    ff = fills.loc[fills.execution_mode.eq(mode)].sort_values("event_time_ns").reset_index(drop=True)
    funding_column = "FIRST_TICK_funding_payment" if mode == DIRECT else "MAKER_funding_payment"
    if funding_column in funding.columns and "event_time_ns" in funding.columns:
        fund = funding.loc[funding.execution_mode.eq("EVENT_DETAIL")].copy()
        fund = fund.loc[fund[funding_column].notna()].sort_values("event_time_ns").reset_index(drop=True)
    else:
        fund = pd.DataFrame(columns=["event_time_ns", funding_column])
    decisions = decisions.sort_values("event_time_ns").reset_index(drop=True)
    cash, position, turnover, target = initial_capital, 0.0, 0.0, 0.0
    fi = fu = di = 0
    rows: list[dict] = []
    peak = -math.inf
    for point in prices.itertuples(index=False):
        ts, price = int(point.timestamp_ns), float(point.price)
        while di < len(decisions) and int(decisions.iloc[di].event_time_ns) <= ts:
            target = float(decisions.iloc[di].target)
            di += 1
        while fi < len(ff) and int(ff.iloc[fi].event_time_ns) <= ts:
            fill = ff.iloc[fi]
            signed = float(fill.quantity) * (1.0 if str(fill.side) == "BUY" else -1.0)
            fee = abs(signed * float(fill.price)) * float(fill.fee_rate)
            cash -= signed * float(fill.price) + fee
            position += signed
            turnover += abs(signed * float(fill.price)) / target_notional
            fi += 1
        while fu < len(fund) and int(fund.iloc[fu].event_time_ns) <= ts:
            cash += float(fund.iloc[fu][funding_column])
            fu += 1
        equity = cash + position * price
        peak = max(peak, equity)
        rows.append({
            "timestamp_ns": ts,
            "timestamp_utc": pd.Timestamp(ts, unit="ns", tz="UTC").isoformat(),
            "price": price,
            "target": target,
            "target_position_qty": target * target_notional / price if price else 0.0,
            "actual_position": position,
            "equity": equity,
            "Return": equity / initial_capital - 1.0,
            "cumulative_turnover_raw": turnover,
            "drawdown": equity / peak - 1.0 if peak else 0.0,
        })
    return pd.DataFrame(rows)


def _render_mode(path: Path, perf: pd.DataFrame, title: str) -> None:
    ts = pd.to_datetime(perf.timestamp_ns, unit="ns", utc=True)
    fig, axes = plt.subplots(3, 1, figsize=(14, 9), sharex=True, constrained_layout=True)
    axes[0].plot(ts, perf.Return * 100, color="#1558D6", label="Cumulative Return (%)")
    turn = axes[0].twinx()
    turn.plot(ts, perf.cumulative_turnover_raw, color="#E07A1F", alpha=0.7, label="Turnover (raw)")
    axes[0].set_ylabel("Return (%)")
    turn.set_ylabel("Turnover (raw)")
    axes[1].step(ts, perf.actual_position, where="post", color="#6A3D9A", label="Actual position")
    axes[1].step(ts, perf.target_position_qty, where="post", color="black", alpha=0.45, label="Target qty")
    axes[1].legend(loc="upper left")
    axes[1].set_ylabel("Position qty / target")
    axes[2].fill_between(ts, perf.drawdown * 100, 0, color="#C44E52", alpha=0.45)
    trough = int(perf.drawdown.idxmin())
    axes[2].scatter([ts.iloc[trough]], [perf.drawdown.iloc[trough] * 100], color="black", s=24)
    axes[2].set_ylabel("Drawdown (%)")
    axes[2].set_xlabel("UTC")
    fig.suptitle(title)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=150)
    plt.close(fig)


def _render_comparison(
    path: Path,
    direct: pd.DataFrame,
    maker: pd.DataFrame,
    maker_summary: dict,
) -> None:
    joined = direct.merge(maker, on=["timestamp_ns", "timestamp_utc", "price"], suffixes=("_DIRECT", "_MAKER"))
    ts = pd.to_datetime(joined.timestamp_ns, unit="ns", utc=True)
    fig, axes = plt.subplots(4, 1, figsize=(15, 12), sharex=True, constrained_layout=True)
    axes[0].plot(ts, joined.Return_DIRECT * 100, label="DIRECT")
    axes[0].plot(ts, joined.Return_MAKER * 100, label="MAKER")
    axes[0].set_ylabel("Return (%)")
    axes[0].legend()
    axes[1].step(ts, joined.target_position_qty_DIRECT, where="post", color="black", alpha=0.5, label="Target qty")
    axes[1].step(ts, joined.actual_position_DIRECT, where="post", label="DIRECT")
    axes[1].step(ts, joined.actual_position_MAKER, where="post", label="MAKER")
    axes[1].set_ylabel("Position")
    axes[1].legend()
    axes[2].plot(ts, joined.cumulative_turnover_raw_DIRECT, label="DIRECT")
    axes[2].plot(ts, joined.cumulative_turnover_raw_MAKER, label="MAKER")
    axes[2].set_ylabel("Turnover (raw)")
    axes[2].legend()
    axes[3].plot(ts, joined.drawdown_DIRECT * 100, label="DIRECT")
    axes[3].plot(ts, joined.drawdown_MAKER * 100, label="MAKER")
    axes[3].set_ylabel("Drawdown (%)")
    axes[3].set_xlabel("UTC")
    axes[3].legend()
    metric_text = (
        f"Maker fill={maker_summary['quantity_fill_ratio']:.2%} | "
        f"Zero-fill={maker_summary['zero_fill_rate']:.2%} | "
        f"Median/P95 first fill={maker_summary['median_first_fill_latency_ms']:.1f}/"
        f"{maker_summary['P95_first_fill_latency_ms']:.1f} ms | "
        f"Target error={maker_summary['mean_target_position_error']:.6g}"
    )
    fig.text(0.5, 0.005, metric_text, ha="center", va="bottom", fontsize=9)
    fig.suptitle(f"{STRATEGY} | {SYMBOL} | {TIMEFRAME} | {VARIANT} | isolated local A/B")
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=150)
    plt.close(fig)


def _finalize(source: Path, output: Path) -> None:  # noqa: C901
    worker, _ = _source_paths(source)
    validation_path = worker / "dry_run_validation.json"
    if not validation_path.exists():
        raise RuntimeError("source 24h paper run has not completed")
    validation = json.loads(validation_path.read_text())
    if validation.get("status") != "PASSED" or validation.get("production_exchange_orders") != 0:
        raise RuntimeError("source paper validation did not pass safely")
    case = _prepare_layout(source, output)
    replay_csv = worker / "replay_validation.csv"
    replay_json = worker / "replay" / "replay_validation.json"
    if not replay_csv.exists():
        repo = source.parents[2]
        subprocess.run(  # noqa: S603 fixed local interpreter and repository script
            [
                sys.executable,
                str(repo / "scripts" / "internal" / "replay_paper_experiment.py"),
                "--repo",
                str(repo),
                "--experiment",
                str(source),
                "--phase-root",
                str(worker),
                "--candidate-id",
                CANDIDATE_ID,
            ],
            cwd=repo,
            check=True,
        )
    replay_mismatches = pd.read_csv(replay_csv)
    if len(replay_mismatches) != 0:
        raise RuntimeError(f"offline replay mismatch count is {len(replay_mismatches)}")
    shutil.copy2(replay_csv, case / "comparison" / "replay_validation.csv")
    if replay_json.exists():
        shutil.copy2(replay_json, case / "comparison" / "replay_validation.json")
    summary = pd.read_csv(worker / "strategy_case_summary.csv")
    summary = summary.loc[summary.experiment_candidate_id.eq(CANDIDATE_ID)]
    if len(summary) != 1:
        raise RuntimeError("candidate summary missing or duplicated")
    fills = pd.read_csv(worker / "fills" / "simulated_fills.csv")
    fills = fills.loc[fills.experiment_candidate_id.eq(CANDIDATE_ID)].copy()
    orders = pd.read_csv(worker / "orders" / "simulated_orders.csv")
    orders = orders.loc[orders.experiment_candidate_id.eq(CANDIDATE_ID)].copy()
    daily = pd.read_csv(worker / "daily_turnover.csv")
    daily = daily.loc[daily.experiment_candidate_id.eq(CANDIDATE_ID)].copy()
    funding = pd.read_csv(worker / "funding" / "funding_summary.csv")
    funding = funding.loc[funding.experiment_candidate_id.eq(CANDIDATE_ID)].copy()
    fees = pd.read_csv(worker / "fees" / "fee_summary.csv")
    fees = fees.loc[fees.experiment_candidate_id.eq(CANDIDATE_ID)].copy()
    decisions_path = worker / "decisions" / f"{CANDIDATE_ID}.jsonl"
    decisions = pd.DataFrame([json.loads(line) for line in decisions_path.read_text().splitlines() if line])
    prices = _load_market_prices(worker)
    config = yaml.safe_load((source / "manifest" / "paper_trading_v1.resolved.yaml").read_text())
    initial = float(config["account"]["initial_capital"])
    notional = float(config["account"]["target_notional"])
    perfs: dict[str, pd.DataFrame] = {}
    duration_days = max(float(validation["summary"]["duration_hours"]) / 24.0, 1e-12)
    mode_summaries: dict[str, dict] = {}
    accounting_failures: list[dict] = []
    for folder, mode in (("01_DIRECT", DIRECT), ("02_MAKER", MAKER)):
        root = case / folder
        (root / "RUNNING.csv").unlink(missing_ok=True)
        mode_fills = fills.loc[fills.execution_mode.eq(mode)].copy()
        mode_daily = daily.loc[daily.execution_mode.eq(mode)].copy()
        mode_funding = funding.loc[funding.execution_mode.isin([mode, "EVENT_DETAIL"])].copy()
        mode_fees = fees.loc[fees.execution_mode.eq(mode)].copy()
        mode_fills.to_csv(root / "fills.csv", index=False)
        mode_daily.to_csv(root / "daily_turnover.csv", index=False)
        mode_funding.to_csv(root / "funding.csv", index=False)
        mode_fees.to_csv(root / "fees.csv", index=False)
        if mode == MAKER:
            orders.to_csv(root / "orders.csv", index=False)
        perf = _performance(prices, fills, funding, decisions, mode, initial, notional)
        perf.to_csv(root / "performance.csv", index=False)
        perfs[mode] = perf
        source_row = summary.iloc[0]
        prefix = "FIRST_TICK" if mode == DIRECT else "MAKER"
        mode_summary = {
            "strategy_id": STRATEGY,
            "symbol": SYMBOL,
            "timeframe": TIMEFRAME,
            "direction_variant": VARIANT,
            "execution_mode": mode,
            "Return": float(perf.iloc[-1].Return),
            "MaxDD": float(perf.drawdown.min()),
            "Avg_Daily_Turnover_pct": float(perf.iloc[-1].cumulative_turnover_raw / duration_days * 100.0),
            "Total_Turnover_raw": float(perf.iloc[-1].cumulative_turnover_raw),
            "Total_Turnover_pct": float(perf.iloc[-1].cumulative_turnover_raw * 100.0),
            "Sharpe": "INSUFFICIENT_DAILY_OBSERVATIONS",
            "fee_total": float(mode_fees.fee_total.sum()),
            "funding_pnl": float(mode_funding.loc[mode_funding.execution_mode.eq(mode), "funding_pnl"].sum()),
            "fill_count": len(mode_fills),
            "order_count": int(source_row.get(f"{prefix}_orders", 0)) if prefix == "MAKER" else len(mode_fills),
            "full_fill_orders": int(source_row.get("MAKER_full_fill_orders", 0)) if prefix == "MAKER" else len(mode_fills),
            "partial_fill_orders": int(source_row.get("MAKER_partial_fill_orders", 0)) if prefix == "MAKER" else 0,
            "zero_fill_orders": int(source_row.get("MAKER_zero_fill_orders", 0)) if prefix == "MAKER" else 0,
            "zero_fill_rate": (
                float(source_row.get("MAKER_zero_fill_orders", 0))
                / max(float(source_row.get("MAKER_orders", 0)), 1.0)
                if prefix == "MAKER" else 0.0
            ),
            "quantity_fill_ratio": float(source_row.get("MAKER_quantity_fill_ratio", float("nan"))) if prefix == "MAKER" else 1.0,
            "median_first_fill_latency_ms": float(source_row.get("MAKER_median_first_fill_latency_ms", float("nan"))) if prefix == "MAKER" else float("nan"),
            "P95_first_fill_latency_ms": float(source_row.get("MAKER_P95_first_fill_latency_ms", float("nan"))) if prefix == "MAKER" else float("nan"),
            "mean_target_position_error": float(source_row.get("MAKER_mean_target_position_error", float("nan"))) if prefix == "MAKER" else 0.0,
        }
        expected_return = float(source_row[f"{prefix}_Return"])
        expected_turnover = float(source_row[f"{prefix}_total_turnover_raw"])
        for field, actual, expected in (
            ("Return", mode_summary["Return"], expected_return),
            ("Total_Turnover_raw", mode_summary["Total_Turnover_raw"], expected_turnover),
            ("Daily_Turnover_sum", float(mode_daily.daily_turnover_raw.sum()), expected_turnover),
        ):
            if not math.isclose(actual, expected, rel_tol=1e-10, abs_tol=1e-10):
                accounting_failures.append({
                    "execution_mode": mode,
                    "field": field,
                    "actual": actual,
                    "expected": expected,
                })
        mode_summaries[mode] = mode_summary
        pd.DataFrame([mode_summary]).to_csv(root / "mode_summary.csv", index=False)
        _render_mode(root / "figures" / "performance.png", perf, f"{STRATEGY} | {SYMBOL} | {TIMEFRAME} | {VARIANT} | {mode}")
    direct, maker = perfs[DIRECT], perfs[MAKER]
    comparison = direct.merge(maker, on=["timestamp_ns", "timestamp_utc", "price"], suffixes=("_DIRECT", "_MAKER"))
    comparison.to_csv(case / "comparison" / "execution_comparison.csv", index=False)
    decisions.to_csv(case / "comparison" / "decision_alignment.csv", index=False)
    _render_comparison(
        case / "comparison" / "comparison.png",
        direct,
        maker,
        mode_summaries[MAKER],
    )
    pd.DataFrame(
        accounting_failures,
        columns=["execution_mode", "field", "actual", "expected"],
    ).to_csv(case / "comparison" / "accounting_invariant_failures.csv", index=False)
    if accounting_failures:
        raise RuntimeError(f"accounting invariant failure count is {len(accounting_failures)}")
    demo_validation_path = output / "exchange_demo_engineering" / "demo_environment_validation.json"
    demo_validation = json.loads(demo_validation_path.read_text())
    demo_passed = demo_validation.get("status") == "PASSED"
    source_summary = validation.get("summary", {})
    worker_errors = source_summary.get("worker_errors", [])
    reconnects = source_summary.get("reconnects", {})
    duration_hours = float(source_summary.get("duration_hours", 0.0))
    expected_1m_bars = max(math.floor(duration_hours * 60.0), 0)
    observed_1m_bars = int(source_summary.get("bars_1m", 0))
    data_gaps = max(expected_1m_bars - observed_1m_bars, 0)
    data_continuity_passed = data_gaps == 0
    final = {
        "status": "PASSED" if data_continuity_passed else "BLOCKED",
        "independent_binance_demo_accounts_available": False,
        "two_api_keys_same_account": False,
        "exchange_native_simultaneous_ab": "UNAVAILABLE",
        "demo_market_engineering_test": demo_validation.get("market_test", "BLOCKED"),
        "demo_post_only_engineering_test": demo_validation.get("post_only_test", "BLOCKED"),
        "local_isolated_simultaneous_ab": "COMPLETED",
        "portfolio_isolation": True,
        "source_validation": validation,
        "replay_mismatches": len(replay_mismatches),
        "data_gaps": data_gaps,
        "data_continuity_passed": data_continuity_passed,
        "expected_1m_bars": expected_1m_bars,
        "observed_1m_bars": observed_1m_bars,
        "worker_error_records": len(worker_errors) if isinstance(worker_errors, list) else int(worker_errors or 0),
        "reconnects": reconnects,
        "accounting_invariant_failures": len(accounting_failures),
        "direct": mode_summaries[DIRECT],
        "maker": mode_summaries[MAKER],
        "sharpe": "INSUFFICIENT_DAILY_OBSERVATIONS",
        "seven_day_run": "NOT_STARTED",
        "production_exchange_orders": 0,
        "existing_production_data_paper": "UNCHANGED_COMPLETED",
        "demo_engineering": "WAITING_FOR_CREDENTIALS" if not demo_passed else "PASSED",
        "result": str(output),
    }
    (output / "isolation_resolution_status.json").write_text(json.dumps(final, indent=2) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-experiment", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--wait", action="store_true")
    parser.add_argument("--poll-seconds", type=int, default=60)
    args = parser.parse_args()
    source, output = args.source_experiment.resolve(), args.output.resolve()
    _prepare_layout(source, output)
    validation = _source_paths(source)[0] / "dry_run_validation.json"
    while args.wait and not validation.exists():
        time.sleep(max(10, args.poll_seconds))
    if validation.exists():
        _finalize(source, output)
    print((output / "isolation_resolution_status.json").read_text())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
