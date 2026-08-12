#!/usr/bin/env python3
"""Run the continuous event-time MA strategy through existing platform contracts.

The market reader is invoked one canonical date partition at a time so feature
state can remain continuous without loading five years of ticks at once.  Trade
events still enter through ``data_engine.load_events``; features through
``FeatureStrategyRunner``; signals through the registered strategy plugin; and
fills/PnL through the existing execution simulator and report writer.
"""
from __future__ import annotations

import argparse
import csv
import gc
import json
import math
import time
from dataclasses import asdict, dataclass, replace
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from data_engine.loader import load_events
from feature_engine.runner import FeatureStrategyRunner
from results.strategy_evaluation import (
    build_strategy_evaluation,
    render_strategy_evaluation,
    validate_strategy_evaluation,
)
from results.trade_episode import (
    build_de_risk_episodes,
    render_episode_break_even,
    write_episode_csv,
)
from strategy_framework.backends.nautilus_simulation import IntentFillSimulator
from strategy_framework.execution.backtest_report import (
    write_artifact_manifest,
    write_backtest_report,
)
from strategy_framework.execution.duration_lag import DurationLagTargetAdapter
from strategy_framework.registry import get_entry

MINUTE_NS = 60_000_000_000
ACTIONABLE = {"BUY", "SELL"}


@dataclass(frozen=True)
class ExperimentConfig:
    market_root: str
    output_root: str
    start: str
    end: str
    instrument_id: str = "BTCUSDT-PERP.BINANCE"
    notional_usdt: float = 100_000.0
    lag_ns: int = MINUTE_NS
    fee_bps_vip9_taker: float = 1.7
    fee_bps_vip0_taker: float = 5.0
    slippage_bps: float = 0.0


@dataclass
class _Variant:
    lag_name: str
    direction_name: str
    lag_ns: int
    reverse: bool
    adapter: DurationLagTargetAdapter
    simulator: IntentFillSimulator
    fills: list[Any]
    intents: list[dict[str, Any]]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--start", default="2021-07-01")
    parser.add_argument("--end", default="2026-06-30")
    parser.add_argument("--lag-seconds", type=float, default=60.0)
    parser.add_argument("--notional-usdt", type=float, default=100_000.0)
    parser.add_argument(
        "--compare-lag-zero",
        action="store_true",
        help="run lag=0 and --lag-seconds in one shared signal pass",
    )
    parser.add_argument(
        "--include-reverse",
        action="store_true",
        help="also run the redundant sign-inverted execution control",
    )
    return parser.parse_args()


def _dates(start: str, end: str):
    cursor = date.fromisoformat(start)
    last = date.fromisoformat(end)
    while cursor <= last:
        yield cursor.isoformat()
        cursor += timedelta(days=1)


def _trade_config(config: ExperimentConfig, day: str) -> dict[str, Any]:
    return {
        "mode": "hive_parquet_trades",
        "root": config.market_root,
        "instrument_id": config.instrument_id,
        "start": day,
        "end": day,
        "warmup": 0,
        "filters": {
            "asset_class": "crypto",
            "exchange": "BINANCE",
            "venue_type": "futures_um",
            "symbol": "BTCUSDT",
            "data_type": "trade",
            "freq": "tick",
        },
    }


def _funding_config(config: ExperimentConfig) -> dict[str, Any]:
    return {
        "mode": "hive_parquet_funding",
        "root": config.market_root,
        "instrument_id": config.instrument_id,
        "start": config.start,
        "end": config.end,
        "filters": {
            "asset_class": "crypto",
            "exchange": "BINANCE",
            "venue_type": "futures_um",
            "symbol": "BTCUSDT",
            "data_type": "funding_rate",
            "freq": "settlement",
        },
    }


def _mark(ts_ns: int, instrument_id: str, price: float) -> dict[str, Any]:
    return {
        "event_time_ns": int(ts_ns),
        "instrument_id": instrument_id,
        "close": float(price),
    }


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    tmp.replace(path)


def _intent_row(intent: Any) -> dict[str, Any]:
    return {
        "event_time_ns": intent.event_time_ns,
        "instrument_id": intent.instrument_id,
        "action": intent.side,
        "quantity": intent.quantity,
        "reason": intent.reason,
    }


def _summary_rows(
    variant: _Variant,
    metrics: dict[str, Any],
    config: ExperimentConfig,
    evaluation: dict[str, dict[str, float]],
    output_dir: Path,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for premium, values in evaluation.items():
        rows.append(
            {
                "strategy": "continuous_tick_ma",
                "direction": variant.direction_name,
                "start": config.start,
                "end": config.end,
                "lag_ns": variant.lag_ns,
                "lag_definition": (
                    "first following TradeEvent"
                    if variant.lag_ns == 0
                    else f"first TradeEvent at or after signal_time + {variant.lag_ns}ns"
                ),
                "premium": premium,
                "notional_usdt": config.notional_usdt,
                "leverage": 1.0,
                "signal_count": metrics["signal_count"],
                "fill_count": metrics["fill_count"],
                "final_return_1x": values["final_return_1x"],
                "turnover": values["turnover"],
                "break_even_bps": values["break_even_bps"],
                "max_drawdown": values["max_drawdown"],
                "funding_pnl": metrics["funding_pnl"],
                "funding_event_count": metrics["funding_event_count"],
                "output_dir": str(output_dir),
            }
        )
    return rows


def run(
    config: ExperimentConfig,
    *,
    compare_lag_zero: bool = False,
    include_reverse: bool = False,
) -> Path:
    if config.notional_usdt <= 0 or config.lag_ns < 0:
        raise ValueError("notional must be positive and lag_ns non-negative")
    out = Path(config.output_root)
    out.mkdir(parents=True, exist_ok=True)

    plugin = get_entry("continuous_tick_ma")
    strategy_config = plugin.config_cls()
    strategy = plugin.strategy_cls(strategy_config)
    specs = plugin.build_specs(strategy_config)
    runner = FeatureStrategyRunner(
        specs,
        strategy,
        engine_kwargs={"is_live": False},
    )

    lag_values = [config.lag_ns]
    if compare_lag_zero and config.lag_ns != 0:
        lag_values.insert(0, 0)
    variants: list[_Variant] = []
    for lag_ns in lag_values:
        for reverse in ((False, True) if include_reverse else (False,)):
            variants.append(
                _Variant(
                    lag_name=f"lag_{lag_ns / 1e9:g}s",
                    direction_name="strict_reverse" if reverse else "normal",
                    lag_ns=lag_ns,
                    reverse=reverse,
                    adapter=DurationLagTargetAdapter(
                        lag_ns=lag_ns,
                        notional=config.notional_usdt,
                        reverse=reverse,
                    ),
                    simulator=IntentFillSimulator(
                        default_price_field="price", allow_short=True
                    ),
                    fills=[],
                    intents=[],
                )
            )

    _, funding_stream = load_events(_funding_config(config))
    funding_source = list(funding_stream)
    resolved_funding: list[Any] = []
    funding_index = 0

    signals: list[dict[str, Any]] = []
    signal_times: list[int] = []
    marks: list[dict[str, Any]] = []
    first_ready_points: list[dict[str, Any]] = []
    last_event: Any | None = None
    last_minute: int | None = None
    total_events = 0
    started = time.monotonic()

    audit_path = out / "execution_audit.csv"
    audit_fields = [
        "lag_ns", "direction", "signal", "signal_time_ns", "due_time_ns", "fill_time_ns",
        "observed_lag_ns", "lag_overshoot_ns", "side", "quantity", "fill_price",
        "position_before", "position_after", "position_notional_after",
        "target_notional_error",
    ]
    with audit_path.open("w", newline="", encoding="utf-8") as audit_handle:
        audit_writer = csv.DictWriter(audit_handle, fieldnames=audit_fields)
        audit_writer.writeheader()

        day_list = list(_dates(config.start, config.end))
        for day_number, day in enumerate(day_list, start=1):
            _, event_stream = load_events(_trade_config(config, day))
            events = list(event_stream)
            for event in events:
                ts_ns = int(event.event_time_ns)

                # Resolve each settlement mark from the latest TradeEvent at or
                # before the settlement, never from a future minute close.
                while (
                    funding_index < len(funding_source)
                    and funding_source[funding_index].event_time_ns < ts_ns
                ):
                    funding = funding_source[funding_index]
                    # At the experiment's first boundary there may be no prior
                    # tick in scope (e.g. settlement at 00:00:00.005 and the
                    # first archived aggTrade a few ms later).  Only for that
                    # boundary use the first available trade; every later
                    # settlement uses the latest trade at or before it.
                    mark_price = float(
                        last_event.price if last_event is not None else event.price
                    )
                    resolved_funding.append(replace(funding, mark_price=mark_price))
                    marks.append(_mark(funding.event_time_ns, config.instrument_id, mark_price))
                    funding_index += 1

                minute = ts_ns // MINUTE_NS
                if last_event is not None and minute != last_minute:
                    marks.append(
                        _mark(last_event.event_time_ns, config.instrument_id, last_event.price)
                    )

                # Execution observes this event before the strategy can schedule
                # a signal from it. Even lag=0 therefore means the next trade.
                for variant in variants:
                    attempts = variant.adapter.on_market_event(event, variant.simulator.on_intent)
                    for attempt in attempts:
                        if attempt.intent is not None:
                            variant.intents.append(_intent_row(attempt.intent))
                        if attempt.fill is not None:
                            variant.fills.append(attempt.fill)
                            # Preserve the exact executable event in the report
                            # clock so episode completion is not rounded to a
                            # later minute mark.
                            marks.append(
                                _mark(
                                    attempt.fill_time_ns,
                                    config.instrument_id,
                                    attempt.fill.price,
                                )
                            )
                        notional_after = abs(attempt.position_after * attempt.price)
                        audit_writer.writerow({
                            "lag_ns": variant.lag_ns,
                            "direction": variant.direction_name,
                            "signal": attempt.target.signal,
                            "signal_time_ns": attempt.target.signal_time_ns,
                            "due_time_ns": attempt.target.due_time_ns,
                            "fill_time_ns": attempt.fill_time_ns,
                            "observed_lag_ns": attempt.observed_lag_ns,
                            "lag_overshoot_ns": attempt.fill_time_ns - attempt.target.due_time_ns,
                            "side": attempt.intent.side if attempt.intent else None,
                            "quantity": attempt.intent.quantity if attempt.intent else 0.0,
                            "fill_price": attempt.fill.price if attempt.fill else None,
                            "position_before": attempt.position_before,
                            "position_after": attempt.position_after,
                            "position_notional_after": notional_after,
                            "target_notional_error": notional_after - config.notional_usdt,
                        })

                snapshot, signal = runner.on_event(event)
                fast = snapshot.value(strategy_config.fast_name)
                slow = snapshot.value(strategy_config.slow_name)
                if fast is not None and slow is not None and len(first_ready_points) < 6:
                    first_ready_points.append({
                        "event_time_ns": ts_ns,
                        "price": event.price,
                        strategy_config.fast_name: fast,
                        strategy_config.slow_name: slow,
                        "source_event_time_match": all(
                            snapshot.values[name].source_event_time_ns == ts_ns
                            for name in (strategy_config.fast_name, strategy_config.slow_name)
                        ),
                    })
                if str(signal) in ACTIONABLE:
                    signal_times.append(ts_ns)
                    signals.append({
                        "event_time_ns": ts_ns,
                        "instrument_id": config.instrument_id,
                        "signal": str(signal),
                        "close": float(event.price),
                        strategy_config.fast_name: fast,
                        strategy_config.slow_name: slow,
                    })
                    for variant in variants:
                        variant.adapter.schedule(event, str(signal))

                total_events += 1
                last_event = event
                last_minute = minute

            elapsed = max(time.monotonic() - started, 1e-9)
            _atomic_json(out / "progress.json", {
                "status": "running",
                "day": day,
                "processed_days": day_number,
                "total_days": len(day_list),
                "processed_events": total_events,
                "signals": len(signals),
                "fills": {
                    f"{variant.lag_name}/{variant.direction_name}": len(variant.fills)
                    for variant in variants
                },
                "events_per_second": total_events / elapsed,
            })
            del events, event_stream
            gc.collect()

        if last_event is not None:
            marks.append(_mark(last_event.event_time_ns, config.instrument_id, last_event.price))
            while (
                funding_index < len(funding_source)
                and funding_source[funding_index].event_time_ns <= last_event.event_time_ns
            ):
                funding = funding_source[funding_index]
                mark_price = float(last_event.price)
                resolved_funding.append(replace(funding, mark_price=mark_price))
                marks.append(_mark(funding.event_time_ns, config.instrument_id, mark_price))
                funding_index += 1

    if last_event is None:
        raise RuntimeError("no trade events loaded")

    # Contract invariants before accounting.
    # Binance aggTrades can share the same exchange millisecond timestamp, so
    # event time is required to be non-decreasing (trade order inside an equal
    # timestamp is already preserved by the canonical source), not strictly
    # increasing.
    if any(b < a for a, b in zip(signal_times, signal_times[1:])):
        raise RuntimeError("signal timestamps are decreasing")
    gaps = [b - a for a, b in zip(signal_times, signal_times[1:])]
    if len(gaps) > 1 and len(set(gaps)) == 1:
        raise RuntimeError("signal timestamps unexpectedly form a fixed interval")
    inverse_fill_match: bool | None = None
    if include_reverse:
        inverse_checks = []
        for lag_ns in lag_values:
            normal = next(
                variant for variant in variants if variant.lag_ns == lag_ns and not variant.reverse
            )
            reverse = next(
                variant for variant in variants if variant.lag_ns == lag_ns and variant.reverse
            )
            inverse_checks.append(
                len(normal.fills) == len(reverse.fills)
                and all(
                    left.event_time_ns == right.event_time_ns
                    and left.side != right.side
                    and math.isclose(
                        left.quantity, right.quantity, rel_tol=1e-12, abs_tol=1e-12
                    )
                    and left.price == right.price
                    for left, right in zip(normal.fills, reverse.fills)
                )
            )
        inverse_fill_match = all(inverse_checks)
        if not inverse_fill_match:
            raise RuntimeError("strict reverse fills are not exact opposites")

    _atomic_json(out / "signal_gap_audit.json", {
        "signal_count": len(signal_times),
        "gap_count": len(gaps),
        "unique_gap_count": len(set(gaps)),
        "min_gap_ns": min(gaps) if gaps else None,
        "max_gap_ns": max(gaps) if gaps else None,
        "irregular": len(set(gaps)) > 1,
    })
    _atomic_json(out / "feature_timestamp_audit.json", {
        "feature_semantics": "trade-count-weighted arithmetic mean over (t-window, t]",
        "points": first_ready_points,
        "all_source_timestamps_match": all(
            point["source_event_time_match"] for point in first_ready_points
        ),
    })

    config_payload = asdict(config)
    config_payload.update({
        "feature_names": [spec.name for spec in specs],
        "signal_contract": "BUY/SELL/HOLD",
        "execution_timing": "first TradeEvent with event_time_ns >= signal_time_ns + lag_ns",
        "compared_lag_ns": lag_values,
        "include_reverse": include_reverse,
        "turnover_formula": "sum(abs(fill_quantity * fill_price)) / 100000",
        "signed_breakeven_bps_formula": "arithmetic_return / turnover * 10000",
    })
    rows: list[dict[str, Any]] = []
    report_results = []
    marks.sort(key=lambda row: row["event_time_ns"])
    for variant in variants:
        if compare_lag_zero or include_reverse:
            variant_dir = out / variant.lag_name / variant.direction_name / "nofee"
        else:
            variant_dir = out / variant.direction_name / "nofee"
        run_name = (
            f"continuous_tick_ma/BTCUSDT/{variant.lag_name}/"
            f"{variant.direction_name}/nofee"
        )
        result = write_backtest_report(
            output_dir=variant_dir,
            run_name=run_name,
            mode="simulated",
            backend="nautilus_backtest",
            initial_cash=config.notional_usdt,
            bars=marks,
            signals=signals,
            intents=variant.intents,
            fills=variant.fills,
            feature_names=[spec.name for spec in specs],
            fee_rate=0.0,
            slippage_bps=config.slippage_bps,
            fill_timing="duration_lag",
            execution_stats={
                "original_intent_count": len(variant.intents),
                "executed_intent_count": len(variant.fills),
                "dropped_tail_intents": variant.adapter.pending_count,
            },
            funding_events=resolved_funding,
            config=config_payload,
        )
        report_results.append(result)
        evaluation_series, evaluation_metrics = build_strategy_evaluation(
            result.equity_curve,
            variant.fills,
            initial_cash=config.notional_usdt,
        )
        validation = validate_strategy_evaluation(evaluation_series, evaluation_metrics)
        render_strategy_evaluation(
            evaluation_series,
            evaluation_metrics,
            output_dir=variant_dir,
            run_name=run_name,
            lag_ns=variant.lag_ns,
        )
        episode_rows: list[dict[str, Any]] = []
        episode_summaries: dict[str, dict[str, Any]] = {}
        positions = [float(row["position"]) for row in evaluation_series]
        cumulative_turnover = [
            float(row["cumulative_turnover"]) for row in evaluation_series
        ]
        turnover_increment = [
            value - (cumulative_turnover[index - 1] if index else 0.0)
            for index, value in enumerate(cumulative_turnover)
        ]
        for premium, return_column in (
            ("included", "return_with_premium"),
            ("excluded", "return_without_premium"),
        ):
            cumulative_return = [
                float(row[return_column]) for row in evaluation_series
            ]
            return_increment = [
                value - (cumulative_return[index - 1] if index else 0.0)
                for index, value in enumerate(cumulative_return)
            ]
            premium_rows, premium_summary = build_de_risk_episodes(
                event_time_ns=[
                    int(row["event_time_ns"]) for row in evaluation_series
                ],
                executed_position=positions,
                turnover_increment=turnover_increment,
                gross_return_increment=return_increment,
                strategy="continuous_tick_ma",
                symbol="BTCUSDT",
                granularity="native trade tick",
                lag=(
                    "0s physical-time (first following TradeEvent)"
                    if variant.lag_ns == 0
                    else f"{variant.lag_ns / 1e9:g}s physical-time"
                ),
                premium_mode=premium,
            )
            episode_rows.extend(premium_rows)
            episode_summaries[premium] = premium_summary
        write_episode_csv(variant_dir / "per_trade_break_even.csv", episode_rows)
        render_episode_break_even(
            episode_rows,
            destination=variant_dir / "charts" / "per_trade_break_even.png",
            title=(
                "continuous_tick_ma/BTCUSDT/native tick — Per-Episode "
                f"Break-even Cost (lag={variant.lag_ns / 1e9:g}s physical-time)"
            ),
        )
        _atomic_json(
            variant_dir / "per_trade_break_even_summary.json", episode_summaries
        )
        _atomic_json(variant_dir / "strategy_evaluation_validation.json", validation)
        write_artifact_manifest(variant_dir, run_name)
        rows.extend(
            _summary_rows(
                variant,
                result.metrics,
                config,
                evaluation_metrics,
                variant_dir,
            )
        )

    if len(resolved_funding) != len(funding_source):
        raise RuntimeError(
            f"funding coverage mismatch: {len(resolved_funding)} != {len(funding_source)}"
        )
    funding_inverse: bool | None = None
    if include_reverse:
        funding_inverse = all(
            math.isclose(
                report_results[index].metrics["funding_pnl"],
                -report_results[index + 1].metrics["funding_pnl"],
                rel_tol=1e-10,
                abs_tol=1e-8,
            )
            for index in range(0, len(report_results), 2)
        )
        if not funding_inverse:
            raise RuntimeError("strict reverse funding PnL is not the exact opposite")

    evaluation_path = out / "evaluation_table.csv"
    with evaluation_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    _atomic_json(out / "control_validation.json", {
        "signal_stream_shared": True,
        "lag_comparison_changes_only_lag": compare_lag_zero and not include_reverse,
        "premium_comparison_changes_only_funding_treatment": True,
        "strict_reverse_fill_match": inverse_fill_match,
        "strict_reverse_funding_match": funding_inverse,
        "final_position_qty": {
            f"{variant.lag_name}/{variant.direction_name}": variant.adapter.position_qty
            for variant in variants
        },
        "funding_source_events": len(funding_source),
        "funding_resolved_events": len(resolved_funding),
        "pending_targets": {
            f"{variant.lag_name}/{variant.direction_name}": variant.adapter.pending_count
            for variant in variants
        },
    })
    _atomic_json(out / "progress.json", {
        "status": "complete",
        "processed_days": len(list(_dates(config.start, config.end))),
        "processed_events": total_events,
        "signals": len(signals),
        "fills": {
            f"{variant.lag_name}/{variant.direction_name}": len(variant.fills)
            for variant in variants
        },
        "evaluation_table": str(evaluation_path),
    })
    return evaluation_path


def main() -> None:
    args = _parse_args()
    config = ExperimentConfig(
        market_root=str(args.market_root),
        output_root=str(args.output_root),
        start=args.start,
        end=args.end,
        notional_usdt=float(args.notional_usdt),
        lag_ns=int(args.lag_seconds * 1_000_000_000),
    )
    print(
        run(
            config,
            compare_lag_zero=args.compare_lag_zero,
            include_reverse=args.include_reverse,
        )
    )


if __name__ == "__main__":
    main()
