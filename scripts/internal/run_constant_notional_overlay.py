#!/usr/bin/env python3
"""
Re-account validated strategy lifecycles at a strict constant notional.

This is an execution-results overlay.  It does not run or modify strategy,
data, feature, funding, or backtest-engine code.  The sign of the validated
position series determines the active lifecycle.  At every one-minute close,
an active position is resized to ``sign * notional / close``; flat stays zero.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures import as_completed
from dataclasses import asdict
from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


EQUITY_COLUMNS = ("event_time_ns", "close", "position")
FUNDING_COLUMNS = ("event_time_ns", "mark_price", "funding_rate")
POSITION_POLICIES = (
    "strict_constant_notional",
    "fixed_return_base_source_positions",
)


@dataclass(frozen=True)
class OverlayConfig:
    source_root: str
    market_root: str
    output_root: str
    notional_usdt: float
    slippage_bps: float
    vip9_fee_bps: float
    vip0_fee_bps: float
    position_policy: str
    direction_multiplier: int
    additional_lag_bars: int
    start: str | None
    end: str | None


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--market-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--notional-usdt", type=float, default=100_000.0)
    parser.add_argument("--slippage-bps", type=float, default=0.0)
    parser.add_argument("--vip9-fee-bps", type=float, default=1.7)
    parser.add_argument("--vip0-fee-bps", type=float, default=5.0)
    parser.add_argument(
        "--position-policy",
        choices=POSITION_POLICIES,
        default="strict_constant_notional",
    )
    parser.add_argument(
        "--direction-multiplier",
        type=int,
        choices=(-1, 1),
        default=1,
        help="Use -1 for the strict position-direction reversal control.",
    )
    parser.add_argument(
        "--additional-lag-bars",
        type=int,
        default=0,
        help="Delay the validated position lifecycle by this many additional 1m bars.",
    )
    parser.add_argument("--start", help="Inclusive UTC date/time")
    parser.add_argument("--end", help="Inclusive UTC date/time")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--strategy", action="append", dest="strategies")
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace existing per-strategy overlay outputs.",
    )
    return parser.parse_args(argv)


def utc_ns(value: str | None, *, end: bool = False) -> int | None:
    if value is None:
        return None
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("UTC")
    else:
        timestamp = timestamp.tz_convert("UTC")
    if end and len(value) == 10:
        timestamp += pd.Timedelta(days=1) - pd.Timedelta(nanoseconds=1)
    return int(timestamp.value)


def discover_strategies(source_root: Path) -> list[str]:
    return sorted(
        path.name
        for path in source_root.iterdir()
        if path.is_dir()
        and (path / "fee_5bps" / "equity_curve.parquet").is_file()
        and (path / "fee_5bps" / "funding_payments.csv").is_file()
    )


def filter_period(
    frame: pd.DataFrame,
    start_ns: int | None,
    end_ns: int | None,
) -> pd.DataFrame:
    selected = np.ones(len(frame), dtype=bool)
    values = frame["event_time_ns"].to_numpy(dtype=np.int64, copy=False)
    if start_ns is not None:
        selected &= values >= start_ns
    if end_ns is not None:
        selected &= values <= end_ns
    return frame.loc[selected].reset_index(drop=True)


_MARKET_OPEN_CACHE: dict[tuple[str, str | None, str | None], pd.Series] = {}


def load_market_open(
    market_root: Path,
    event_time_ns: np.ndarray,
    start: str | None,
    end: str | None,
) -> np.ndarray:
    cache_key = (str(market_root), start, end)
    series = _MARKET_OPEN_CACHE.get(cache_key)
    if series is None:
        start_date = start[:10] if start else None
        end_date = end[:10] if end else None
        files = []
        for path in sorted(market_root.glob("date=*/*.parquet")):
            date_value = path.parent.name.removeprefix("date=")
            if start_date is not None and date_value < start_date:
                continue
            if end_date is not None and date_value > end_date:
                continue
            files.append(path)
        if not files:
            raise ValueError(f"no market parquet files selected under {market_root}")
        market = pd.read_parquet(files, columns=["ts", "open"])
        market_ts = pd.to_datetime(market["ts"], utc=True).array.as_unit("ns").asi8
        series = pd.Series(
            market["open"].to_numpy(dtype=np.float64, copy=False),
            index=market_ts,
        )
        if series.index.has_duplicates:
            raise ValueError("market data contains duplicate timestamps")
        _MARKET_OPEN_CACHE[cache_key] = series
    aligned = series.reindex(event_time_ns)
    if aligned.isna().any():
        missing = int(aligned.isna().sum())
        raise ValueError(f"market open missing for {missing} equity timestamps")
    return aligned.to_numpy(dtype=np.float64, copy=False)


def calculate_overlay(  # noqa: C901 - both policies share one accounting path
    equity: pd.DataFrame,
    funding: pd.DataFrame,
    market_open: np.ndarray | None = None,
    *,
    notional_usdt: float,
    slippage_bps: float,
    vip9_fee_bps: float,
    vip0_fee_bps: float,
    position_policy: str,
    direction_multiplier: int = 1,
    additional_lag_bars: int = 0,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    if equity.empty:
        raise ValueError("equity input is empty")
    if not equity["event_time_ns"].is_monotonic_increasing:
        equity = equity.sort_values("event_time_ns", kind="stable").reset_index(drop=True)
    if equity["event_time_ns"].duplicated().any():
        raise ValueError("equity input contains duplicate event_time_ns")

    event_time_ns = equity["event_time_ns"].to_numpy(dtype=np.int64, copy=False)
    close = equity["close"].to_numpy(dtype=np.float64, copy=False)
    source_position = equity["position"].to_numpy(dtype=np.float64, copy=False)
    if not np.isfinite(close).all() or (close <= 0).any():
        raise ValueError("close must be finite and positive")
    if not np.isfinite(source_position).all():
        raise ValueError("position must be finite")

    if direction_multiplier not in (-1, 1):
        raise ValueError("direction_multiplier must be -1 or 1")
    if additional_lag_bars < 0:
        raise ValueError("additional_lag_bars cannot be negative")
    source_direction = np.sign(source_position).astype(np.int8)
    if additional_lag_bars:
        direction = np.zeros_like(source_direction)
        direction[additional_lag_bars:] = source_direction[:-additional_lag_bars]
    else:
        direction = source_direction.copy()
    direction *= direction_multiplier
    if position_policy == "strict_constant_notional":
        if market_open is None:
            raise ValueError("strict_constant_notional requires aligned market open")
        market_open = np.asarray(market_open, dtype=np.float64)
        if market_open.shape != close.shape:
            raise ValueError("market open must align one-to-one with equity rows")
        if not np.isfinite(market_open).all() or (market_open <= 0).any():
            raise ValueError("market open must be finite and positive")
        signed_notional = direction.astype(np.float64) * notional_usdt
        open_quantity = signed_notional / market_open
        target_quantity = direction.astype(np.float64) * notional_usdt / close
        previous_close_quantity = np.empty_like(target_quantity)
        previous_close_quantity[0] = 0.0
        previous_close_quantity[1:] = target_quantity[:-1]
        previous_close = np.empty_like(close)
        previous_close[0] = market_open[0]
        previous_close[1:] = close[:-1]
        gap_pnl = previous_close_quantity * (market_open - previous_close)
        intrabar_pnl = open_quantity * (close - market_open)
        gross_price_pnl = gap_pnl + intrabar_pnl
        traded_notional = (
            np.abs(open_quantity - previous_close_quantity) * market_open
            + np.abs(target_quantity - open_quantity) * close
        )
    elif position_policy == "fixed_return_base_source_positions":
        if additional_lag_bars:
            target_quantity = np.zeros_like(source_position)
            target_quantity[additional_lag_bars:] = source_position[:-additional_lag_bars]
        else:
            target_quantity = source_position.copy()
        target_quantity *= direction_multiplier
        previous_quantity = np.empty_like(target_quantity)
        previous_quantity[0] = 0.0
        previous_quantity[1:] = target_quantity[:-1]
        gross_price_pnl = np.zeros(len(equity), dtype=np.float64)
        gross_price_pnl[1:] = previous_quantity[1:] * np.diff(close)
        traded_notional = np.abs(target_quantity - previous_quantity) * close
    else:
        raise ValueError(f"unsupported position_policy: {position_policy}")
    turnover = traded_notional / notional_usdt
    slippage_pnl = -traded_notional * slippage_bps / 10_000.0
    trading_pnl = gross_price_pnl + slippage_pnl

    funding_pnl = np.zeros(len(equity), dtype=np.float64)
    funding_event_count = 0
    if not funding.empty:
        funding = funding.sort_values("event_time_ns", kind="stable")
        funding_ts = funding["event_time_ns"].to_numpy(dtype=np.int64, copy=False)
        funding_mark = funding["mark_price"].to_numpy(dtype=np.float64, copy=False)
        funding_rate = funding["funding_rate"].to_numpy(dtype=np.float64, copy=False)
        if not np.isfinite(funding_rate).all() or not np.isfinite(funding_mark).all():
            raise ValueError("funding_rate and mark_price must be finite")
        held_index = np.searchsorted(event_time_ns, funding_ts, side="right") - 1
        report_index = np.searchsorted(event_time_ns, funding_ts, side="left")
        valid = (
            (held_index >= 0)
            & (report_index >= 0)
            & (report_index < len(event_time_ns))
        )
        if position_policy == "strict_constant_notional":
            payments = (
                -direction[held_index[valid]].astype(np.float64)
                * notional_usdt
                * funding_rate[valid]
            )
        else:
            payments = (
                -target_quantity[held_index[valid]]
                * funding_mark[valid]
                * funding_rate[valid]
            )
        np.add.at(funding_pnl, report_index[valid], payments)
        funding_event_count = int(valid.sum())

    trading_return = trading_pnl / notional_usdt
    funding_return = funding_pnl / notional_usdt
    total_return = trading_return + funding_return
    vip9_fee_return = -turnover * vip9_fee_bps / 10_000.0
    vip0_fee_return = -turnover * vip0_fee_bps / 10_000.0

    boundary_notional = np.abs(target_quantity) * close
    if position_policy == "strict_constant_notional":
        expected_notional = np.where(direction == 0, 0.0, notional_usdt)
        max_notional_error: float | None = float(
            np.max(np.abs(boundary_notional - expected_notional))
        )
    else:
        max_notional_error = None

    result = pd.DataFrame(
        {
            "event_time_ns": event_time_ns,
            "direction": direction,
            "target_quantity": target_quantity,
            "boundary_notional_usdt": boundary_notional,
            "gross_price_return": gross_price_pnl / notional_usdt,
            "slippage_return": slippage_pnl / notional_usdt,
            "trading_return": trading_return,
            "funding_return": funding_return,
            "total_return": total_return,
            "turnover": turnover,
            "vip9_total_return": total_return + vip9_fee_return,
            "vip0_total_return": total_return + vip0_fee_return,
        }
    )

    total_turnover = float(turnover.sum())
    total_return_sum = float(total_return.sum())
    summary: dict[str, Any] = {
        "row_count": len(result),
        "first_event_time_utc": pd.Timestamp(event_time_ns[0], unit="ns", tz="UTC").isoformat(),
        "last_event_time_utc": pd.Timestamp(event_time_ns[-1], unit="ns", tz="UTC").isoformat(),
        "notional_usdt": notional_usdt,
        "position_policy": position_policy,
        "direction_multiplier": direction_multiplier,
        "additional_lag_bars": additional_lag_bars,
        "active_minutes": int(np.count_nonzero(direction)),
        "flat_minutes": int(np.count_nonzero(direction == 0)),
        "funding_event_count": funding_event_count,
        "gross_price_pnl_usdt": float(gross_price_pnl.sum()),
        "slippage_pnl_usdt": float(slippage_pnl.sum()),
        "trading_pnl_usdt": float(trading_pnl.sum()),
        "funding_pnl_usdt": float(funding_pnl.sum()),
        "total_pnl_usdt_fee0": total_return_sum * notional_usdt,
        "total_pnl_usdt_vip9": float((total_return + vip9_fee_return).sum())
        * notional_usdt,
        "total_pnl_usdt_vip0": float((total_return + vip0_fee_return).sum())
        * notional_usdt,
        "trading_simple_return": float(trading_return.sum()),
        "funding_simple_return": float(funding_return.sum()),
        "total_simple_return_fee0": total_return_sum,
        "total_simple_return_vip9": float((total_return + vip9_fee_return).sum()),
        "total_simple_return_vip0": float((total_return + vip0_fee_return).sum()),
        "total_turnover_x": total_turnover,
        "breakeven_fee_bps": (
            total_return_sum / total_turnover * 10_000.0
            if total_turnover > 0
            else None
        ),
        "max_boundary_notional_error_usdt": max_notional_error,
        "accounting_identity_max_error": float(
            np.max(np.abs(total_return - trading_return - funding_return))
        ),
    }
    return result, summary


def run_strategy(
    strategy: str,
    config: OverlayConfig,
    overwrite: bool,
) -> dict[str, Any]:
    source = Path(config.source_root) / strategy / "fee_5bps"
    destination = Path(config.output_root) / strategy
    timeseries_path = destination / "timeseries.parquet"
    summary_path = destination / "summary.json"
    if not overwrite and timeseries_path.is_file() and summary_path.is_file():
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        return {"strategy": strategy, "status": "existing", **summary}

    equity = pd.read_parquet(source / "equity_curve.parquet", columns=list(EQUITY_COLUMNS))
    start_ns = utc_ns(config.start)
    end_ns = utc_ns(config.end, end=True)
    equity = filter_period(equity, start_ns, end_ns)
    if equity.empty:
        raise ValueError(f"{strategy}: no equity rows in selected period")

    funding = pd.read_csv(source / "funding_payments.csv", usecols=list(FUNDING_COLUMNS))
    funding = filter_period(funding, start_ns, end_ns)
    market_open = (
        load_market_open(
            Path(config.market_root),
            equity["event_time_ns"].to_numpy(dtype=np.int64, copy=False),
            config.start,
            config.end,
        )
        if config.position_policy == "strict_constant_notional"
        else None
    )
    result, summary = calculate_overlay(
        equity,
        funding,
        market_open,
        notional_usdt=config.notional_usdt,
        slippage_bps=config.slippage_bps,
        vip9_fee_bps=config.vip9_fee_bps,
        vip0_fee_bps=config.vip0_fee_bps,
        position_policy=config.position_policy,
        direction_multiplier=config.direction_multiplier,
        additional_lag_bars=config.additional_lag_bars,
    )
    summary = {
        "strategy": strategy,
        "source_equity_path": str(source / "equity_curve.parquet"),
        "source_funding_path": str(source / "funding_payments.csv"),
        **summary,
    }

    destination.mkdir(parents=True, exist_ok=True)
    temporary_parquet = destination / "timeseries.parquet.tmp"
    result.to_parquet(temporary_parquet, index=False, compression="zstd")
    os.replace(temporary_parquet, timeseries_path)
    temporary_summary = destination / "summary.json.tmp"
    temporary_summary.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary_summary, summary_path)
    return {"strategy": strategy, "status": "completed", **summary}


def write_batch_metadata(
    output_root: Path,
    config: OverlayConfig,
    rows: list[dict[str, Any]],
) -> None:
    pd.DataFrame(rows).sort_values("strategy").to_csv(
        output_root / "evaluation_table.csv",
        index=False,
    )
    manifest = {
        "artifact_type": "constant_notional_execution_overlay",
        "created_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "config": asdict(config),
        "strategy_count": len(rows),
        "status_counts": pd.Series([row["status"] for row in rows]).value_counts().to_dict(),
        "method": {
            "lifecycle_source": "validated fee_5bps equity position sign",
            "notional_rule": (
                "active position is exactly +/- notional at each 1m open and close boundary"
                if config.position_policy == "strict_constant_notional"
                else "source position sizing; returns use fixed notional as denominator"
            ),
            "quantity_model": "continuous fractional quantity",
            "lag": (
                "canonical source lag=1 bar plus "
                f"{config.additional_lag_bars} additional 1m bars"
            ),
            "direction_multiplier": config.direction_multiplier,
            "funding": "stored final funding_rate applied to strict target notional",
            "premium_limit": "raw Premium Index is not available separately",
            "return_method": "arithmetic non-compounded",
        },
    }
    (output_root / "artifact_manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.notional_usdt <= 0:
        raise ValueError("--notional-usdt must be positive")
    if min(args.slippage_bps, args.vip9_fee_bps, args.vip0_fee_bps) < 0:
        raise ValueError("cost bps values cannot be negative")
    if args.additional_lag_bars < 0:
        raise ValueError("--additional-lag-bars cannot be negative")
    if args.workers < 1:
        raise ValueError("--workers must be at least one")
    if not args.source_root.is_dir():
        raise ValueError(f"source root does not exist: {args.source_root}")
    if not args.market_root.is_dir():
        raise ValueError(f"market root does not exist: {args.market_root}")

    available = discover_strategies(args.source_root)
    strategies = args.strategies or available
    missing = sorted(set(strategies).difference(available))
    if missing:
        raise ValueError(f"strategies unavailable in source root: {missing}")
    args.output_root.mkdir(parents=True, exist_ok=True)
    config = OverlayConfig(
        source_root=str(args.source_root.resolve()),
        market_root=str(args.market_root.resolve()),
        output_root=str(args.output_root.resolve()),
        notional_usdt=args.notional_usdt,
        slippage_bps=args.slippage_bps,
        vip9_fee_bps=args.vip9_fee_bps,
        vip0_fee_bps=args.vip0_fee_bps,
        position_policy=args.position_policy,
        direction_multiplier=args.direction_multiplier,
        additional_lag_bars=args.additional_lag_bars,
        start=args.start,
        end=args.end,
    )

    rows: list[dict[str, Any]] = []
    with ProcessPoolExecutor(max_workers=min(args.workers, len(strategies))) as executor:
        futures = {
            executor.submit(run_strategy, strategy, config, args.overwrite): strategy
            for strategy in strategies
        }
        for future in as_completed(futures):
            strategy = futures[future]
            try:
                row = future.result()
            except Exception as exc:
                print(f"FAILED {strategy}: {exc}", file=sys.stderr, flush=True)
                raise
            rows.append(row)
            print(
                f"{row['status'].upper()} {strategy} "
                f"rows={row['row_count']} pnl0={row['total_pnl_usdt_fee0']:.2f} "
                f"turnover={row['total_turnover_x']:.2f}x",
                flush=True,
            )

    write_batch_metadata(args.output_root, config, rows)
    print(f"COMPLETE strategies={len(rows)} output={args.output_root}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
