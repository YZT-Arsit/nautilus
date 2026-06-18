#!/usr/bin/env python3
"""CLI to build ML V2 label-variant train/validation datasets (B3-label).

Reuses the V2 direct-parquet loader (so order-flow bar columns reach the V2
features) and the month-chunked writer, but swaps the label via
``research.label_builder_v2`` to support: multiclass symmetric/asymmetric with a
configurable ``--horizon`` / ``--long-threshold`` / ``--short-threshold``, and a
``long_only_binary`` task (REST=0 / LONG=1). Reads only train+validation date
partitions - never the test window. Output restricted to
``outputs/research_datasets/``. No model training, no backtest.

Example::

    uv run --no-sync python scripts/build_ml_dataset_v2_variants.py \
        --task multiclass --horizon 30 --long-threshold 0.0015 --short-threshold 0.0015 \
        --out outputs/research_datasets/ml_v2_btcusdt_1m_h30_sym_train_val
"""
from __future__ import annotations

import argparse
import functools
import glob
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from research.dataset_builder_label_variants import build_dataset_variant  # noqa: E402
from research.dataset_builder_v2 import parquet_part_writer_v2  # noqa: E402
from research.dataset_writer import build_dataset_partitioned, write_partitioned_dataset  # noqa: E402
from research.features_v2 import FEATURE_COLUMNS_V2  # noqa: E402
from research.label_builder_v2 import LONG_ONLY_BINARY, MULTICLASS, VALID_TASKS, task_codes  # noqa: E402
from research.splits import DEFAULT_SPLITS  # noqa: E402
from scripts.build_ml_dataset_v2 import REQUIRED_BAR_COLUMNS, load_bars  # noqa: E402

VALID_SPLITS = ("train", "validation")          # test is never built here
_ALLOWED_OUT_PREFIX = "outputs/research_datasets"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Build ML V2 label-variant datasets (B3-label)")
    p.add_argument("--root", default="historical_data/market_data")
    p.add_argument("--exchange", default="BINANCE")
    p.add_argument("--venue-type", default="spot")
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--bar-type", default="1m")
    p.add_argument("--splits", default="train,validation")
    p.add_argument("--out", required=True)
    p.add_argument("--task", default=MULTICLASS, choices=list(VALID_TASKS))
    p.add_argument("--horizon", type=int, default=15)
    p.add_argument("--long-threshold", type=float, default=0.0015)
    p.add_argument("--short-threshold", type=float, default=0.0015)
    p.add_argument("--fee-rate", type=float, default=0.0005)
    p.add_argument("--buffer", type=float, default=0.0005)
    p.add_argument("--lead-in", type=int, default=120)
    p.add_argument("--tail", type=int, default=None, help="default: horizon")
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


def _resolve_window(args, splits) -> tuple[str, str]:
    if args.start and args.end:
        return args.start, args.end
    starts = [DEFAULT_SPLITS[s][0] for s in splits]
    ends = [DEFAULT_SPLITS[s][1] for s in splits]
    return (args.start or min(starts)), (args.end or max(ends))


def _tail(args) -> int:
    return args.tail if args.tail is not None else args.horizon


def preflight(args) -> dict[str, Any]:
    splits = parse_splits(args.splits)
    if not splits:
        raise ValueError("splits must be non-empty")
    bad = [s for s in splits if s not in VALID_SPLITS]
    if bad:
        raise ValueError(f"invalid splits {bad}; allowed (no test): {VALID_SPLITS}")
    if args.task not in VALID_TASKS:
        raise ValueError(f"invalid task {args.task!r}; allowed: {VALID_TASKS}")
    if args.horizon <= 0:
        raise ValueError("horizon must be > 0")
    if args.long_threshold <= 0 or args.short_threshold <= 0:
        raise ValueError("thresholds must be > 0")
    if args.lead_in < 120:
        raise ValueError("lead_in must be >= 120 (feature warmup)")
    if _tail(args) < args.horizon:
        raise ValueError("tail must be >= horizon")

    out_posix = Path(args.out).as_posix()
    if "historical_data" in out_posix:
        raise ValueError("refusing to write under historical_data")
    if "outputs/backtests" in out_posix:
        raise ValueError("refusing to write under outputs/backtests")
    if _ALLOWED_OUT_PREFIX not in out_posix:
        raise ValueError(f"output_dir must be under {_ALLOWED_OUT_PREFIX}/")

    from scripts.build_ml_dataset_v2 import bar_dir  # noqa: PLC0415
    if not bar_dir(args).exists():
        raise ValueError(f"bar dir does not exist: {bar_dir(args)}")
    if not _is_plan(args) and Path(args.out).exists() and not args.overwrite:
        raise ValueError(f"output_dir already exists (use --overwrite): {args.out}")

    start, end = _resolve_window(args, splits)
    return {"splits": splits, "start": start, "end": end, "out": args.out,
            "task": args.task, "horizon": args.horizon, "tail": _tail(args),
            "long_threshold": args.long_threshold, "short_threshold": args.short_threshold,
            "lead_in": args.lead_in, "fee_rate": args.fee_rate, "buffer": args.buffer}


def _print_diagnostics(summary: dict, final: Path, plan: dict) -> None:
    pq_bytes = sum(os.path.getsize(p) for p in glob.glob(str(final / "split=*" / "*.parquet")))
    print(f"OUTPUT_DIR: {final}")
    print(f"task: {plan['task']}  horizon: {plan['horizon']}  "
          f"long_threshold: {plan['long_threshold']}  short_threshold: {plan['short_threshold']}")
    print(f"raw_rows: {summary['raw_rows']}  output_rows: {summary['output_rows']}  "
          f"total_dropped: {summary['total_dropped']}")
    print(f"split_counts: {summary['split_counts']}")
    print(f"label_distribution_total: {summary['label_distribution_total']}")
    print(f"label_distribution_by_split: {summary['label_distribution_by_split']}")
    print(f"feature_count: {len(summary['feature_columns'])}  parts: {len(summary['parts'])}  "
          f"parquet_bytes: {pq_bytes}")


def run(args, *, bars_loader=None, part_writer=None):
    """Validate, build, write. Returns ``(summary, final)`` or ``None`` for a plan."""
    plan = preflight(args)
    if _is_plan(args):
        print("PLAN_ONLY (no load, no write)")
        print(f"  task={plan['task']} horizon={plan['horizon']} "
              f"long_threshold={plan['long_threshold']} short_threshold={plan['short_threshold']}")
        print(f"  window: {plan['start']} .. {plan['end']}  splits={plan['splits']}  out: {plan['out']}")
        print(f"  label_mapping: {task_codes(plan['task'])}  features: {len(FEATURE_COLUMNS_V2)} (v2)")
        return None

    loader = bars_loader or load_bars
    bars = loader(args, plan["start"], plan["end"])

    build_fn = functools.partial(build_dataset_variant, task=plan["task"],
                                 long_threshold=plan["long_threshold"],
                                 short_threshold=plan["short_threshold"])
    parts, summary = build_dataset_partitioned(
        bars, build_fn=build_fn, feature_columns=FEATURE_COLUMNS_V2,
        horizon=plan["horizon"], fee_rate=plan["fee_rate"], buffer=plan["buffer"],
        keep_splits=tuple(plan["splits"]), lead_in=plan["lead_in"], tail=plan["tail"],
    )
    final = write_partitioned_dataset(
        parts, args.out, summary=summary, feature_columns=FEATURE_COLUMNS_V2,
        part_writer=part_writer or parquet_part_writer_v2, overwrite=args.overwrite,
    )
    _print_diagnostics(summary, final, plan)
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
