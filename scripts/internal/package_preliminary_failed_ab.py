#!/usr/bin/env python3
"""Build a non-authoritative boss package from a failed paper A/B run.

The source experiment is read-only.  Publication is refused unless an
as-recorded replay exists and has zero mismatches.  No missing market data is
created, interpolated, or copied into the source experiment.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
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
LABEL = "PRELIMINARY_DIAGNOSTIC_ONLY"
WARNING = "NOT VALIDATED DUE TO 1-MINUTE CANONICAL DATA GAP"
MISSING_UTC = pd.Timestamp("2026-10-07T10:53:00Z")


def _read_jsonl(path: Path) -> pd.DataFrame:
    return pd.DataFrame([json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line])


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _performance(
    bars: pd.DataFrame,
    fills: pd.DataFrame,
    decisions: pd.DataFrame,
    mode: str,
    initial_capital: float,
    target_notional: float,
) -> pd.DataFrame:
    mode_fills = fills.loc[fills.execution_mode.eq(mode)].sort_values("event_time_ns").reset_index(drop=True)
    decisions = decisions.sort_values("event_time_ns").reset_index(drop=True)
    cash, position, turnover, target = initial_capital, 0.0, 0.0, 0.0
    fill_index = decision_index = 0
    peak = -math.inf
    rows: list[dict] = []
    for bar in bars.sort_values("event_time_ns").itertuples(index=False):
        timestamp_ns, price = int(bar.event_time_ns), float(bar.close)
        while decision_index < len(decisions) and int(decisions.iloc[decision_index].event_time_ns) <= timestamp_ns:
            target = float(decisions.iloc[decision_index].target)
            decision_index += 1
        while fill_index < len(mode_fills) and int(mode_fills.iloc[fill_index].event_time_ns) <= timestamp_ns:
            fill = mode_fills.iloc[fill_index]
            signed = float(fill.quantity) * (1.0 if str(fill.side) == "BUY" else -1.0)
            cash -= signed * float(fill.price) + float(fill.fee)
            position += signed
            turnover += abs(signed * float(fill.price)) / target_notional
            fill_index += 1
        equity = cash + position * price
        peak = max(peak, equity)
        rows.append({
            "timestamp_ns": timestamp_ns,
            "timestamp_utc": pd.Timestamp(timestamp_ns, unit="ns", tz="UTC").isoformat(),
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


def _summary_row(source: pd.Series, perf: pd.DataFrame, mode: str, duration_days: float) -> dict:
    prefix = "FIRST_TICK" if mode == DIRECT else "MAKER"
    total_turnover = float(source[f"{prefix}_total_turnover_raw"])
    return {
        "evidence_label": LABEL,
        "integrity_warning": WARNING,
        "strategy_id": STRATEGY,
        "symbol": SYMBOL,
        "timeframe": TIMEFRAME,
        "direction_variant": VARIANT,
        "execution_mode": mode,
        "PRELIMINARY_Return": float(source[f"{prefix}_Return"]),
        "PRELIMINARY_MaxDD": float(perf.drawdown.min()),
        "PRELIMINARY_Avg_Daily_Turnover_pct": total_turnover / duration_days * 100.0,
        "PRELIMINARY_Total_Turnover_raw": total_turnover,
        "PRELIMINARY_Total_Turnover_pct": total_turnover * 100.0,
        "Sharpe": "INSUFFICIENT_DAILY_OBSERVATIONS",
        "fill_count": int(source["FIRST_TICK_fills"] if mode == DIRECT else source["MAKER_fills"]),
        "order_count": 0 if mode == DIRECT else int(source["MAKER_orders"]),
        "full_fill_orders": 0 if mode == DIRECT else int(source["MAKER_full_fill_orders"]),
        "partial_fill_orders": 0 if mode == DIRECT else int(source["MAKER_partial_fill_orders"]),
        "zero_fill_orders": 0 if mode == DIRECT else int(source["MAKER_zero_fill_orders"]),
        "quantity_fill_ratio": 1.0 if mode == DIRECT else float(source["MAKER_quantity_fill_ratio"]),
        "order_fill_rate": 1.0 if mode == DIRECT else (
            (float(source["MAKER_full_fill_orders"]) + float(source["MAKER_partial_fill_orders"]))
            / max(float(source["MAKER_orders"]), 1.0)
        ),
        "median_first_fill_latency_ms": math.nan if mode == DIRECT else float(source["MAKER_median_first_fill_latency_ms"]),
        "P95_first_fill_latency_ms": math.nan if mode == DIRECT else float(source["MAKER_P95_first_fill_latency_ms"]),
        "mean_target_position_error": 0.0 if mode == DIRECT else float(source["MAKER_mean_target_position_error"]),
    }


def _source_present(frame: pd.DataFrame, route: str) -> pd.Series:
    return (frame[f"{route}_quote_count"] > 0) & (frame[f"{route}_trade_count"] > 0)


def _render_comparison(path: Path, direct: pd.DataFrame, maker: pd.DataFrame) -> None:
    joined = direct.merge(maker, on=["timestamp_ns", "timestamp_utc", "price"], suffixes=("_DIRECT", "_MAKER"))
    timestamp = pd.to_datetime(joined.timestamp_ns, unit="ns", utc=True)
    fig, axes = plt.subplots(4, 1, figsize=(15, 12), sharex=True, constrained_layout=True)
    axes[0].plot(timestamp, joined.Return_DIRECT * 100.0, label="DIRECT")
    axes[0].plot(timestamp, joined.Return_MAKER * 100.0, label="MAKER")
    axes[0].set_ylabel("Return (%)")
    axes[0].legend()
    axes[1].step(timestamp, joined.target_position_qty_DIRECT, where="post", color="black", alpha=.45, label="Target")
    axes[1].step(timestamp, joined.actual_position_DIRECT, where="post", label="DIRECT")
    axes[1].step(timestamp, joined.actual_position_MAKER, where="post", label="MAKER")
    axes[1].set_ylabel("Position")
    axes[1].legend()
    axes[2].plot(timestamp, joined.cumulative_turnover_raw_DIRECT, label="DIRECT")
    axes[2].plot(timestamp, joined.cumulative_turnover_raw_MAKER, label="MAKER")
    axes[2].set_ylabel("Turnover (raw)")
    axes[2].legend()
    axes[3].plot(timestamp, joined.drawdown_DIRECT * 100.0, label="DIRECT")
    axes[3].plot(timestamp, joined.drawdown_MAKER * 100.0, label="MAKER")
    axes[3].set_ylabel("Drawdown (%)")
    axes[3].set_xlabel("UTC")
    axes[3].legend()
    for axis in axes:
        axis.axvline(MISSING_UTC, color="#D62728", linestyle="--", linewidth=1.6, label="Missing live minute")
    fig.suptitle(
        "PRELIMINARY — FAILED DATA INTEGRITY GATE\n"
        "dynamic_breakout_short | BTCUSDT | 1m | STRICT_REVERSE | 1439/1440 bars",
        color="#B00020",
        fontweight="bold",
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=160)
    plt.close(fig)


def main() -> int:  # noqa: C901
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--gap-sensitivity-json", type=Path)
    parser.add_argument("--gap-source-dir", type=Path)
    args = parser.parse_args()
    source, output = args.source.resolve(), args.output.resolve()
    worker = source / "workers" / SYMBOL
    validation = json.loads((worker / "dry_run_validation.json").read_text(encoding="utf-8"))
    if validation.get("status") != "BLOCKED" or validation.get("observed_bars") != 1439 or validation.get("expected_bars") != 1440:
        raise RuntimeError("source is not the frozen 1439/1440 failed-integrity experiment")
    if int(validation.get("production_exchange_orders", -1)) != 0:
        raise RuntimeError("production order safety assertion failed")
    replay_path = worker / "replay" / "replay_validation.json"
    if not replay_path.exists():
        raise RuntimeError("as-recorded replay has not completed; preliminary metrics are not publishable")
    replay = json.loads(replay_path.read_text(encoding="utf-8"))
    if replay.get("status") != "PASSED" or int(replay.get("mismatch_count", -1)) != 0:
        raise RuntimeError("as-recorded replay is not deterministic; preliminary metrics are not publishable")

    if output.exists():
        shutil.rmtree(output)
    for folder in ("01_summary", "02_direct", "03_maker", "04_data_quality", "05_gap_sensitivity", "06_replay"):
        (output / folder).mkdir(parents=True, exist_ok=True)

    case = pd.read_csv(worker / "strategy_case_summary.csv")
    case = case.loc[case.experiment_candidate_id.eq(CANDIDATE_ID)]
    if len(case) != 1:
        raise RuntimeError("frozen candidate result missing or duplicated")
    row = case.iloc[0]
    fills = pd.read_csv(worker / "fills" / "simulated_fills.csv")
    fills = fills.loc[fills.experiment_candidate_id.eq(CANDIDATE_ID)].copy()
    orders = pd.read_csv(worker / "orders" / "simulated_orders.csv")
    orders = orders.loc[orders.experiment_candidate_id.eq(CANDIDATE_ID)].copy()
    daily = pd.read_csv(worker / "daily_turnover.csv")
    daily = daily.loc[daily.experiment_candidate_id.eq(CANDIDATE_ID)].copy()
    decisions = _read_jsonl(worker / "decisions" / f"{CANDIDATE_ID}.jsonl")
    bars = _read_jsonl(worker / "bars" / f"{SYMBOL}_{TIMEFRAME}.jsonl")
    config = yaml.safe_load((source / "manifest" / "paper_trading_v1.resolved.yaml").read_text(encoding="utf-8"))
    initial_capital = float(config["account"]["initial_capital"])
    target_notional = float(config["account"]["target_notional"])
    duration_days = float(validation["summary"]["duration_hours"]) / 24.0
    direct = _performance(bars, fills, decisions, DIRECT, initial_capital, target_notional)
    maker = _performance(bars, fills, decisions, MAKER, initial_capital, target_notional)
    direct_summary = _summary_row(row, direct, DIRECT, duration_days)
    maker_summary = _summary_row(row, maker, MAKER, duration_days)
    direct_ledger_fills = fills.loc[fills.execution_mode.eq(DIRECT)]
    maker_ledger_fills = fills.loc[fills.execution_mode.eq(MAKER)]
    direct_summary.update({
        "fill_count": len(direct_ledger_fills),
        "fill_count_definition": "UNIQUE_SIMULATED_FILL_LEDGER_ROWS",
        "unique_fill_ids": int(direct_ledger_fills.fill_id.nunique()),
        "trade_callback_fill_count": int(validation["summary"]["first_tick_fills"]),
        "other_callback_fill_count": len(direct_ledger_fills) - int(validation["summary"]["first_tick_fills"]),
    })
    maker_summary.update({
        "fill_count": len(maker_ledger_fills),
        "fill_count_definition": "UNIQUE_SIMULATED_ORDERFILLED_LEDGER_ROWS",
        "unique_fill_ids": int(maker_ledger_fills.fill_id.nunique()),
        "trade_callback_fill_count": int(validation["summary"]["maker_fills"]),
        "other_callback_fill_count": len(maker_ledger_fills) - int(validation["summary"]["maker_fills"]),
    })
    pd.DataFrame([direct_summary, maker_summary]).to_csv(output / "01_summary" / "preliminary_direct_vs_maker.csv", index=False)
    _render_comparison(output / "01_summary" / "preliminary_comparison.png", direct, maker)

    coverage = pd.read_csv(source / "active_active" / "canonical_minute_coverage.csv")
    source_flags = {route: _source_present(coverage, route) for route in ("ROUTE_A", "ROUTE_B", "ROUTE_C")}
    healthy_count = sum(source_flags.values())
    data_integrity = [{
        "evidence_label": LABEL,
        "integrity_status": "FAILED_INTEGRITY_GATE",
        "expected_bars": 1440,
        "observed_bars": 1439,
        "canonical_missing_minutes": 1,
        "missing_minute_utc": MISSING_UTC.isoformat(),
        "missing_minute_china": MISSING_UTC.tz_convert("Asia/Shanghai").isoformat(),
        "minutes_with_3_sources": int((healthy_count == 3).sum()),
        "minutes_with_2_or_more_sources": int((healthy_count >= 2).sum()),
        "minutes_with_exactly_1_source": int((healthy_count == 1).sum()),
        "minutes_with_0_sources": int((healthy_count == 0).sum()),
        "Route_A_coverage_minutes": int(source_flags["ROUTE_A"].sum()),
        "Route_B_coverage_minutes": int(source_flags["ROUTE_B"].sum()),
        "Collector_C_coverage_minutes": int(source_flags["ROUTE_C"].sum()),
        "Route_A_coverage_pct": float(source_flags["ROUTE_A"].mean() * 100.0),
        "Route_B_coverage_pct": float(source_flags["ROUTE_B"].mean() * 100.0),
        "Collector_C_coverage_pct": float(source_flags["ROUTE_C"].mean() * 100.0),
        "canonical_coverage_pct": 1439 / 1440 * 100.0,
        "redundancy_conclusion": "REDUNDANCY_ARCHITECTURE_PRESENT_BUT_LONG_HORIZON_SOURCE_REDUNDANCY_INSUFFICIENT",
        "production_exchange_orders": 0,
    }]
    pd.DataFrame(data_integrity).to_csv(output / "01_summary" / "data_integrity_summary.csv", index=False)
    coverage.assign(
        source_A_present=source_flags["ROUTE_A"],
        source_B_present=source_flags["ROUTE_B"],
        source_C_present=source_flags["ROUTE_C"],
        healthy_source_count=healthy_count,
    ).to_csv(output / "04_data_quality" / "minute_source_coverage.csv", index=False)

    direct.to_csv(output / "02_direct" / "preliminary_performance.csv", index=False)
    maker.to_csv(output / "03_maker" / "preliminary_performance.csv", index=False)
    fills.loc[fills.execution_mode.eq(DIRECT)].to_csv(output / "02_direct" / "fills.csv", index=False)
    fills.loc[fills.execution_mode.eq(MAKER)].to_csv(output / "03_maker" / "fills.csv", index=False)
    daily.loc[daily.execution_mode.eq(DIRECT)].to_csv(output / "02_direct" / "daily_turnover.csv", index=False)
    daily.loc[daily.execution_mode.eq(MAKER)].to_csv(output / "03_maker" / "daily_turnover.csv", index=False)
    orders.to_csv(output / "03_maker" / "orders.csv", index=False)
    decisions.to_csv(output / "04_data_quality" / "as_recorded_decisions.csv", index=False)
    bars.to_csv(output / "04_data_quality" / "as_recorded_bars.csv", index=False)

    sensitivity = {
        "status": "BLOCKED",
        "classification": "BLOCKED",
        "reason": "AUTHORITATIVE_MISSING_MINUTE_DATA_NOT_YET_AVAILABLE",
        "evidence_label": "POST_HOC_BACKFILL_FOR_SENSITIVITY_ONLY",
        "source_experiment_modified": False,
    }
    if args.gap_sensitivity_json and args.gap_sensitivity_json.exists():
        sensitivity = json.loads(args.gap_sensitivity_json.read_text(encoding="utf-8"))
    pd.DataFrame([{
        "status": sensitivity.get("status", "BLOCKED"),
        "classification": sensitivity.get("classification", "BLOCKED"),
        "evidence_label": sensitivity.get("evidence_label", "POST_HOC_BACKFILL_FOR_SENSITIVITY_ONLY"),
        "official_aggregate_trade_rows": sensitivity.get("official_aggregate_trade_rows"),
        "official_raw_trade_count": sensitivity.get("official_raw_trade_count"),
        "inserted_counterfactual_decisions": sensitivity.get("inserted_counterfactual_decisions"),
        "divergent_decisions_on_common_timestamps": sensitivity.get("divergent_decisions_on_common_timestamps"),
        "canonical_target_unchanged_through_gap": sensitivity.get("canonical_target_unchanged_through_gap"),
        "direct_actual_position_unchanged_through_gap": sensitivity.get("direct_actual_position_unchanged_through_gap"),
        "maker_actual_position_unchanged_through_gap": sensitivity.get("maker_actual_position_unchanged_through_gap"),
        "final_Return_difference": sensitivity.get("final_Return_difference"),
        "final_turnover_difference": sensitivity.get("final_turnover_difference"),
        "first_decision_divergence_timestamp": sensitivity.get("first_decision_divergence_timestamp"),
        "source_experiment_modified": sensitivity.get("source_experiment_modified", False),
    }]).to_csv(output / "01_summary" / "gap_sensitivity_summary.csv", index=False)
    (output / "05_gap_sensitivity" / "gap_sensitivity.json").write_text(json.dumps(sensitivity, indent=2) + "\n", encoding="utf-8")
    if args.gap_source_dir and args.gap_source_dir.exists():
        for source_file in args.gap_source_dir.iterdir():
            if source_file.is_file():
                shutil.copy2(source_file, output / "05_gap_sensitivity" / source_file.name)
    shutil.copy2(replay_path, output / "06_replay" / "replay_validation.json")
    replay_csv = worker / "replay_validation.csv"
    if replay_csv.exists():
        shutil.copy2(replay_csv, output / "06_replay" / "replay_validation.csv")

    readme = f"""STATUS: PRELIMINARY / NON-AUTHORITATIVE

The 24h run failed the preregistered integrity gate: 1439/1440 canonical bars.

One minute had zero data from all available live sources.

Metrics below are provided for engineering / preliminary review only and must not be treated as validated forward performance.

# Scope

- Experiment: `{source.name}`
- Strategy: `{STRATEGY}`
- Case: `{SYMBOL} / {TIMEFRAME} / {VARIANT}`
- Permanent status: `FAILED_INTEGRITY_GATE`
- Evidence label: `{LABEL}`
- Missing minute: `2026-10-07 18:53 China time` (`2026-10-07 10:53 UTC`)
- Production exchange orders: `0`

# Preliminary observations

- DIRECT observed Return: `{direct_summary['PRELIMINARY_Return']:.12%}`
- DIRECT observed Avg Daily Turnover: `{direct_summary['PRELIMINARY_Avg_Daily_Turnover_pct']:.12f}%`
- MAKER observed Return: `{maker_summary['PRELIMINARY_Return']:.12%}`
- MAKER observed Avg Daily Turnover: `{maker_summary['PRELIMINARY_Avg_Daily_Turnover_pct']:.12f}%`
- MAKER observed quantity fill ratio: `{maker_summary['quantity_fill_ratio']:.12%}`
- As-recorded replay mismatches: `0`
- Gap sensitivity: `{sensitivity.get('classification', sensitivity.get('status', 'BLOCKED'))}`

# Integrity conclusion

`REDUNDANCY_ARCHITECTURE_PRESENT BUT LONG_HORIZON_SOURCE_REDUNDANCY_INSUFFICIENT`.
This package is version `v0_preliminary_failed_integrity`. It is retained for audit and does not supersede the need for a new 1440/1440 clean forward run.
"""
    (output / "01_summary" / "README_PRELIMINARY.md").write_text(readme, encoding="utf-8")
    result = {
        "status": "PASSED_PRELIMINARY_PACKAGE",
        "performance_label": LABEL,
        "warning": WARNING,
        "replay_mismatches": 0,
        "gap_sensitivity": sensitivity.get("classification", sensitivity.get("status", "BLOCKED")),
        "output": str(output),
        "production_exchange_orders": 0,
    }
    (output / "preliminary_package_validation.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    manifest_rows = []
    for path in sorted(output.rglob("*")):
        if path.is_file() and path.name != "package_file_manifest.csv":
            manifest_rows.append({
                "relative_path": path.relative_to(output).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": _sha256(path),
            })
    pd.DataFrame(manifest_rows).to_csv(output / "package_file_manifest.csv", index=False)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
