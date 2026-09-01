#!/usr/bin/env python3
"""Run the isolated workbook multi-timeframe / exact raw-tick screen."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data_engine.events import BarEvent  # noqa: E402
from data_engine.loader import load_events  # noqa: E402
from results.trade_episode import build_de_risk_episodes  # noqa: E402
from scripts.internal.build_phase4a_baseline_evaluation import drawdown  # noqa: E402
from scripts.internal.run_all_strategy_timeframe_lag import (  # noqa: E402
    build_strategy_clock,
    run_decision_lifecycle,
)
from scripts.internal.run_constant_notional_overlay import calculate_overlay  # noqa: E402
from strategy_framework.registry import get_entry  # noqa: E402


SYMBOLS = (
    "XRPUSDT", "DOGEUSDT", "SUIUSDT", "BNBUSDT", "ETHUSDT",
    "BTCUSDT", "1000PEPEUSDT", "SOLUSDT", "ADAUSDT",
)
TIMEFRAMES = ("10m", "15m", "5m", "1m")
NOTIONAL = 100_000.0
PROVENANCE_KEYS = {
    "source_registry_id", "semantic_provenance", "contracts_applied", "defaulted_parameters",
}


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False, default=str) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def event_config(root: Path, symbol: str, data_type: str, start: str, end: str) -> dict[str, Any]:
    funding = data_type == "funding_rate"
    return {
        "mode": "hive_parquet_funding" if funding else "hive_parquet_bars",
        "root": str(root),
        "instrument_id": f"{symbol}-PERP.BINANCE",
        "warmup_bars": 0,
        "timestamp_column": "ts",
        "timestamp_unit": "ns",
        "filters": {
            "asset_class": "crypto", "exchange": "BINANCE", "venue_type": "futures_um",
            "symbol": symbol, "data_type": data_type,
            "freq": "settlement" if funding else "1m",
        },
        "start": start,
        "end": end,
    }


def load_symbol(
    market_root: Path, index_root: Path, symbol: str, start: str, end_inclusive: str
) -> tuple[list[BarEvent], pd.DataFrame, list[BarEvent], np.ndarray, np.ndarray]:
    _, stream = load_events(event_config(market_root, symbol, "bar", start, end_inclusive))
    bars = list(stream)
    expected = (pd.Timestamp(end_inclusive) - pd.Timestamp(start)).days * 1440 + 1440
    if len(bars) != expected:
        raise ValueError(f"{symbol}: expected {expected} 1m bars, got {len(bars)}")
    bar_times = np.fromiter((bar.event_time_ns for bar in bars), dtype=np.int64)
    if np.any(np.diff(bar_times) != 60_000_000_000):
        raise ValueError(f"{symbol}: canonical 1m bar clock is incomplete")
    _, funding_stream = load_events(
        event_config(market_root, symbol, "funding_rate", start, end_inclusive)
    )
    funding_events = list(funding_stream)
    funding = pd.DataFrame(
        {
            "event_time_ns": [event.event_time_ns for event in funding_events],
            "mark_price": [event.mark_price or 0.0 for event in funding_events],
            "funding_rate": [event.funding_rate for event in funding_events],
        }
    )
    paths = sorted(
        path for path in (index_root / f"symbol={symbol}").glob("date=*/*.parquet")
        if start <= path.parent.name.removeprefix("date=") <= end_inclusive
    )
    if len(paths) != (pd.Timestamp(end_inclusive) - pd.Timestamp(start)).days + 1:
        raise ValueError(f"{symbol}: incomplete tick execution index partitions")
    index = pd.read_parquet(paths)
    boundary_ns = pd.to_datetime(index.minute_boundary_timestamp, utc=True).array.as_unit("ns").asi8
    trade_ns = pd.to_datetime(index.first_trade_timestamp, utc=True).array.as_unit("ns").asi8
    if not np.array_equal(boundary_ns, bar_times):
        raise ValueError(f"{symbol}: tick index boundary does not align with canonical 1m bars")
    if np.any(trade_ns < boundary_ns):
        raise ValueError(f"{symbol}: tick index contains pre-decision execution")
    prices = index.price.to_numpy(dtype=np.float64, copy=False)
    if not np.isfinite(prices).all() or np.any(prices <= 0):
        raise ValueError(f"{symbol}: invalid tick execution price")
    execution = [
        BarEvent(
            close=float(price), open=float(price), high=float(price), low=float(price),
            volume=float(quantity), instrument_id=bars[i].instrument_id,
            event_time_ns=int(trade_ns[i]), quote_volume=float(quote), trade_count=1,
        )
        for i, (price, quantity, quote) in enumerate(
            zip(prices, index.quantity.to_numpy(float), index.quote_quantity.to_numpy(float), strict=True)
        )
    ]
    waits = index.wait_ms.to_numpy(dtype=np.int64, copy=False)
    return bars, funding, execution, prices, waits


def strategy_scope(scope_path: Path) -> list[str]:
    frame = pd.read_csv(scope_path)
    eligible = frame.eligible_for_intraday_resample
    if eligible.dtype != bool:
        eligible = eligible.astype(str).str.lower().eq("true")
    values = sorted(frame.loc[eligible, "strategy_id"].astype(str))
    if len(values) != 267 or len(set(values)) != 267:
        raise ValueError(f"expected 267 eligible strategies, found {len(values)}")
    return values


def semantic_identity(strategy_id: str) -> tuple[str, dict[str, Any]]:
    source = yaml.safe_load((ROOT / "strategies" / strategy_id / "config.yaml").read_text(encoding="utf-8")) or {}
    params = {key: value for key, value in source.get("params", {}).items() if key not in PROVENANCE_KEYS}
    plugin = get_entry(strategy_id)
    strategy_contract = plugin.strategy_cls.__mro__[1]
    config_contract = plugin.config_cls.__mro__[1]
    payload = {
        "strategy_contract": f"{strategy_contract.__module__}.{strategy_contract.__qualname__}",
        "config_contract": f"{config_contract.__module__}.{config_contract.__qualname__}",
        "build_specs": f"{plugin.build_specs.__module__}.{plugin.build_specs.__qualname__}",
        "params": params,
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()
    return digest, source


def semantic_groups(strategies: list[str]) -> list[tuple[str, list[str], dict[str, Any]]]:
    grouped: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for strategy in strategies:
        digest, source = semantic_identity(strategy)
        grouped.setdefault(digest, []).append((strategy, source))
    return [
        (digest, [item[0] for item in members], members[0][1])
        for digest, members in sorted(grouped.items())
    ]


def persistence_metrics(direction: np.ndarray) -> dict[str, Any]:
    values = np.asarray(direction, dtype=np.float64)
    signs = np.sign(values).astype(np.int8)
    long = signs > 0
    short = signs < 0
    flat = signs == 0
    changes = np.flatnonzero(np.r_[True, signs[1:] != signs[:-1]])
    ends = np.r_[changes[1:], len(signs)]
    run_signs = signs[changes]
    durations = (ends - changes) * 60.0
    held = durations[run_signs != 0]
    transitions = signs[1:] != signs[:-1]
    sign_switches = (signs[1:] * signs[:-1]) < 0
    return {
        "long_fraction": float(long.mean()),
        "short_fraction": float(short.mean()),
        "flat_fraction": float(flat.mean()),
        "nonflat_fraction": float((~flat).mean()),
        "long_short_balance": float(abs(long.mean() - short.mean())),
        "position_change_count": int(transitions.sum()),
        "sign_switch_count": int(sign_switches.sum()),
        "direct_reversal_count": int(sign_switches.sum()),
        "median_holding_duration_seconds": float(np.median(held)) if len(held) else 0.0,
        "p90_holding_duration_seconds": float(np.quantile(held, 0.9)) if len(held) else 0.0,
        "near_always_in_market": bool((~flat).mean() >= 0.90),
        "strongly_directionally_biased": bool(max(long.mean(), short.mean()) >= 0.90),
    }


def tick_wait_metrics(boundary_times: np.ndarray, waits: np.ndarray, timeframe: str) -> dict[str, Any]:
    minutes = pd.to_datetime(boundary_times, unit="ns", utc=True).minute.to_numpy()
    interval = int(timeframe.removesuffix("m"))
    selected = waits[minutes % interval == 0]
    return {
        "first_tick_wait_median_ms": float(np.median(selected)),
        "first_tick_wait_p95_ms": float(np.quantile(selected, 0.95)),
        "first_tick_wait_p99_ms": float(np.quantile(selected, 0.99)),
        "first_tick_wait_max_ms": int(selected.max()),
    }


def review_sample_indices(
    executed_position: pd.Series | np.ndarray,
    drawdown_values: np.ndarray,
) -> np.ndarray:
    """Retain daily review points and every actual position transition."""
    position = np.asarray(executed_position, dtype=np.float64)
    changes = np.flatnonzero(np.r_[True, position[1:] != position[:-1]])
    return np.unique(
        np.r_[
            np.arange(0, len(position), 1440),
            changes,
            np.maximum(changes - 1, 0),
            len(position) - 1,
            int(np.argmin(drawdown_values)),
        ]
    )


def run_group_case(
    *, representative: str, members: list[str], source: dict[str, Any], semantic_hash: str,
    symbol: str, timeframe: str, bars: list[BarEvent], funding: pd.DataFrame,
    execution: list[BarEvent], tick_prices: np.ndarray, waits: np.ndarray, end_ns: int,
) -> tuple[dict[str, Any], pd.DataFrame]:
    clock = build_strategy_clock(bars, timeframe)
    direction, audit, lifecycle = run_decision_lifecycle(
        strategy_name=representative, source_config=source, frequency=timeframe, lag_minutes=0,
        bars_1m=bars, strategy_bars=clock, execution_events=execution,
        end_exclusive_ns=end_ns,
    )
    event_time = np.fromiter((bar.event_time_ns for bar in bars), dtype=np.int64)
    close = np.fromiter((bar.close for bar in bars), dtype=np.float64)
    result, accounting = calculate_overlay(
        pd.DataFrame({"event_time_ns": event_time, "close": close, "position": direction}),
        funding, tick_prices, notional_usdt=NOTIONAL, slippage_bps=0.0,
        vip9_fee_bps=0.0, vip0_fee_bps=5.0, position_policy="strict_constant_notional",
    )
    episode_rows, episode_summary = build_de_risk_episodes(
        event_time_ns=result.event_time_ns, executed_position=result.direction,
        turnover_increment=result.turnover, gross_return_increment=result.total_return,
        strategy=representative, symbol=symbol, granularity=timeframe,
        lag="tick_no_added_lag", premium_mode="included", variant="original",
    )
    total_return = accounting["total_simple_return_fee0"]
    turnover = accounting["total_turnover_x"]
    waits_summary = tick_wait_metrics(event_time, waits, timeframe)
    summary = {
        "status": "COMPLETED", "representative_strategy_id": representative,
        "member_strategy_ids": ";".join(members), "semantic_execution_hash": semantic_hash,
        "symbol": symbol, "timeframe": timeframe, "Return_fee0": total_return,
        "Return_no_premium": accounting["trading_simple_return"],
        "Return_5bp": total_return - turnover * 5.0 / 10_000.0,
        "Turnover_raw": turnover, "Turnover_pct": turnover * 100.0,
        "BE_bps": accounting["breakeven_fee_bps"],
        "MDD": drawdown(result.total_return.to_numpy(float)),
        "episode_count": len(episode_rows), "open_episode_count": episode_summary.get("open_episode_count", 0),
        "decision_count": len(clock), "target_position_change_count": lifecycle["direction_change_count"],
        "tick_execution_count": lifecycle["fill_count"], "no_added_lag": True,
        "tick_source": "official_binance_raw_trades", "funding": "included",
        "max_boundary_notional_error_usdt": accounting["max_boundary_notional_error_usdt"],
        "accounting_identity_max_error": accounting["accounting_identity_max_error"],
        "first_tick_lookup_predecision_count": int(sum(row["fill_time_ns"] < row["due_time_ns"] for row in audit if row["fill_count"])),
        **persistence_metrics(direction), **waits_summary,
    }
    increments = result.total_return.to_numpy(dtype=np.float64, copy=False)
    cumulative = np.cumsum(increments, dtype=np.float64)
    equity = 1.0 + cumulative
    peak = np.maximum.accumulate(np.r_[1.0, equity])[1:]
    dd = np.divide(equity, peak, out=np.zeros_like(equity), where=peak > 0) - 1.0
    review = pd.DataFrame(
        {
            "event_time_ns": result.event_time_ns,
            "cumulative_return_with_premium": cumulative,
            "cumulative_return_without_premium": np.cumsum(result.trading_return.to_numpy(float)),
            "cumulative_turnover": np.cumsum(result.turnover.to_numpy(float)),
            "executed_position": result.direction,
            "drawdown": dd,
        }
    )
    # Daily points keep return/drawdown review files bounded.  Every executed
    # position transition (plus its predecessor) is also retained so the boss
    # position panel is the actual step path, not a daily approximation.
    sample = review_sample_indices(result.direction, dd)
    return summary, review.iloc[sample].reset_index(drop=True)


def run_symbol(args: argparse.Namespace) -> int:
    window = json.loads((args.output_root / "boss_tick_index_data_window.json").read_text(encoding="utf-8"))
    start = args.start or window["common_start"]
    end_exclusive = args.end_exclusive or window["common_end_exclusive"]
    end_inclusive = (date.fromisoformat(end_exclusive) - timedelta(days=1)).isoformat()
    end_ns = int(pd.Timestamp(end_exclusive, tz="UTC").value)
    bars, funding, execution, tick_prices, waits = load_symbol(
        args.market_root, args.output_root / "tick_execution_index", args.symbol, start, end_inclusive
    )
    strategies = strategy_scope(args.output_root / "boss_multitimeframe_strategy_scope.csv")
    if args.strategy_limit:
        strategies = strategies[: args.strategy_limit]
    groups = semantic_groups(strategies)
    progress = args.output_root / f"matrix_progress_{args.symbol}.json"
    completed = 0
    failures = 0
    physical = 0
    for timeframe in TIMEFRAMES:
        for semantic_hash, members, source in groups:
            representative = members[0]
            case_root = args.output_root / "matrix_cases" / f"symbol={args.symbol}" / f"timeframe={timeframe}" / f"semantic={semantic_hash}"
            result_path = case_root / "summary.json"
            if result_path.is_file():
                summary = json.loads(result_path.read_text(encoding="utf-8"))
                if summary.get("status") == "COMPLETED":
                    completed += len(members)
                    continue
            try:
                summary, review = run_group_case(
                    representative=representative, members=members, source=source,
                    semantic_hash=semantic_hash, symbol=args.symbol, timeframe=timeframe,
                    bars=bars, funding=funding, execution=execution, tick_prices=tick_prices,
                    waits=waits, end_ns=end_ns,
                )
                atomic_json(result_path, summary)
                # The daily review path is compact (not raw tick data) and is
                # retained for every physical semantic case.  Keeping it
                # unconditionally lets the final renderer cover any selected
                # cross-symbol candidate without rerunning the backtest after
                # its aggregate ranking is known.
                temporary = case_root / "review_timeseries.parquet.tmp"
                review.to_parquet(temporary, index=False, compression="zstd")
                os.replace(temporary, case_root / "review_timeseries.parquet")
                completed += len(members)
                physical += 1
            except Exception as exc:
                failures += len(members)
                atomic_json(result_path, {
                    "status": "FAILED", "representative_strategy_id": representative,
                    "member_strategy_ids": ";".join(members), "semantic_execution_hash": semantic_hash,
                    "symbol": args.symbol, "timeframe": timeframe,
                    "error": f"{type(exc).__name__}: {exc}",
                })
            atomic_json(progress, {
                "status": "RUNNING", "symbol": args.symbol,
                "logical_planned": len(strategies) * len(TIMEFRAMES),
                "logical_completed": completed, "logical_failures": failures,
                "physical_runs_this_process": physical, "semantic_groups": len(groups),
                "current_timeframe": timeframe, "current_strategy": representative,
            })
    atomic_json(progress, {
        "status": "PASSED" if failures == 0 else "COMPLETED_WITH_FAILURES",
        "symbol": args.symbol, "logical_planned": len(strategies) * len(TIMEFRAMES),
        "logical_completed": completed, "logical_failures": failures,
        "physical_runs_this_process": physical, "semantic_groups": len(groups),
    })
    return 0 if failures == 0 else 2


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", required=True, choices=SYMBOLS)
    parser.add_argument("--strategy-limit", type=int)
    parser.add_argument("--start")
    parser.add_argument("--end-exclusive")
    parser.add_argument("--market-root", type=Path, default=ROOT / "historical_data/market_data")
    parser.add_argument(
        "--output-root", type=Path,
        default=ROOT / "outputs/baseline_evaluation/boss_multitimeframe_tick_screen",
    )
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(run_symbol(parse_args()))
