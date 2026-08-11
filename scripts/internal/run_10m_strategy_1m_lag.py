#!/usr/bin/env python3
"""
Validate a configurable MA-crossover bar clock with a one-minute execution clock.

The script is an experiment adapter only: market bars enter through
``data_engine.load_events``, indicators through ``FeatureStrategyRunner``, and
the existing constant-notional accounting overlay produces returns/turnover.
It does not change any core engine or strategy implementation.
"""

from __future__ import annotations

import argparse
import json
from calendar import monthrange
from dataclasses import asdict
from dataclasses import dataclass
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
import polars as pl

from data_engine.loader import load_events
from data_engine.transforms import parse_frequency
from data_engine.transforms import resample_bars
from feature_engine.runner import FeatureStrategyRunner
from feature_engine.storage.layout import FEATURE_DATA_PARTITION_COLS
from feature_engine.storage.parquet_store import ParquetStore
from scripts.internal.run_constant_notional_overlay import calculate_overlay
from strategies.ma_crossover import MovingAverageCrossoverConfig
from strategies.ma_crossover import MovingAverageCrossoverStrategy
from strategies.ma_crossover import build_specs


MINUTE_NS = 60_000_000_000
DIRECTION_VARIANTS = (
    "long_only",
    "short_only",
    "long_short",
    "reverse_long_short",
)


@dataclass(frozen=True)
class ExperimentConfig:
    market_root: str
    feature_root: str
    output_root: str
    start: str
    end: str
    instrument_id: str = "BTCUSDT-PERP.BINANCE"
    notional_usdt: float = 100_000.0
    strategy_bar_frequency: str = "10m"
    execution_lag_minutes: int = 1
    slippage_bps: float = 0.0
    vip9_fee_bps: float = 1.7
    vip0_fee_bps: float = 5.0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market-root", type=Path, required=True)
    parser.add_argument("--feature-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--start", default="2021-07-01")
    parser.add_argument("--end", default="2026-06-30")
    parser.add_argument("--notional-usdt", type=float, default=100_000.0)
    parser.add_argument(
        "--strategy-bar-frequency",
        choices=("1m", "10m"),
        default="10m",
    )
    parser.add_argument("--execution-lag-minutes", type=int, choices=(0, 1), default=1)
    parser.add_argument("--write-features", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def month_windows(first_date: str, last_date: str) -> list[tuple[str, str]]:
    first = pd.Timestamp(first_date).date()
    last = pd.Timestamp(last_date).date()
    cursor = first.replace(day=1)
    windows: list[tuple[str, str]] = []
    while cursor <= last:
        month_end = cursor.replace(day=monthrange(cursor.year, cursor.month)[1])
        lo = max(first, cursor)
        hi = min(last, month_end)
        windows.append((lo.isoformat(), hi.isoformat()))
        cursor = (pd.Timestamp(month_end) + pd.Timedelta(days=1)).date()
    return windows


def market_config(config: ExperimentConfig, start: str, end: str) -> dict:
    return {
        "mode": "hive_parquet_bars",
        "root": config.market_root,
        "instrument_id": config.instrument_id,
        "start": start,
        "end": end,
        "warmup_bars": 0,
        "filters": {
            "asset_class": "crypto",
            "exchange": "BINANCE",
            "venue_type": "futures_um",
            "symbol": "BTCUSDT",
            "data_type": "bar",
            "freq": "1m",
        },
    }


def funding_frame(config: ExperimentConfig) -> pd.DataFrame:
    _, events = load_events(
        {
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
    )
    rows = [
        {
            "event_time_ns": event.event_time_ns,
            "mark_price": event.mark_price if event.mark_price is not None else 0.0,
            "funding_rate": event.funding_rate,
        }
        for event in events
    ]
    return pd.DataFrame(rows, columns=["event_time_ns", "mark_price", "funding_rate"])


def write_feature_partitions(
    frame: pd.DataFrame,
    root: Path,
    frequency: str,
) -> list[str]:
    store = ParquetStore(root, FEATURE_DATA_PARTITION_COLS)
    written: list[str] = []
    frame = frame.copy()
    frame["date"] = pd.to_datetime(frame["ts_event"], unit="ns", utc=True).dt.date.astype(str)
    for date, day in frame.groupby("date", sort=True):
        values = pl.from_pandas(day[["instrument_id", "ts_event", "ma5_close", "ma20_close"]])
        paths = store.write(
            values,
            partition_values={
                "feature_group": "strategy_ma_crossover",
                "asset_class": "crypto",
                "exchange": "BINANCE",
                "venue_type": "futures_um",
                "symbol": "BTCUSDT",
                "freq": frequency,
                "date": str(date),
            },
            basename_template=f"part-{frequency}-multiclock-{{i}}.parquet",
        )
        written.extend(str(path) for path in paths)
    return written


def build_indicator_audit(
    features: pd.DataFrame,
    start_ns: int,
    strategy_interval_ns: int,
) -> pd.DataFrame:
    ready = features.loc[
        (features["ts_event"] >= start_ns)
        & features["ma5_close"].notna()
        & features["ma20_close"].notna()
    ].head(6).copy()
    ready["manual_ma5"] = ready["strategy_bar_close"].rolling(5).mean()
    ready["manual_ma20"] = ready["strategy_bar_close"].rolling(20).mean()
    # The rolling windows need the preceding rows, so calculate over the full frame.
    manual5 = features["strategy_bar_close"].rolling(5).mean()
    manual20 = features["strategy_bar_close"].rolling(20).mean()
    ready["manual_ma5"] = manual5.loc[ready.index].to_numpy()
    ready["manual_ma20"] = manual20.loc[ready.index].to_numpy()
    ready["ma5_abs_error"] = (ready["ma5_close"] - ready["manual_ma5"]).abs()
    ready["ma20_abs_error"] = (ready["ma20_close"] - ready["manual_ma20"]).abs()
    ready["source_first_1m_open_ns"] = ready["ts_event"] - strategy_interval_ns
    ready["source_last_1m_open_ns"] = ready["ts_event"] - MINUTE_NS
    ready["source_data_end_ns"] = ready["source_last_1m_open_ns"] + MINUTE_NS
    ready["timestamp_match"] = (
        (ready["ts_event"] == ready["feature_source_event_time_ns"])
        & (ready["ts_event"] == ready["source_data_end_ns"])
    )
    ready["value_match"] = (
        (ready["ma5_abs_error"] <= 1e-10) & (ready["ma20_abs_error"] <= 1e-10)
    )
    ready["match"] = ready["timestamp_match"] & ready["value_match"]
    time_cols = [
        "ts_event",
        "feature_source_event_time_ns",
        "source_first_1m_open_ns",
        "source_last_1m_open_ns",
        "source_data_end_ns",
    ]
    for column in time_cols:
        ready[column.removesuffix("_ns") + "_utc"] = pd.to_datetime(
            ready[column], unit="ns", utc=True
        )
    return ready


def direction_from_signals(
    event_times: np.ndarray,
    signal_times: list[int],
    signal_values: list[int],
) -> np.ndarray:
    if not signal_times:
        return np.zeros(len(event_times), dtype=np.int8)
    indices = np.searchsorted(np.asarray(signal_times, dtype=np.int64), event_times, side="right") - 1
    result = np.zeros(len(event_times), dtype=np.int8)
    valid = indices >= 0
    result[valid] = np.asarray(signal_values, dtype=np.int8)[indices[valid]]
    return result


def update_direction_states(states: dict[str, int], signal: str) -> None:
    if signal == "BUY":
        states.update(
            long_only=1,
            short_only=0,
            long_short=1,
            reverse_long_short=-1,
        )
    elif signal == "SELL":
        states.update(
            long_only=0,
            short_only=-1,
            long_short=-1,
            reverse_long_short=1,
        )


def run(args: argparse.Namespace) -> Path:
    if args.output_root.exists() and any(args.output_root.iterdir()) and not args.overwrite:
        raise ValueError(f"output exists: {args.output_root}; pass --overwrite")
    args.output_root.mkdir(parents=True, exist_ok=True)
    config = ExperimentConfig(
        market_root=str(args.market_root),
        feature_root=str(args.feature_root),
        output_root=str(args.output_root),
        start=args.start,
        end=args.end,
        notional_usdt=args.notional_usdt,
        strategy_bar_frequency=args.strategy_bar_frequency,
        execution_lag_minutes=args.execution_lag_minutes,
    )
    (args.output_root / "experiment_config.json").write_text(
        json.dumps(asdict(config), indent=2) + "\n", encoding="utf-8"
    )

    strategy_config = MovingAverageCrossoverConfig(fast_window=5, slow_window=20)
    runner = FeatureStrategyRunner(
        build_specs(strategy_config), MovingAverageCrossoverStrategy(strategy_config)
    )
    start_ns = int(pd.Timestamp(args.start, tz="UTC").value)
    end_exclusive_ns = int((pd.Timestamp(args.end, tz="UTC") + pd.Timedelta(days=1)).value)
    raw_times: list[np.ndarray] = []
    raw_open: list[np.ndarray] = []
    raw_close: list[np.ndarray] = []
    feature_rows: list[dict] = []
    signal_rows: list[dict] = []
    signal_times = {variant: [] for variant in DIRECTION_VARIANTS}
    signal_values = {variant: [] for variant in DIRECTION_VARIANTS}
    direction_states = dict.fromkeys(DIRECTION_VARIANTS, 0)
    strategy_interval_ns = parse_frequency(config.strategy_bar_frequency)

    warmup_start = (pd.Timestamp(args.start) - pd.Timedelta(days=1)).date().isoformat()
    for window_start, window_end in month_windows(warmup_start, args.end):
        _, bars_iter = load_events(market_config(config, window_start, window_end))
        bars = list(bars_iter)
        strategy_bars = [
            replace(bar, event_time_ns=bar.event_time_ns + strategy_interval_ns)
            for bar in resample_bars(bars, config.strategy_bar_frequency)
        ]
        for bar in strategy_bars:
            snapshot, signal = runner.on_event(bar)
            fast_value = snapshot.get(strategy_config.fast_name)
            feature_rows.append(
                {
                    "instrument_id": config.instrument_id,
                    "ts_event": snapshot.ts_event,
                    "strategy_bar_close": bar.close,
                    "ma5_close": snapshot.value(strategy_config.fast_name),
                    "ma20_close": snapshot.value(strategy_config.slow_name),
                    "feature_source_event_time_ns": (
                        fast_value.source_event_time_ns if fast_value is not None else None
                    ),
                }
            )
            if start_ns <= snapshot.ts_event < end_exclusive_ns and signal != "HOLD":
                effective = snapshot.ts_event + config.execution_lag_minutes * MINUTE_NS
                update_direction_states(direction_states, signal)
                for variant in DIRECTION_VARIANTS:
                    signal_times[variant].append(effective)
                    signal_values[variant].append(direction_states[variant])
                signal_rows.append(
                    {
                        "feature_time_ns": snapshot.ts_event,
                        "signal": signal,
                        "fill_time_ns": effective,
                        **{
                            f"{variant}_target": direction_states[variant]
                            for variant in DIRECTION_VARIANTS
                        },
                    }
                )

        selected = [bar for bar in bars if start_ns <= bar.event_time_ns < end_exclusive_ns]
        if selected:
            raw_times.append(np.fromiter((bar.event_time_ns for bar in selected), dtype=np.int64))
            raw_open.append(np.fromiter((bar.open for bar in selected), dtype=np.float64))
            raw_close.append(np.fromiter((bar.close for bar in selected), dtype=np.float64))
        print(
            f"processed {window_start}..{window_end}: {len(bars)} x 1m, "
            f"{len(strategy_bars)} x {config.strategy_bar_frequency}"
        )

    event_time_ns = np.concatenate(raw_times)
    market_open = np.concatenate(raw_open)
    close = np.concatenate(raw_close)
    all_features = pd.DataFrame(feature_rows)
    features = all_features.loc[
        (all_features["ts_event"] >= start_ns)
        & (all_features["ts_event"] < end_exclusive_ns)
    ].copy()
    signals = pd.DataFrame(signal_rows)
    features.to_parquet(
        args.output_root / f"features_{config.strategy_bar_frequency}.parquet",
        index=False,
    )
    signals.to_csv(
        args.output_root / f"signals_{config.strategy_bar_frequency}.csv",
        index=False,
    )
    audit = build_indicator_audit(all_features, start_ns, strategy_interval_ns)
    audit.to_csv(args.output_root / "indicator_manual_check.csv", index=False)
    if not bool(audit["match"].all()):
        raise RuntimeError("manual indicator/timestamp audit failed")

    feature_paths = (
        write_feature_partitions(features, args.feature_root, config.strategy_bar_frequency)
        if args.write_features
        else []
    )
    funding = funding_frame(config)
    summaries: list[dict] = []
    for variant in DIRECTION_VARIANTS:
        name = f"ma_crossover_{config.strategy_bar_frequency}_{variant}"
        direction = direction_from_signals(
            event_time_ns,
            signal_times[variant],
            signal_values[variant],
        )
        equity = pd.DataFrame(
            {"event_time_ns": event_time_ns, "close": close, "position": direction}
        )
        result, summary = calculate_overlay(
            equity,
            funding,
            market_open,
            notional_usdt=config.notional_usdt,
            slippage_bps=config.slippage_bps,
            vip9_fee_bps=config.vip9_fee_bps,
            vip0_fee_bps=config.vip0_fee_bps,
            position_policy="strict_constant_notional",
        )
        strategy_dir = args.output_root / name
        strategy_dir.mkdir(exist_ok=True)
        result.to_parquet(strategy_dir / "timeseries.parquet", index=False)
        summary.update(
            {
                "strategy": name,
                "strategy_bar_frequency": config.strategy_bar_frequency,
                "execution_clock_frequency": "1m",
                "execution_lag_minutes": config.execution_lag_minutes,
                "direction_variant": variant,
                "signal_count": len(signals),
                "slippage_bps": 0.0,
            }
        )
        (strategy_dir / "summary.json").write_text(
            json.dumps(summary, indent=2) + "\n", encoding="utf-8"
        )
        summaries.append(summary)

    pd.DataFrame(summaries).to_csv(args.output_root / "evaluation_table.csv", index=False)
    manifest = {
        "config": asdict(config),
        "market_data_contract": "data_engine.loader.load_events(hive_parquet_bars)",
        "feature_contract": "FeatureStrategyRunner + rolling_mean FeatureSpec",
        "signal_logic": "strategies.ma_crossover.MovingAverageCrossoverStrategy",
        "timestamp_semantics": {
            "raw_1m_ts": "bar open",
            "feature_ts": f"{config.strategy_bar_frequency} source window end",
            "fill_ts": f"feature_ts + {config.execution_lag_minutes} minute(s)",
        },
        "indicator_manual_check": "indicator_manual_check.csv",
        "feature_partition_file_count": len(feature_paths),
        "feature_partition_paths": feature_paths,
    }
    (args.output_root / "artifact_manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    return args.output_root


if __name__ == "__main__":
    namespace = parse_args()
    print(run(namespace))
