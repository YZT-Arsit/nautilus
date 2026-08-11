#!/usr/bin/env python3
"""
Build a no-fee/no-slippage fixed-capital view of canonical backtests.

The source strategy sizing is preserved. Existing commission and 1 bp
simulated slippage are removed, and PnL is divided by a fixed 100,000 USDT
base. Funding is kept as a separate return component. No strategy or backtest
is rerun.
"""

from __future__ import annotations

import argparse
import json
import os
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures import as_completed
from datetime import UTC
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--capital-usdt", type=float, default=100_000.0)
    parser.add_argument("--source-slippage-bps", type=float, default=1.0)
    parser.add_argument("--start")
    parser.add_argument("--end")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--strategy", action="append", dest="strategies")
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args(argv)


def utc_ns(value: str | None, *, end: bool = False) -> int | None:
    if value is None:
        return None
    timestamp = pd.Timestamp(value)
    timestamp = (
        timestamp.tz_localize("UTC")
        if timestamp.tzinfo is None
        else timestamp.tz_convert("UTC")
    )
    if end and len(value) == 10:
        timestamp += pd.Timedelta(days=1) - pd.Timedelta(nanoseconds=1)
    return int(timestamp.value)


def discover(source_root: Path) -> list[str]:
    return sorted(
        child.name
        for child in source_root.iterdir()
        if child.is_dir()
        and (child / "fee_5bps" / "equity_curve.parquet").is_file()
        and (child / "fee_5bps" / "fills.csv").is_file()
    )


def run_one(
    strategy: str,
    source_root: str,
    output_root: str,
    capital_usdt: float,
    source_slippage_bps: float,
    start: str | None,
    end: str | None,
    overwrite: bool,
) -> dict[str, Any]:
    source = Path(source_root) / strategy / "fee_5bps"
    destination = Path(output_root) / strategy
    timeseries_path = destination / "timeseries.parquet"
    summary_path = destination / "summary.json"
    if not overwrite and timeseries_path.is_file() and summary_path.is_file():
        return {"status": "existing", **json.loads(summary_path.read_text(encoding="utf-8"))}

    equity = pd.read_parquet(
        source / "equity_curve.parquet",
        columns=["event_time_ns", "net_pnl", "funding_pnl", "commission"],
    )
    start_ns = utc_ns(start)
    end_ns = utc_ns(end, end=True)
    mask = np.ones(len(equity), dtype=bool)
    event_time_ns = equity["event_time_ns"].to_numpy(dtype=np.int64, copy=False)
    if start_ns is not None:
        mask &= event_time_ns >= start_ns
    if end_ns is not None:
        mask &= event_time_ns <= end_ns
    equity = equity.loc[mask].reset_index(drop=True)
    if equity.empty:
        raise ValueError(f"{strategy}: no rows in selected period")
    event_time_ns = equity["event_time_ns"].to_numpy(dtype=np.int64, copy=False)

    fills = pd.read_csv(
        source / "fills.csv",
        usecols=["event_time_ns", "side", "quantity", "fill_price"],
    )
    fill_ts = fills["event_time_ns"].to_numpy(dtype=np.int64, copy=False)
    fill_mask = np.ones(len(fills), dtype=bool)
    if start_ns is not None:
        fill_mask &= fill_ts >= start_ns
    if end_ns is not None:
        fill_mask &= fill_ts <= end_ns
    fills = fills.loc[fill_mask].reset_index(drop=True)

    slippage_fraction = source_slippage_bps / 10_000.0
    slippage_rebate = np.zeros(len(equity), dtype=np.float64)
    turnover = np.zeros(len(equity), dtype=np.float64)
    if not fills.empty:
        side = fills["side"].astype(str).str.upper().to_numpy()
        fill_price = fills["fill_price"].to_numpy(dtype=np.float64, copy=False)
        quantity = fills["quantity"].to_numpy(dtype=np.float64, copy=False)
        raw_price = np.where(
            side == "BUY",
            fill_price / (1.0 + slippage_fraction),
            fill_price / (1.0 - slippage_fraction),
        )
        cost = np.abs(fill_price - raw_price) * quantity
        raw_notional = np.abs(quantity) * raw_price
        report_index = np.searchsorted(
            event_time_ns,
            fills["event_time_ns"].to_numpy(dtype=np.int64, copy=False),
            side="left",
        )
        valid = report_index < len(equity)
        np.add.at(slippage_rebate, report_index[valid], cost[valid])
        np.add.at(turnover, report_index[valid], raw_notional[valid] / capital_usdt)

    cumulative_rebate = np.cumsum(slippage_rebate)
    net_pnl = equity["net_pnl"].to_numpy(dtype=np.float64, copy=False)
    commission_cumulative = equity["commission"].to_numpy(dtype=np.float64, copy=False)
    funding_pnl_cumulative = equity["funding_pnl"].to_numpy(dtype=np.float64, copy=False)
    trading_pnl_cumulative = (
        net_pnl
        - funding_pnl_cumulative
        + commission_cumulative
        + cumulative_rebate
    )
    total_pnl_cumulative = trading_pnl_cumulative + funding_pnl_cumulative
    funding_pnl_cumulative = funding_pnl_cumulative - funding_pnl_cumulative[0]
    trading_pnl_cumulative = trading_pnl_cumulative - trading_pnl_cumulative[0]
    total_pnl_cumulative = total_pnl_cumulative - total_pnl_cumulative[0]
    trading_pnl_delta = np.diff(trading_pnl_cumulative, prepend=0.0)
    funding_pnl_delta = np.diff(funding_pnl_cumulative, prepend=0.0)
    total_pnl_delta = np.diff(total_pnl_cumulative, prepend=0.0)
    result = pd.DataFrame(
        {
            "event_time_ns": event_time_ns,
            "trading_return": trading_pnl_delta / capital_usdt,
            "funding_return": funding_pnl_delta / capital_usdt,
            "total_return": total_pnl_delta / capital_usdt,
            "turnover": turnover,
        }
    )

    total_turnover = float(turnover.sum())
    total_return = float(total_pnl_cumulative[-1] / capital_usdt)
    summary: dict[str, Any] = {
        "strategy": strategy,
        "row_count": len(result),
        "first_event_time_utc": pd.Timestamp(event_time_ns[0], unit="ns", tz="UTC").isoformat(),
        "last_event_time_utc": pd.Timestamp(event_time_ns[-1], unit="ns", tz="UTC").isoformat(),
        "capital_usdt": capital_usdt,
        "source_position_sizing_preserved": True,
        "source_slippage_removed_bps": source_slippage_bps,
        "source_commission_removed": True,
        "trading_pnl_usdt": float(trading_pnl_cumulative[-1]),
        "funding_pnl_usdt": float(funding_pnl_cumulative[-1]),
        "total_pnl_usdt": float(total_pnl_cumulative[-1]),
        "trading_simple_return": float(trading_pnl_cumulative[-1] / capital_usdt),
        "funding_simple_return": float(funding_pnl_cumulative[-1] / capital_usdt),
        "total_simple_return": total_return,
        "total_turnover_x": total_turnover,
        "breakeven_fee_bps": (
            total_return / total_turnover * 10_000.0 if total_turnover > 0 else None
        ),
        "accounting_identity_error": float(
            abs(
                total_pnl_cumulative[-1]
                - trading_pnl_cumulative[-1]
                - funding_pnl_cumulative[-1]
            )
        ),
    }
    destination.mkdir(parents=True, exist_ok=True)
    temporary = destination / "timeseries.parquet.tmp"
    result.to_parquet(temporary, index=False, compression="zstd")
    os.replace(temporary, timeseries_path)
    temporary_summary = destination / "summary.json.tmp"
    temporary_summary.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary_summary, summary_path)
    return {"status": "completed", **summary}


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.capital_usdt <= 0 or args.source_slippage_bps < 0 or args.workers < 1:
        raise ValueError("capital must be positive; costs non-negative; workers >= 1")
    available = discover(args.source_root)
    strategies = args.strategies or available
    missing = sorted(set(strategies).difference(available))
    if missing:
        raise ValueError(f"unavailable strategies: {missing}")
    args.output_root.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, Any]] = []
    with ProcessPoolExecutor(max_workers=min(args.workers, len(strategies))) as executor:
        futures = {
            executor.submit(
                run_one,
                strategy,
                str(args.source_root.resolve()),
                str(args.output_root.resolve()),
                args.capital_usdt,
                args.source_slippage_bps,
                args.start,
                args.end,
                args.overwrite,
            ): strategy
            for strategy in strategies
        }
        for future in as_completed(futures):
            row = future.result()
            rows.append(row)
            print(
                f"{row['status'].upper()} {row['strategy']} rows={row['row_count']} "
                f"pnl={row['total_pnl_usdt']:.2f} turnover={row['total_turnover_x']:.4f}x",
                flush=True,
            )

    pd.DataFrame(rows).sort_values("strategy").to_csv(
        args.output_root / "evaluation_table.csv",
        index=False,
    )
    manifest = {
        "artifact_type": "fixed_100k_return_base_overlay",
        "created_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "strategy_count": len(rows),
        "capital_usdt": args.capital_usdt,
        "leverage": 1.0,
        "fee_bps": 0.0,
        "slippage_bps": 0.0,
        "source_slippage_removed_bps": args.source_slippage_bps,
        "source_commission_removed": True,
        "funding": "preserved from canonical source and split separately",
        "return_method": "arithmetic non-compounded",
        "position_policy": "canonical source quantities; only return denominator is fixed",
    }
    (args.output_root / "artifact_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"COMPLETE strategies={len(rows)} output={args.output_root}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
