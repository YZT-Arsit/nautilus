#!/usr/bin/env python3
"""Run the frozen Phase 4C cross-symbol replication matrix.

The runner is intentionally narrow: six Phase 4B semantic groups, three
pre-performance symbols, one canonical lag, ORIGINAL direction, Premium
Included, and no parameter search.  Every completed case is atomically
committed and can be resumed without re-running prior cases.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

from data_engine.loader import load_events
from results.trade_episode import build_de_risk_episodes
from results.trade_episode import write_episode_csv
from scripts.internal.build_phase4a_baseline_evaluation import ROOT
from scripts.internal.prepare_phase4c_cross_symbol import COMMON_END_EXCLUSIVE
from scripts.internal.prepare_phase4c_cross_symbol import COMMON_START
from scripts.internal.prepare_phase4c_cross_symbol import EXPECTED_CASES
from scripts.internal.run_all_strategy_timeframe_lag import build_strategy_clock
from scripts.internal.run_all_strategy_timeframe_lag import run_decision_lifecycle
from scripts.internal.run_constant_notional_overlay import calculate_overlay


SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
NOTIONAL_USDT = 100_000.0


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def atomic_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    frame = pd.DataFrame(rows)
    frame.to_csv(temporary, index=False, encoding="utf-8-sig")
    os.replace(temporary, path)


def normalized_config_hash(source: dict[str, Any]) -> str:
    params = dict(source.get("params", {}))
    for key in (
        "source_registry_id",
        "family",
        "semantic_provenance",
        "contracts_applied",
        "defaulted_parameters",
    ):
        params.pop(key, None)
    encoded = json.dumps(params, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def event_config(root: Path, symbol: str, data_type: str) -> dict[str, Any]:
    funding = data_type == "funding_rate"
    return {
        "mode": "hive_parquet_funding" if funding else "hive_parquet_bars",
        "root": str(root),
        "instrument_id": f"{symbol}-PERP.BINANCE",
        "warmup_bars": 0,
        "timestamp_column": "ts",
        "timestamp_unit": "ns",
        "filters": {
            "asset_class": "crypto",
            "exchange": "BINANCE",
            "venue_type": "futures_um",
            "symbol": symbol,
            "data_type": data_type,
            "freq": "settlement" if funding else "1m",
        },
        "start": COMMON_START,
        # The canonical loader treats a date as an included daily partition.
        "end": "2026-06-29",
    }


def load_symbol(root: Path, symbol: str) -> tuple[list[Any], pd.DataFrame, dict[str, Any]]:
    _, stream = load_events(event_config(root, symbol, "bar"))
    bars = list(stream)
    if len(bars) != 729 * 1440:
        raise ValueError(f"{symbol}: expected 1,049,760 common-window bars, got {len(bars)}")
    bar_times = np.fromiter((bar.event_time_ns for bar in bars), dtype=np.int64)
    if np.any(np.diff(bar_times) <= 0):
        raise ValueError(f"{symbol}: 1m bars are not strictly ordered")
    _, funding_stream = load_events(event_config(root, symbol, "funding_rate"))
    funding = pd.DataFrame(
        [
            {
                "event_time_ns": event.event_time_ns,
                "mark_price": event.mark_price or 0.0,
                "funding_rate": event.funding_rate,
            }
            for event in funding_stream
        ]
    )
    if funding.empty or funding.event_time_ns.duplicated().any():
        raise ValueError(f"{symbol}: missing or duplicate funding events")
    if not funding.event_time_ns.is_monotonic_increasing:
        raise ValueError(f"{symbol}: funding events are not ordered")
    if not np.isfinite(funding[["mark_price", "funding_rate"]].to_numpy(float)).all():
        raise ValueError(f"{symbol}: non-finite funding values")
    first = pd.Timestamp(bar_times[0], unit="ns", tz="UTC")
    last = pd.Timestamp(bar_times[-1], unit="ns", tz="UTC")
    validation = {
        "symbol": symbol,
        "bar_rows": len(bars),
        "funding_rows": len(funding),
        "first_bar": first.isoformat(),
        "last_bar": last.isoformat(),
        "bar_ordering_passed": True,
        "funding_ordering_passed": True,
        "funding_duplicate_count": 0,
    }
    return bars, funding, validation


def write_execution_events(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    fieldnames = list(rows[0]) if rows else ["strategy", "case", "signal_time_ns"]
    with temporary.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    os.replace(temporary, path)


def run_case(
    *,
    strategy: str,
    symbol: str,
    frequency: str,
    lag_minutes: int,
    config_hash: str,
    bars: list[Any],
    funding: pd.DataFrame,
    output: Path,
) -> dict[str, Any]:
    config_path = ROOT / "strategies" / strategy / "config.yaml"
    source = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    actual_hash = normalized_config_hash(source)
    if actual_hash != config_hash:
        raise ValueError(f"{strategy}: frozen config hash changed")
    strategy_bars = build_strategy_clock(bars, frequency)
    end_ns = int(pd.Timestamp(COMMON_END_EXCLUSIVE, tz="UTC").value)
    direction, execution_rows, lifecycle = run_decision_lifecycle(
        strategy_name=strategy,
        source_config=source,
        frequency=frequency,
        lag_minutes=lag_minutes,
        bars_1m=bars,
        strategy_bars=strategy_bars,
        end_exclusive_ns=end_ns,
    )
    event_time = np.fromiter((bar.event_time_ns for bar in bars), dtype=np.int64)
    market_open = np.fromiter((bar.open for bar in bars), dtype=np.float64)
    close = np.fromiter((bar.close for bar in bars), dtype=np.float64)
    result, overlay = calculate_overlay(
        pd.DataFrame({"event_time_ns": event_time, "close": close, "position": direction}),
        funding,
        market_open,
        notional_usdt=NOTIONAL_USDT,
        slippage_bps=0.0,
        vip9_fee_bps=1.7,
        vip0_fee_bps=5.0,
        position_policy="strict_constant_notional",
    )
    result.insert(1, "close", close)
    episode_rows, episode_summary = build_de_risk_episodes(
        event_time_ns=result.event_time_ns,
        executed_position=result.direction,
        turnover_increment=result.turnover,
        gross_return_increment=result.total_return,
        strategy=strategy,
        symbol=symbol,
        granularity=frequency,
        lag=f"lag{lag_minutes}m",
        premium_mode="included",
        variant="original",
    )
    output.mkdir(parents=True, exist_ok=True)
    temporary = output / "timeseries.parquet.tmp"
    result.to_parquet(temporary, index=False, compression="zstd")
    os.replace(temporary, output / "timeseries.parquet")
    write_execution_events(output / "execution_events.csv", execution_rows)
    write_episode_csv(output / "per_trade_break_even.csv", episode_rows)
    summary = {
        "status": "COMPLETED",
        "strategy_id": strategy,
        "symbol": symbol,
        "timeframe": frequency,
        "lag_minutes": lag_minutes,
        "direction": "ORIGINAL",
        "premium": "INCLUDED",
        "common_start": COMMON_START,
        "common_end_exclusive": COMMON_END_EXCLUSIVE,
        "strategy_config_hash": actual_hash,
        "semantic_parameter_changes": 0,
        "symbol_specific_parameter_changes": 0,
        "notional_usdt": NOTIONAL_USDT,
        "instrument_precision_policy": "continuous research quantity; no exchange rounding",
        **lifecycle,
        **overlay,
        **episode_summary,
    }
    atomic_json(output / "summary.json", summary)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market-root", type=Path, default=ROOT / "historical_data/market_data")
    parser.add_argument("--audit-root", type=Path, default=ROOT / "outputs/baseline_evaluation/phase4c")
    parser.add_argument("--output-root", type=Path, default=ROOT / "outputs/batches/phase4c_cross_symbol")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    plan = json.loads((args.audit_root / "phase4c_compute_plan.json").read_text(encoding="utf-8"))
    if plan["status"] != "FROZEN_PRE_PERFORMANCE" or plan["primary_cases"] != EXPECTED_CASES:
        raise ValueError("Phase 4C pre-performance plan is not frozen")
    audit = pd.read_csv(args.audit_root / "phase4c_candidate_transfer_audit.csv")
    run_rows: list[dict[str, Any]] = []
    data_rows: list[dict[str, Any]] = []
    for symbol in SYMBOLS:
        bars, funding, data_validation = load_symbol(args.market_root, symbol)
        data_rows.append(data_validation)
        for row in audit.itertuples(index=False):
            frequency = str(row.timeframe)
            lag_match = re.search(r"\d+", str(row.realistic_lag))
            if lag_match is None:
                raise ValueError(f"{row.strategy_id}: unparseable realistic lag {row.realistic_lag!r}")
            lag_minutes = int(lag_match.group())
            destination = args.output_root / symbol / row.strategy_id / f"{frequency}_lag{lag_minutes}m"
            if not args.overwrite and (destination / "summary.json").is_file() and (destination / "timeseries.parquet").is_file() and (destination / "per_trade_break_even.csv").is_file():
                summary = json.loads((destination / "summary.json").read_text(encoding="utf-8"))
                status = "EXISTING_VALIDATED"
            else:
                summary = run_case(
                    strategy=row.strategy_id,
                    symbol=symbol,
                    frequency=frequency,
                    lag_minutes=lag_minutes,
                    config_hash=row.strategy_config_hash,
                    bars=bars,
                    funding=funding,
                    output=destination,
                )
                status = "COMPLETED"
            run_rows.append({"strategy_id": row.strategy_id, "symbol": symbol, "status": status, "output_path": str(destination), **summary})
            atomic_csv(args.output_root / "phase4c_run_manifest.csv", run_rows)
    atomic_csv(args.audit_root / "phase4c_data_integrity.csv", data_rows)
    if len(run_rows) != EXPECTED_CASES or any(row["status"] not in {"COMPLETED", "EXISTING_VALIDATED"} for row in run_rows):
        raise ValueError("Phase 4C run accounting failed")
    atomic_json(
        args.output_root / "phase4c_run_validation.json",
        {
            "status": "PASSED",
            "planned_cases": EXPECTED_CASES,
            "terminal_cases": len(run_rows),
            "parameter_search_runs": 0,
            "symbol_specific_tuning": 0,
            "production_configs_created": 0,
        },
    )
    print(json.dumps({"terminal_cases": len(run_rows), "status": "PASSED"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
