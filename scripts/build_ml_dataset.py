#!/usr/bin/env python3
"""CLI to build the ML V1 train/validation research dataset (month-chunked).

Thin orchestrator: it does **no** feature/label/split/writer logic itself - it
calls ``data_engine.load_events`` to read bars and
``research.dataset_writer.{build_dataset_partitioned,write_partitioned_dataset}``
to build + write. Output is restricted to ``outputs/research_datasets/`` (never
``historical_data/`` or ``outputs/backtests/``). No model training, no backtest.

Usage::

    python scripts/build_ml_dataset.py \
        --root historical_data/market_data \
        --exchange BINANCE --venue-type spot --symbol BTCUSDT --bar-type 1m \
        --splits train,validation \
        --out outputs/research_datasets/ml_v1_btcusdt_1m_train_val \
        --horizon 15 --fee-rate 0.0005 --buffer 0.0005 --lead-in 120 --tail 15

``--dry-run`` / ``--plan-only`` validate args + print the plan and load/write
nothing. ``load_events`` is imported lazily so this module imports without pandas.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Repo-root bootstrap: when run as ``python scripts/build_ml_dataset.py`` the
# script dir (``scripts/``), not the repo root, is on sys.path[0], so the
# top-level ``research`` package would not import. Insert the repo root first.
_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from research.dataset_writer import (
    build_dataset_partitioned,
    write_partitioned_dataset,
)
from research.features import FEATURE_COLUMNS
from research.splits import DEFAULT_SPLITS

VALID_SPLITS = ("train", "validation", "test")
_ALLOWED_OUT_PREFIX = "outputs/research_datasets"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Build ML V1 train/validation research dataset")
    p.add_argument("--root", default="historical_data/market_data")
    p.add_argument("--exchange", default="BINANCE")
    p.add_argument("--venue-type", default="spot")
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--bar-type", default="1m")
    p.add_argument("--timestamp-column", default="ts")
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


def _is_plan(args: argparse.Namespace) -> bool:
    return bool(getattr(args, "dry_run", False) or getattr(args, "plan_only", False))


def preflight(args: argparse.Namespace) -> dict[str, Any]:
    """Validate args (raises ValueError on any violation). Returns the resolved plan."""
    splits = parse_splits(args.splits)
    if not splits:
        raise ValueError("splits must be non-empty")
    bad = [s for s in splits if s not in VALID_SPLITS]
    if bad:
        raise ValueError(f"invalid splits {bad}; allowed: {VALID_SPLITS}")
    if args.horizon <= 0:
        raise ValueError("horizon must be > 0")
    if args.fee_rate < 0:
        raise ValueError("fee_rate must be >= 0")
    if args.buffer < 0:
        raise ValueError("buffer must be >= 0")
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

    if not Path(args.root).exists():
        raise ValueError(f"data root does not exist: {args.root}")

    if not _is_plan(args) and Path(args.out).exists() and not args.overwrite:
        raise ValueError(f"output_dir already exists (use --overwrite): {args.out}")

    start, end = _resolve_window(args, splits)
    return {"splits": splits, "start": start, "end": end, "out": args.out,
            "horizon": args.horizon, "fee_rate": args.fee_rate, "buffer": args.buffer,
            "lead_in": args.lead_in, "tail": args.tail}


def _resolve_window(args: argparse.Namespace, splits: list[str]) -> tuple[str, str]:
    if args.start and args.end:
        return args.start, args.end
    starts = [DEFAULT_SPLITS[s][0] for s in splits]
    ends = [DEFAULT_SPLITS[s][1] for s in splits]
    return (args.start or min(starts)), (args.end or max(ends))


def build_data_config(args: argparse.Namespace, start: str, end: str) -> dict[str, Any]:
    return {
        "mode": "hive_parquet_bars",
        "root": args.root,
        "timestamp_column": args.timestamp_column,
        "filters": {"exchange": args.exchange, "venue_type": args.venue_type,
                    "symbol": args.symbol, "bar_type": args.bar_type},
        "start": start,
        "end": end,
    }


def _attr(e: Any, name: str) -> Any:
    return e.get(name) if isinstance(e, dict) else getattr(e, name, None)


def bar_to_row(e: Any) -> dict[str, Any]:
    return {
        "event_time_ns": int(_attr(e, "event_time_ns")),
        "instrument_id": str(_attr(e, "instrument_id") or "UNKNOWN"),
        "open": float(_attr(e, "open")), "high": float(_attr(e, "high")),
        "low": float(_attr(e, "low")), "close": float(_attr(e, "close")),
        "volume": float(_attr(e, "volume") or 0.0),
    }


def _resolve_load_events():
    from data_engine import load_events  # lazy: keeps this module importable w/o pandas
    return load_events


def _iso(ns: Any) -> Any:
    if ns is None:
        return None
    return datetime.fromtimestamp(int(ns) / 1e9, tz=timezone.utc).isoformat()


def _print_diagnostics(summary: dict, final: Path) -> None:
    pq_bytes = sum(os.path.getsize(p) for p in glob.glob(str(final / "split=*" / "*.parquet")))
    print(f"OUTPUT_DIR: {final}")
    print(f"raw_rows: {summary['raw_rows']}")
    print(f"output_rows: {summary['output_rows']}")
    print(f"split_counts: {summary['split_counts']}")
    print(f"month_counts: {summary['month_counts']}")
    print(f"label_distribution_total: {summary['label_distribution_total']}")
    print(f"label_distribution_by_split: {summary['label_distribution_by_split']}")
    print(f"first_ts_by_split: {{{', '.join(f'{k}: {_iso(v)}' for k, v in summary['first_ts_by_split'].items())}}}")
    print(f"last_ts_by_split: {{{', '.join(f'{k}: {_iso(v)}' for k, v in summary['last_ts_by_split'].items())}}}")
    print(f"feature_count: {len(summary['feature_columns'])}")
    print(f"parts_count: {len(summary['parts'])}")
    print(f"total_parquet_bytes: {pq_bytes}")
    print(f"summary_json: {final / 'summary.json'}")
    print(f"feature_columns_json: {final / 'feature_columns.json'}")


def run(args: argparse.Namespace, *, load_events_fn=None, part_writer=None):
    """Validate, build, write. Returns ``(summary, final_path)`` or ``None`` for a plan."""
    plan = preflight(args)
    if _is_plan(args):
        print("PLAN_ONLY (no load, no write)")
        print(f"  window: {plan['start']} .. {plan['end']}  splits={plan['splits']}")
        print(f"  out: {plan['out']}  horizon={plan['horizon']} "
              f"lead_in={plan['lead_in']} tail={plan['tail']}")
        print(f"  features: {len(FEATURE_COLUMNS)}")
        return None

    le = load_events_fn or _resolve_load_events()
    data_cfg = build_data_config(args, plan["start"], plan["end"])
    warmup, live = le(data_cfg)
    rows_in = [bar_to_row(e) for e in (list(warmup) + list(live))]

    parts, summary = build_dataset_partitioned(
        rows_in, horizon=args.horizon, fee_rate=args.fee_rate, buffer=args.buffer,
        keep_splits=tuple(plan["splits"]), lead_in=args.lead_in, tail=args.tail,
    )
    write_kwargs: dict[str, Any] = {"summary": summary, "overwrite": args.overwrite}
    if part_writer is not None:
        write_kwargs["part_writer"] = part_writer
    final = write_partitioned_dataset(parts, args.out, **write_kwargs)
    _print_diagnostics(summary, final)
    return summary, final


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        run(args)
    except (ValueError, FileExistsError) as exc:
        print(f"PREFLIGHT_ERROR: {exc}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
