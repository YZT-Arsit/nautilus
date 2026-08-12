#!/usr/bin/env python3
"""Run validated strategies on independent N-minute clocks and M-minute lag.

This is an experiment orchestrator.  It reuses the canonical market loader,
feature runner, registered strategy plugin, execution intent/fill records, and
the existing constant-notional accounting overlay.  Defaults preserve the
historical 1m/10m × 0m/1m matrix; repeatable ``--case N:M`` selects any
supported positive N and non-negative M. Strategy logic and all core engines
remain unchanged.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
from dataclasses import fields, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

from data_engine.events import BarEvent
from data_engine.loader import load_events
from data_engine.transforms import resample_bars
from feature_engine.runner import FeatureStrategyRunner
from scripts.internal.run_constant_notional_overlay import calculate_overlay
from strategy_framework.backends.nautilus_simulation import IntentFillSimulator
from strategy_framework.execution.intents import OrderIntent, PlannedSignal, PositionIntent
from strategy_framework.execution.reports import ExecutionReport, FillRecord
from strategy_framework.registry import get_entry


MINUTE_NS = 60_000_000_000
DEFAULT_CASES = (("1m", 0), ("1m", 1), ("10m", 0), ("10m", 1))
RESULT_COLUMNS = (
    "direction",
    "target_quantity",
    "boundary_notional_usdt",
    "gross_price_return",
    "slippage_return",
    "trading_return",
    "funding_return",
    "total_return",
    "turnover",
    "vip9_total_return",
    "vip0_total_return",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--market-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--start", default="2021-07-01")
    parser.add_argument("--end", default="2026-06-30")
    parser.add_argument("--notional-usdt", type=float, default=100_000.0)
    parser.add_argument("--vip9-fee-bps", type=float, default=1.7)
    parser.add_argument("--vip0-fee-bps", type=float, default=5.0)
    parser.add_argument("--slippage-bps", type=float, default=0.0)
    parser.add_argument("--shard-index", type=int, default=0)
    parser.add_argument("--shard-count", type=int, default=1)
    parser.add_argument("--strategy", action="append", dest="strategies")
    parser.add_argument(
        "--case",
        action="append",
        dest="cases",
        metavar="N:M",
        help=(
            "strategy bar minutes and independent physical execution lag minutes; "
            "repeatable, e.g. --case 5:0 --case 5:1"
        ),
    )
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def parse_cases(values: list[str] | None) -> tuple[tuple[str, int], ...]:
    if not values:
        return DEFAULT_CASES
    parsed: list[tuple[str, int]] = []
    for value in values:
        frequency_text, separator, lag_text = value.partition(":")
        if not separator:
            raise ValueError(f"invalid --case {value!r}; expected N:M")
        bar_minutes = int(frequency_text.removesuffix("m"))
        lag_minutes = int(lag_text.removesuffix("m"))
        if bar_minutes <= 0 or lag_minutes < 0:
            raise ValueError("bar minutes must be positive and lag minutes non-negative")
        case = (f"{bar_minutes}m", lag_minutes)
        if case not in parsed:
            parsed.append(case)
    return tuple(parsed)


def _build_config_obj(config_cls: type, params: dict[str, Any], frequency: str) -> Any:
    allowed = {field.name for field in fields(config_cls)}
    values = {key: value for key, value in params.items() if key in allowed}
    if "bar_type" in allowed:
        values["bar_type"] = frequency
    return config_cls(**values)


def discover_strategies(source_root: Path) -> list[str]:
    return sorted(
        path.name
        for path in source_root.iterdir()
        if path.is_dir()
        and (path / "fee_5bps" / "config.yaml").is_file()
        and (path / "fee_5bps" / "equity_curve.parquet").is_file()
    )


def market_config(root: Path, start: str, end: str) -> dict[str, Any]:
    return {
        "mode": "hive_parquet_bars",
        "root": str(root),
        "instrument_id": "BTCUSDT-PERP.BINANCE",
        "warmup_bars": 0,
        "timestamp_column": "ts",
        "timestamp_unit": "ns",
        "filters": {
            "asset_class": "crypto",
            "exchange": "BINANCE",
            "venue_type": "futures_um",
            "symbol": "BTCUSDT",
            "data_type": "bar",
            "freq": "1m",
        },
        "start": start,
        "end": end,
    }


def funding_config(root: Path, start: str, end: str) -> dict[str, Any]:
    return {
        "mode": "hive_parquet_funding",
        "root": str(root),
        "instrument_id": "BTCUSDT-PERP.BINANCE",
        "timestamp_column": "ts",
        "timestamp_unit": "ns",
        "filters": {
            "asset_class": "crypto",
            "exchange": "BINANCE",
            "venue_type": "futures_um",
            "symbol": "BTCUSDT",
            "data_type": "funding_rate",
            "freq": "settlement",
        },
        "start": start,
        "end": end,
    }


def load_market_and_funding(
    market_root: Path, start: str, end: str
) -> tuple[list[BarEvent], pd.DataFrame]:
    _, stream = load_events(market_config(market_root, start, end))
    bars = list(stream)
    if not bars:
        raise ValueError("market stream is empty")
    _, funding_stream = load_events(funding_config(market_root, start, end))
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
    return bars, funding


def build_strategy_clock(bars_1m: list[BarEvent], frequency: str) -> list[BarEvent]:
    """Build completed N-minute observations independently of execution lag."""
    minutes = int(frequency.removesuffix("m"))
    source = bars_1m if minutes == 1 else resample_bars(bars_1m, frequency)
    return [
        replace(bar, event_time_ns=bar.event_time_ns + minutes * MINUTE_NS)
        for bar in source
    ]


def execution_bar(
    bars: list[BarEvent], event_times: np.ndarray, due_time_ns: int
) -> BarEvent | None:
    index = int(np.searchsorted(event_times, due_time_ns, side="left"))
    return bars[index] if index < len(bars) else None


def signed_direction(quantity: float) -> int:
    return 1 if quantity > 0 else -1 if quantity < 0 else 0


def report_for(fills: list[FillRecord]) -> ExecutionReport:
    return ExecutionReport(
        backend="timeframe_lag_experiment",
        total_intents=len(fills),
        total_fills=len(fills),
        fills=fills,
        positions=[],
        realized_pnl=0.0,
        unrealized_pnl=0.0,
        metadata={"mode": "simulated", "price": "actual 1m open"},
    )


def execute_planned(
    signal: PlannedSignal,
    event: BarEvent,
    simulator: IntentFillSimulator,
) -> list[FillRecord]:
    """Execute planned actions and return only the fills created by this signal.

    Ownership of the cumulative fill ledger stays with the lifecycle caller.  In
    particular, this helper must not append a fill to both a local and caller-
    owned collection as a side effect.
    """
    new_fills: list[FillRecord] = []
    for action in signal.actions:
        metadata = dict(action.metadata or {})
        if action.fill_price is not None:
            metadata["strategy_theoretical_fill_price"] = float(action.fill_price)
        if action.close_all:
            intent: OrderIntent | PositionIntent = PositionIntent(
                instrument_id=event.instrument_id,
                target="FLAT",
                quantity=0.0,
                event_time_ns=event.event_time_ns,
                reason=action.reason,
                metadata=metadata,
            )
        else:
            intent = OrderIntent(
                instrument_id=event.instrument_id,
                side=action.side,
                quantity=float(action.quantity),
                event_time_ns=event.event_time_ns,
                reason=action.reason,
                metadata=metadata,
            )
        fill = simulator.on_intent(intent, event)
        if fill is not None:
            new_fills.append(fill)
    return new_fills


def execute_target(
    target: int,
    current_quantity: float,
    event: BarEvent,
    simulator: IntentFillSimulator,
    fills: list[FillRecord],
) -> tuple[float, list[FillRecord]]:
    delta = float(target) - current_quantity
    if abs(delta) <= 1e-15:
        return current_quantity, []
    intent = OrderIntent(
        instrument_id=event.instrument_id,
        side="BUY" if delta > 0 else "SELL",
        quantity=abs(delta),
        event_time_ns=event.event_time_ns,
        reason=f"decision_target={target}",
        metadata={"target_direction": target},
    )
    fill = simulator.on_intent(intent, event)
    if fill is None:
        return current_quantity, []
    fills.append(fill)
    signed = float(fill.quantity) if fill.side == "BUY" else -float(fill.quantity)
    return current_quantity + signed, [fill]


def run_decision_lifecycle(
    *,
    strategy_name: str,
    source_config: dict[str, Any],
    frequency: str,
    lag_minutes: int,
    bars_1m: list[BarEvent],
    strategy_bars: list[BarEvent],
    end_exclusive_ns: int,
) -> tuple[np.ndarray, list[dict[str, Any]], dict[str, Any]]:
    plugin = get_entry(strategy_name)
    config_obj = _build_config_obj(plugin.config_cls, source_config.get("params", {}), frequency)
    strategy = plugin.strategy_cls(config_obj)
    runner = FeatureStrategyRunner(plugin.build_specs(config_obj), strategy)
    simulator = IntentFillSimulator(
        default_price_field="open", allow_short=True, backend="timeframe_lag_experiment"
    )
    market_times = np.fromiter((bar.event_time_ns for bar in bars_1m), dtype=np.int64)
    fills: list[FillRecord] = []
    current_quantity = 0.0
    current_direction = 0
    fallback_target = 0
    change_times: list[int] = []
    change_directions: list[int] = []
    audit_rows: list[dict[str, Any]] = []
    signal_count = 0
    dropped_tail = 0

    for event in strategy_bars:
        snapshot, signal = runner.on_event(event)
        signal_text = str(signal)
        if event.event_time_ns >= end_exclusive_ns:
            continue
        if signal_text != "HOLD":
            signal_count += 1
        actions = signal.actions if isinstance(signal, PlannedSignal) else ()
        decision_target = getattr(strategy, "decision_position", None)
        if decision_target is None and not actions:
            if signal_text == "BUY":
                fallback_target = 1
            elif signal_text == "SELL":
                fallback_target = -1
            decision_target = fallback_target

        needs_execution = bool(actions)
        if not actions and decision_target is not None:
            needs_execution = int(decision_target) != current_direction
        if not needs_execution:
            continue

        due_time_ns = event.event_time_ns + lag_minutes * MINUTE_NS
        fill_event = execution_bar(bars_1m, market_times, due_time_ns)
        if fill_event is None or fill_event.event_time_ns >= end_exclusive_ns:
            dropped_tail += 1
            continue
        before = current_direction
        if actions:
            new_fills = execute_planned(signal, fill_event, simulator)
            fills.extend(new_fills)
            if new_fills:
                for fill in new_fills:
                    signed = float(fill.quantity) if fill.side == "BUY" else -float(fill.quantity)
                    current_quantity += signed
        else:
            current_quantity, new_fills = execute_target(
                int(decision_target), current_quantity, fill_event, simulator, fills
            )
        current_direction = signed_direction(current_quantity)
        hook = getattr(strategy, "on_execution_report", None)
        if hook is not None and new_fills:
            hook(report_for(fills))
        if current_direction != before:
            change_times.append(fill_event.event_time_ns)
            change_directions.append(current_direction)
        audit_rows.append(
            {
                "strategy": strategy_name,
                "case": f"{frequency}_lag{lag_minutes}",
                "signal_time_ns": event.event_time_ns,
                "due_time_ns": due_time_ns,
                "fill_time_ns": fill_event.event_time_ns if new_fills else None,
                "observed_lag_ns": (
                    fill_event.event_time_ns - event.event_time_ns if new_fills else None
                ),
                "signal": signal_text,
                "action_count": len(actions),
                "fill_count": len(new_fills),
                "direction_before": before,
                "direction_after": current_direction,
                "decision_target": decision_target,
                "fill_price": fill_event.open if new_fills else None,
            }
        )

    direction = np.zeros(len(market_times), dtype=np.int8)
    if change_times:
        change_ts = np.asarray(change_times, dtype=np.int64)
        change_values = np.asarray(change_directions, dtype=np.int8)
        indices = np.searchsorted(change_ts, market_times, side="right") - 1
        valid = indices >= 0
        direction[valid] = change_values[indices[valid]]
    meta = {
        "signal_count": signal_count,
        "fill_count": len(fills),
        "direction_change_count": len(change_times),
        "dropped_tail": dropped_tail,
        "final_direction": int(current_direction),
    }
    return direction, audit_rows, meta


def write_case(
    *,
    strategy_name: str,
    case: str,
    direction: np.ndarray,
    lifecycle_meta: dict[str, Any],
    audit_rows: list[dict[str, Any]],
    bars: list[BarEvent],
    funding: pd.DataFrame,
    output_dir: Path,
    notional_usdt: float,
    slippage_bps: float,
    vip9_fee_bps: float,
    vip0_fee_bps: float,
) -> dict[str, dict[str, Any]]:
    event_time = np.fromiter((bar.event_time_ns for bar in bars), dtype=np.int64)
    market_open = np.fromiter((bar.open for bar in bars), dtype=np.float64)
    close = np.fromiter((bar.close for bar in bars), dtype=np.float64)
    combined = pd.DataFrame({"event_time_ns": event_time, "close": close})
    summaries: dict[str, dict[str, Any]] = {}
    for variant, multiplier in (("normal", 1), ("strict_reverse", -1)):
        equity = pd.DataFrame(
            {"event_time_ns": event_time, "close": close, "position": direction * multiplier}
        )
        result, summary = calculate_overlay(
            equity,
            funding,
            market_open,
            notional_usdt=notional_usdt,
            slippage_bps=slippage_bps,
            vip9_fee_bps=vip9_fee_bps,
            vip0_fee_bps=vip0_fee_bps,
            position_policy="strict_constant_notional",
        )
        for column in RESULT_COLUMNS:
            combined[f"{variant}_{column}"] = result[column].to_numpy(copy=False)
        summary.update(
            strategy=strategy_name,
            case=case,
            variant=variant,
            signal_count=lifecycle_meta["signal_count"],
            execution_fill_count=lifecycle_meta["fill_count"],
            direction_change_count=lifecycle_meta["direction_change_count"],
            dropped_tail=lifecycle_meta["dropped_tail"],
            execution_clock="1m",
            strategy_clock=case.split("_", 1)[0],
            execution_lag_minutes=int(case.rsplit("lag", 1)[1]),
            slippage_bps=slippage_bps,
        )
        summaries[variant] = summary

    output_dir.mkdir(parents=True, exist_ok=True)
    temporary = output_dir / "timeseries.parquet.tmp"
    combined.to_parquet(temporary, index=False, compression="zstd")
    os.replace(temporary, output_dir / "timeseries.parquet")
    (output_dir / "summary.json").write_text(
        json.dumps(summaries, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    with open(output_dir / "execution_events.csv", "w", newline="", encoding="utf-8") as fh:
        fieldnames = list(audit_rows[0]) if audit_rows else [
            "strategy", "case", "signal_time_ns", "due_time_ns", "fill_time_ns",
            "observed_lag_ns", "signal", "action_count", "fill_count",
            "direction_before", "direction_after", "decision_target", "fill_price",
        ]
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(audit_rows)
    return summaries


def run_strategy(
    strategy_name: str,
    *,
    source_root: Path,
    output_root: Path,
    bars_1m: list[BarEvent],
    strategy_clocks: dict[str, list[BarEvent]],
    funding: pd.DataFrame,
    cases: tuple[tuple[str, int], ...],
    args: argparse.Namespace,
) -> list[dict[str, Any]]:
    config_path = source_root / strategy_name / "fee_5bps" / "config.yaml"
    source_config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    end_exclusive_ns = int(pd.Timestamp(args.end, tz="UTC").value) + 86_400_000_000_000
    rows: list[dict[str, Any]] = []
    for frequency, lag_minutes in cases:
        case = f"{frequency}_lag{lag_minutes}"
        destination = output_root / strategy_name / case
        summary_path = destination / "summary.json"
        timeseries_path = destination / "timeseries.parquet"
        if not args.overwrite and summary_path.is_file() and timeseries_path.is_file():
            summaries = json.loads(summary_path.read_text(encoding="utf-8"))
        else:
            direction, audit_rows, lifecycle_meta = run_decision_lifecycle(
                strategy_name=strategy_name,
                source_config=source_config,
                frequency=frequency,
                lag_minutes=lag_minutes,
                bars_1m=bars_1m,
                strategy_bars=strategy_clocks[frequency],
                end_exclusive_ns=end_exclusive_ns,
            )
            summaries = write_case(
                strategy_name=strategy_name,
                case=case,
                direction=direction,
                lifecycle_meta=lifecycle_meta,
                audit_rows=audit_rows,
                bars=bars_1m,
                funding=funding,
                output_dir=destination,
                notional_usdt=args.notional_usdt,
                slippage_bps=args.slippage_bps,
                vip9_fee_bps=args.vip9_fee_bps,
                vip0_fee_bps=args.vip0_fee_bps,
            )
        rows.extend(summaries.values())
        print(
            f"COMPLETE {strategy_name} {case} "
            f"signals={summaries['normal']['signal_count']} "
            f"fills={summaries['normal']['execution_fill_count']}",
            flush=True,
        )
    return rows


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def main() -> int:
    args = parse_args()
    cases = parse_cases(args.cases)
    if args.shard_count < 1 or not 0 <= args.shard_index < args.shard_count:
        raise ValueError("invalid shard index/count")
    if args.notional_usdt <= 0:
        raise ValueError("notional must be positive")
    available = discover_strategies(args.source_root)
    selected = args.strategies or available
    missing = sorted(set(selected) - set(available))
    if missing:
        raise ValueError(f"strategies unavailable: {missing}")
    selected = sorted(selected)[args.shard_index :: args.shard_count]
    args.output_root.mkdir(parents=True, exist_ok=True)
    progress_path = args.output_root / f"progress_shard_{args.shard_index}.json"
    atomic_json(
        progress_path,
        {
            "status": "loading_market",
            "shard_index": args.shard_index,
            "shard_count": args.shard_count,
            "strategy_count": len(selected),
            "completed": 0,
        },
    )
    bars_1m, funding = load_market_and_funding(
        args.market_root, args.start, args.end
    )
    strategy_clocks = {
        frequency: build_strategy_clock(bars_1m, frequency)
        for frequency in sorted({frequency for frequency, _ in cases})
    }
    rows: list[dict[str, Any]] = []
    started = datetime.now(UTC)
    for index, strategy_name in enumerate(selected, start=1):
        atomic_json(
            progress_path,
            {
                "status": "running",
                "shard_index": args.shard_index,
                "shard_count": args.shard_count,
                "strategy": strategy_name,
                "strategy_count": len(selected),
                "completed": index - 1,
                "started_at_utc": started.isoformat(),
            },
        )
        rows.extend(
            run_strategy(
                strategy_name,
                source_root=args.source_root,
                output_root=args.output_root,
                bars_1m=bars_1m,
                strategy_clocks=strategy_clocks,
                funding=funding,
                cases=cases,
                args=args,
            )
        )
    pd.DataFrame(rows).to_csv(
        args.output_root / f"evaluation_shard_{args.shard_index}.csv", index=False
    )
    atomic_json(
        progress_path,
        {
            "status": "complete",
            "shard_index": args.shard_index,
            "shard_count": args.shard_count,
            "strategy_count": len(selected),
            "completed": len(selected),
            "finished_at_utc": datetime.now(UTC).isoformat(),
        },
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
