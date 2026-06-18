#!/usr/bin/env python3
"""CLI to build the ML V2 train/validation research dataset (month-chunked).

Unlike ``build_ml_dataset.py`` (which uses ``data_engine.load_events`` and only
sees OHLCV), this reads the 1m **bar parquet directly** in the research layer so
the V2 order-flow features can use the extra bar columns (``quote_volume``,
``trade_count``, ``taker_buy_volume``, ``taker_buy_quote_volume``).
``data_engine`` is **not** modified. Output is restricted to
``outputs/research_datasets/``. Reads only train+validation date partitions -
never the test window. No model training, no backtest.

Usage::

    uv run --no-sync python scripts/build_ml_dataset_v2.py \
        --root historical_data/market_data --exchange BINANCE --venue-type spot \
        --symbol BTCUSDT --bar-type 1m --splits train,validation \
        --out outputs/research_datasets/ml_v2_btcusdt_1m_train_val \
        --horizon 15 --fee-rate 0.0005 --buffer 0.0005 --lead-in 120 --tail 15
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# Repo-root bootstrap (see scripts/build_ml_dataset.py for the why).
_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from research.dataset_builder_v2 import build_dataset_v2, parquet_part_writer_v2  # noqa: E402
from research.dataset_writer import build_dataset_partitioned, write_partitioned_dataset  # noqa: E402
from research.features_v2 import FEATURE_COLUMNS_V2  # noqa: E402
from research.splits import DEFAULT_SPLITS  # noqa: E402

VALID_SPLITS = ("train", "validation")          # test is never built here
_ALLOWED_OUT_PREFIX = "outputs/research_datasets"
REQUIRED_BAR_COLUMNS = (
    "ts", "instrument_id", "open", "high", "low", "close", "volume",
    "quote_volume", "trade_count", "taker_buy_volume", "taker_buy_quote_volume",
)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Build ML V2 train/validation research dataset (direct parquet)")
    p.add_argument("--root", default="historical_data/market_data")
    p.add_argument("--exchange", default="BINANCE")
    p.add_argument("--venue-type", default="spot")
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--bar-type", default="1m")
    p.add_argument("--splits", default="train,validation")
    p.add_argument("--out", required=True)
    p.add_argument("--horizon", type=int, default=15)
    p.add_argument("--fee-rate", type=float, default=0.0005)
    p.add_argument("--buffer", type=float, default=0.0005)
    p.add_argument("--lead-in", type=int, default=120)
    p.add_argument("--tail", type=int, default=15)
    p.add_argument("--start", default=None)
    p.add_argument("--end", default=None)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--plan-only", action="store_true")
    p.add_argument("--overwrite", action="store_true")
    return p


def parse_splits(raw: str) -> list[str]:
    return [s.strip() for s in str(raw).split(",") if s.strip()]


def _is_plan(args) -> bool:
    return bool(getattr(args, "dry_run", False) or getattr(args, "plan_only", False))


def bar_dir(args) -> Path:
    return (Path(args.root) / f"exchange={args.exchange}" / f"venue_type={args.venue_type}"
            / f"symbol={args.symbol}" / f"bar_type={args.bar_type}")


def _resolve_window(args, splits) -> tuple[str, str]:
    if args.start and args.end:
        return args.start, args.end
    starts = [DEFAULT_SPLITS[s][0] for s in splits]
    ends = [DEFAULT_SPLITS[s][1] for s in splits]
    return (args.start or min(starts)), (args.end or max(ends))


def preflight(args) -> dict[str, Any]:
    splits = parse_splits(args.splits)
    if not splits:
        raise ValueError("splits must be non-empty")
    bad = [s for s in splits if s not in VALID_SPLITS]
    if bad:
        raise ValueError(f"invalid splits {bad}; allowed (no test): {VALID_SPLITS}")
    if args.horizon <= 0:
        raise ValueError("horizon must be > 0")
    if args.fee_rate < 0 or args.buffer < 0:
        raise ValueError("fee_rate and buffer must be >= 0")
    if args.lead_in < 120:
        raise ValueError("lead_in must be >= 120 (feature warmup)")
    if args.tail < args.horizon:
        raise ValueError("tail must be >= horizon")

    out_posix = Path(args.out).as_posix()
    if "historical_data" in out_posix:
        raise ValueError("refusing to write under historical_data")
    if "outputs/backtests" in out_posix:
        raise ValueError("refusing to write under outputs/backtests")
    if _ALLOWED_OUT_PREFIX not in out_posix:
        raise ValueError(f"output_dir must be under {_ALLOWED_OUT_PREFIX}/")

    if not bar_dir(args).exists():
        raise ValueError(f"bar dir does not exist: {bar_dir(args)}")
    if not _is_plan(args) and Path(args.out).exists() and not args.overwrite:
        raise ValueError(f"output_dir already exists (use --overwrite): {args.out}")

    start, end = _resolve_window(args, splits)
    return {"splits": splits, "start": start, "end": end, "out": args.out,
            "horizon": args.horizon, "fee_rate": args.fee_rate, "buffer": args.buffer,
            "lead_in": args.lead_in, "tail": args.tail}


def _dates_in_range(start: str, end: str):
    d, last = date.fromisoformat(start), date.fromisoformat(end)
    while d <= last:
        yield d
        d += timedelta(days=1)


def load_bars(args, start: str, end: str):
    """Read 1m bar parquet partitions in [start, end] directly. Returns a DataFrame.

    Raises ValueError if a partition is missing a required column. ``event_time_ns``
    is derived from ``ts`` at nanosecond resolution regardless of the stored unit.
    """
    import pandas as pd  # noqa: PLC0415

    bd = bar_dir(args)
    files: list[str] = []
    for d in _dates_in_range(start, end):
        files += glob.glob(str(bd / f"date={d.isoformat()}" / "*.parquet"))
    if not files:
        raise ValueError(f"no bar parquet under {bd} for {start}..{end}")
    frames = []
    for f in sorted(files):
        df = pd.read_parquet(f)
        missing = [c for c in REQUIRED_BAR_COLUMNS if c not in df.columns]
        if missing:
            raise ValueError(f"bar parquet {f} missing required column(s): {missing}")
        frames.append(df[list(REQUIRED_BAR_COLUMNS)])
    big = pd.concat(frames, ignore_index=True)
    big["event_time_ns"] = big["ts"].to_numpy().astype("datetime64[ns]").astype("int64")
    return big


def _iso(ns: Any) -> Any:
    if ns is None:
        return None
    return datetime.fromtimestamp(int(ns) / 1e9, tz=timezone.utc).isoformat()


def _print_diagnostics(summary: dict, final: Path) -> None:
    pq_bytes = sum(os.path.getsize(p) for p in glob.glob(str(final / "split=*" / "*.parquet")))
    print(f"OUTPUT_DIR: {final}")
    print(f"raw_rows: {summary['raw_rows']}")
    print(f"output_rows: {summary['output_rows']}")
    print(f"total_dropped: {summary['total_dropped']}")
    print(f"split_counts: {summary['split_counts']}")
    print(f"month_counts: {summary['month_counts']}")
    print(f"label_distribution_total: {summary['label_distribution_total']}")
    print(f"label_distribution_by_split: {summary['label_distribution_by_split']}")
    print(f"first_ts_by_split: {{{', '.join(f'{k}: {_iso(v)}' for k, v in summary['first_ts_by_split'].items())}}}")
    print(f"last_ts_by_split: {{{', '.join(f'{k}: {_iso(v)}' for k, v in summary['last_ts_by_split'].items())}}}")
    print(f"feature_count: {len(summary['feature_columns'])}")
    print(f"parts_count: {len(summary['parts'])}")
    print(f"total_parquet_bytes: {pq_bytes}")


def run(args, *, bars_loader=None, part_writer=None):
    """Validate, build, write. Returns ``(summary, final_path)`` or ``None`` for a plan."""
    plan = preflight(args)
    if _is_plan(args):
        print("PLAN_ONLY (no load, no write)")
        print(f"  window: {plan['start']} .. {plan['end']}  splits={plan['splits']}")
        print(f"  out: {plan['out']}  horizon={plan['horizon']} lead_in={plan['lead_in']} tail={plan['tail']}")
        print(f"  features: {len(FEATURE_COLUMNS_V2)} (v2)")
        return None

    loader = bars_loader or load_bars
    bars = loader(args, plan["start"], plan["end"])

    parts, summary = build_dataset_partitioned(
        bars, build_fn=build_dataset_v2, feature_columns=FEATURE_COLUMNS_V2,
        horizon=args.horizon, fee_rate=args.fee_rate, buffer=args.buffer,
        keep_splits=tuple(plan["splits"]), lead_in=args.lead_in, tail=args.tail,
    )
    final = write_partitioned_dataset(
        parts, args.out, summary=summary, feature_columns=FEATURE_COLUMNS_V2,
        part_writer=part_writer or parquet_part_writer_v2, overwrite=args.overwrite,
    )
    _print_diagnostics(summary, final)
    return summary, final


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        run(args)
    except (ValueError, FileExistsError) as exc:
        print(f"PREFLIGHT_ERROR: {exc}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
