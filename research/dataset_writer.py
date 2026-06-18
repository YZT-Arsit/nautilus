"""Month-chunked dataset writer (memory-bounded, atomic-finalize).

Builds the ML dataset **one calendar month at a time** so peak memory stays at
~one month of rows instead of the whole ~1M-row series. Each month is built over
a window that includes a **lead-in** (>= feature warmup) and a **tail** (>= label
horizon) so a month's rows are byte-identical to a one-shot contiguous build -
features use only past bars (the lead-in supplies warmup), labels use the tail,
and split-boundary purge is computed with the tail's split labels. Lead-in/tail
rows are excluded from the month's part, so the union of parts equals a one-shot
build exactly (no duplicates, no missing rows).

Writing is **temp-dir + atomic finalize**: parts go to a sibling ``.tmp`` dir,
``summary.json`` is written last as a completion marker, then the temp dir is
atomically renamed to the final dir. Any failure removes the temp dir and never
touches an existing final dir.

The numeric/partition core is pure-Python (no pandas). Parquet writing is the one
optional pandas/pyarrow step, isolated in :func:`parquet_part_writer` and
injectable, so all logic is testable without pandas. Imports no nautilus_trader.
"""
from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from research.dataset_builder import DATASET_COLUMNS, build_dataset
from research.features import FEATURE_COLUMNS, MAX_WARMUP_BARS
from research.label_builder import DEFAULT_BUFFER, DEFAULT_FEE_RATE, DEFAULT_HORIZON
from research.splits import DEFAULT_SPLITS, split_of_ts

# Default windowing: lead-in >= longest feature warmup; tail >= label horizon.
DEFAULT_LEAD_IN = MAX_WARMUP_BARS        # 120 bars (>= first-valid 119)
DEFAULT_TAIL = DEFAULT_HORIZON           # 15 bars

# Output dtype spec applied by the parquet writer. Features are float32 (the bulk
# of the dataset); reference/label price columns stay float64 for precision.
DTYPE_SPEC: dict[str, str] = {
    **{name: "float32" for name in FEATURE_COLUMNS},
    "event_time_ns": "int64",
    "label_horizon_ts": "int64",
    "close_t": "float64",
    "future_return_15m": "float64",
    "label_code": "int8",
    "label_horizon": "int16",
    "is_valid": "bool",
    "split": "category",
    "label_class": "category",
    "instrument_id": "category",
}


def partition_key(ts_ns: int) -> str:
    """UTC ``YYYY-MM`` month key for a nanosecond timestamp."""
    d = datetime.fromtimestamp(int(ts_ns) // 1_000_000_000, tz=timezone.utc)
    return f"{d.year:04d}-{d.month:02d}"


def build_dataset_partitioned(
    bars: Any,
    *,
    horizon: int = DEFAULT_HORIZON,
    fee_rate: float = DEFAULT_FEE_RATE,
    buffer: float = DEFAULT_BUFFER,
    splits: dict[str, tuple[str, str]] = DEFAULT_SPLITS,
    lead_in: int = DEFAULT_LEAD_IN,
    tail: int = DEFAULT_TAIL,
    keep_splits: tuple[str, ...] | None = None,
) -> tuple[list[tuple[str, str, list[dict]]], dict]:
    """Build the dataset month-by-month. Returns ``(parts, summary)``.

    ``parts`` is a list of ``(split, month_key, rows)``; each row belongs to that
    split and month. ``keep_splits`` (e.g. ``("train","validation")``) restricts
    which splits are emitted - rows of other splits are dropped (so e.g. test is
    excluded while still serving as label-tail context if loaded).
    """
    from research.features import to_columns  # local import; pure-Python

    cols = to_columns(bars)
    n = len(cols.get("event_time_ns", []))
    order = sorted(range(n), key=lambda i: int(cols["event_time_ns"][i]))
    scols = {k: [v[i] for i in order] for k, v in cols.items()}
    if "instrument_id" not in scols:
        scols["instrument_id"] = ["UNKNOWN"] * n

    keys = [partition_key(int(t)) for t in scols["event_time_ns"]]
    months: list[str] = []
    for k in keys:
        if not months or months[-1] != k:
            if k not in months:
                months.append(k)

    parts: list[tuple[str, str, list[dict]]] = []
    for m in months:
        idxs = [i for i in range(n) if keys[i] == m]
        if not idxs:
            continue
        w0 = max(0, idxs[0] - lead_in)
        w1 = min(n, idxs[-1] + tail + 1)
        window = {k: v[w0:w1] for k, v in scols.items()}
        rows, _ = build_dataset(window, horizon=horizon, fee_rate=fee_rate,
                                buffer=buffer, splits=splits)
        # keep only this month's rows (excludes lead-in/tail rows of other months)
        month_rows = [r for r in rows if partition_key(r["event_time_ns"]) == m]
        by_split: dict[str, list[dict]] = {}
        for r in month_rows:
            if keep_splits is not None and r["split"] not in keep_splits:
                continue
            by_split.setdefault(r["split"], []).append(r)
        for sp in sorted(by_split):
            parts.append((sp, m, by_split[sp]))

    summary = _summarize(parts, raw_rows=n, horizon=horizon,
                         fee_rate=fee_rate, buffer=buffer,
                         lead_in=lead_in, tail=tail, keep_splits=keep_splits)
    return parts, summary


def _summarize(parts, *, raw_rows, horizon, fee_rate, buffer, lead_in, tail, keep_splits) -> dict:
    output_rows = sum(len(rows) for _, _, rows in parts)
    split_counts: dict[str, int] = {}
    month_counts: dict[str, int] = {}
    label_total = {"LONG": 0, "SHORT": 0, "NO_TRADE": 0}
    by_split: dict[str, dict] = {}
    first_ts: dict[str, int] = {}
    last_ts: dict[str, int] = {}
    manifest = []
    for sp, m, rows in parts:
        split_counts[sp] = split_counts.get(sp, 0) + len(rows)
        month_counts[m] = month_counts.get(m, 0) + len(rows)
        d = by_split.setdefault(sp, {"LONG": 0, "SHORT": 0, "NO_TRADE": 0})
        for r in rows:
            label_total[r["label_class"]] += 1
            d[r["label_class"]] += 1
            ts = r["event_time_ns"]
            first_ts[sp] = min(first_ts.get(sp, ts), ts)
            last_ts[sp] = max(last_ts.get(sp, ts), ts)
        manifest.append({"split": sp, "month": m, "rows": len(rows),
                         "path": f"split={sp}/part-{m}.parquet"})
    return {
        "raw_rows": raw_rows,
        "output_rows": output_rows,
        "total_dropped": raw_rows - output_rows,
        "split_counts": split_counts,
        "month_counts": month_counts,
        "label_distribution_total": label_total,
        "label_distribution_by_split": by_split,
        "first_ts_by_split": first_ts,
        "last_ts_by_split": last_ts,
        "feature_columns": list(FEATURE_COLUMNS),
        "horizon": horizon,
        "label_threshold": 2.0 * float(fee_rate) + float(buffer),
        "lead_in": lead_in,
        "tail": tail,
        "keep_splits": list(keep_splits) if keep_splits else None,
        "parts": manifest,
    }


# --- writing (temp dir + atomic finalize) -----------------------------------

def parquet_part_writer(rows: list[dict], dest: Path) -> None:
    """Default part writer: typed parquet via pandas (requires pandas+pyarrow)."""
    import pandas as pd  # noqa: PLC0415

    df = pd.DataFrame(rows, columns=DATASET_COLUMNS)
    for col, dt in DTYPE_SPEC.items():
        if col in df.columns:
            df[col] = df[col].astype(dt)
    df.to_parquet(dest, index=False)


def _readme(summary: dict) -> str:
    return (
        "# ML V1 dataset (BTCUSDT 1m, train+validation)\n\n"
        f"- output_rows: {summary['output_rows']}\n"
        f"- splits: {summary['split_counts']}\n"
        f"- horizon: {summary['horizon']} bars  label_threshold: {summary['label_threshold']}\n"
        f"- features ({len(summary['feature_columns'])}): see feature_columns.json\n"
        "- Built month-chunked; features point-in-time; labels cost-aware 3-class.\n"
        "- Generated by research/dataset_writer.py. Do not edit by hand.\n"
    )


def write_partitioned_dataset(
    parts: list[tuple[str, str, list[dict]]],
    output_dir: str | Path,
    *,
    summary: dict,
    overwrite: bool = False,
    part_writer: Callable[[list[dict], Path], None] = parquet_part_writer,
) -> Path:
    """Write parts + metadata under a temp dir, then atomically rename to final.

    Raises ``FileExistsError`` if ``output_dir`` exists and ``overwrite`` is
    False. On any failure the temp dir is removed and an existing final dir is
    never touched. ``summary.json`` is written **last** (completion marker).
    """
    final = Path(output_dir)
    if final.exists():
        if not overwrite:
            raise FileExistsError(f"output_dir already exists: {final}")
    tmp = final.parent / ("." + final.name + ".tmp")
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    try:
        for sp, m, rows in parts:
            dest = tmp / f"split={sp}" / f"part-{m}.parquet"
            dest.parent.mkdir(parents=True, exist_ok=True)
            part_writer(rows, dest)
        (tmp / "feature_columns.json").write_text(
            json.dumps(list(FEATURE_COLUMNS), indent=2), encoding="utf-8")
        (tmp / "README.md").write_text(_readme(summary), encoding="utf-8")
        # summary.json LAST = completion marker.
        (tmp / "summary.json").write_text(json.dumps(summary, indent=2, default=str),
                                          encoding="utf-8")
        if final.exists() and overwrite:
            shutil.rmtree(final)
        os.replace(tmp, final)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return final
