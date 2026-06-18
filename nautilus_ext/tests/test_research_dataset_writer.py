"""Tests for research/dataset_writer.py (month-chunked writer, pure-Python core).

Logic (chunking, partition equivalence, purge, finalize/cleanup) is tested
without pandas via an injected JSON part-writer. The actual parquet dtypes are
checked behind ``pytest.importorskip('pandas')`` so they run on the server.
"""
from __future__ import annotations

import inspect
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from research.dataset_builder import build_dataset  # noqa: E402
from research.dataset_writer import (  # noqa: E402
    DTYPE_SPEC,
    build_dataset_partitioned,
    partition_key,
    write_partitioned_dataset,
)
from research.features import FEATURE_COLUMNS  # noqa: E402
from research.splits import split_of_ts  # noqa: E402


# Synthetic series straddling the 2025-12 -> 2026-01 month AND train->val split
# boundary: 180 Dec bars (21:00..23:59) + 60 Jan bars (00:00..00:59) = 240 bars.
def _boundary_bars():
    start = int(datetime(2025, 12, 31, 21, 0, tzinfo=timezone.utc).timestamp()) * 1_000_000_000
    n = 240
    close = [100.0 + 5.0 * math.sin(i / 7.0) + 0.01 * i for i in range(n)]
    return {
        "event_time_ns": [start + i * 60_000_000_000 for i in range(n)],
        "instrument_id": ["BTCUSDT.BINANCE"] * n,
        "open": [c - 0.1 for c in close],
        "high": [c + 0.5 for c in close],
        "low": [c - 0.5 for c in close],
        "close": close,
        "volume": [100.0 + 10.0 * math.sin(i / 5.0) + 0.1 * i for i in range(n)],
    }


def _json_writer(rows, dest):
    """pandas-free part writer used for logic tests."""
    dest.write_text(json.dumps([r["event_time_ns"] for r in rows]), encoding="utf-8")


# --- partition key ----------------------------------------------------------

def test_partition_key_utc_month():
    ts = int(datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc).timestamp()) * 1_000_000_000
    assert partition_key(ts) == "2026-01"
    ts2 = int(datetime(2025, 12, 31, 23, 59, tzinfo=timezone.utc).timestamp()) * 1_000_000_000
    assert partition_key(ts2) == "2025-12"


# --- chunked == one-shot (the core invariant) -------------------------------

def test_partitioned_equals_one_shot():
    bars = _boundary_bars()
    one_shot, _ = build_dataset(bars)
    parts, _ = build_dataset_partitioned(bars)
    chunk_rows = [r for _, _, rows in parts for r in rows]
    one_ts = sorted(r["event_time_ns"] for r in one_shot)
    chunk_ts = sorted(r["event_time_ns"] for r in chunk_rows)
    assert chunk_ts == one_ts                      # same rows, same count
    assert len(chunk_ts) == len(set(chunk_ts))     # no duplicates across parts
    # full row equality (feature + label values identical to one-shot)
    a = {r["event_time_ns"]: r for r in one_shot}
    for r in chunk_rows:
        assert r == a[r["event_time_ns"]]


def test_no_duplicate_event_time_across_parts():
    parts, _ = build_dataset_partitioned(_boundary_bars())
    seen = set()
    for _, _, rows in parts:
        for r in rows:
            assert r["event_time_ns"] not in seen
            seen.add(r["event_time_ns"])


# --- split assignment + boundary purge --------------------------------------

def test_parts_split_and_month_assignment():
    parts, _ = build_dataset_partitioned(_boundary_bars())
    for sp, m, rows in parts:
        for r in rows:
            assert r["split"] == sp
            assert partition_key(r["event_time_ns"]) == m
    splits_present = {sp for sp, _, _ in parts}
    assert "train" in splits_present and "validation" in splits_present


def test_no_label_crosses_split_boundary():
    # every kept row's label horizon bar must be in the SAME split (purge worked).
    parts, _ = build_dataset_partitioned(_boundary_bars())
    for sp, _, rows in parts:
        for r in rows:
            assert split_of_ts(r["label_horizon_ts"]) == sp


def test_keep_splits_filters_out_other_splits():
    parts, summary = build_dataset_partitioned(_boundary_bars(), keep_splits=("train",))
    assert {sp for sp, _, _ in parts} == {"train"}
    assert set(summary["split_counts"]) == {"train"}


# --- summary consistency ----------------------------------------------------

def test_summary_counts_match_parts():
    parts, summary = build_dataset_partitioned(_boundary_bars())
    assert summary["output_rows"] == sum(len(r) for _, _, r in parts)
    assert sum(summary["split_counts"].values()) == summary["output_rows"]
    assert sum(summary["month_counts"].values()) == summary["output_rows"]
    d = summary["label_distribution_total"]
    assert d["LONG"] + d["SHORT"] + d["NO_TRADE"] == summary["output_rows"]
    assert summary["feature_columns"] == list(FEATURE_COLUMNS)


# --- writer safety (temp dir + atomic finalize), pandas-free ----------------

def test_write_creates_final_with_metadata(tmp_path):
    parts, summary = build_dataset_partitioned(_boundary_bars())
    out = tmp_path / "ds"
    final = write_partitioned_dataset(parts, out, summary=summary, part_writer=_json_writer)
    assert final.exists()
    assert (final / "summary.json").exists()
    assert (final / "feature_columns.json").exists()
    assert (final / "README.md").exists()
    assert (final / "split=train").exists() and (final / "split=validation").exists()
    fc = json.loads((final / "feature_columns.json").read_text())
    assert fc == list(FEATURE_COLUMNS)
    # no leftover temp dir
    assert not (tmp_path / ".ds.tmp").exists()


def test_existing_output_dir_raises(tmp_path):
    parts, summary = build_dataset_partitioned(_boundary_bars())
    out = tmp_path / "ds"
    out.mkdir()
    with pytest.raises(FileExistsError):
        write_partitioned_dataset(parts, out, summary=summary, part_writer=_json_writer)


def test_failure_cleans_temp_and_keeps_existing_final(tmp_path):
    parts, summary = build_dataset_partitioned(_boundary_bars())
    out = tmp_path / "ds"

    def _boom(rows, dest):
        raise RuntimeError("disk full")

    with pytest.raises(RuntimeError, match="disk full"):
        write_partitioned_dataset(parts, out, summary=summary, part_writer=_boom)
    assert not out.exists()                    # no partial final dir
    assert not (tmp_path / ".ds.tmp").exists()  # temp cleaned up


def test_overwrite_false_is_default(tmp_path):
    parts, summary = build_dataset_partitioned(_boundary_bars())
    out = tmp_path / "ds"
    write_partitioned_dataset(parts, out, summary=summary, part_writer=_json_writer)
    # default overwrite=False -> second write must refuse, leaving the first intact.
    with pytest.raises(FileExistsError):
        write_partitioned_dataset(parts, out, summary=summary, part_writer=_json_writer)
    assert (out / "summary.json").exists()


# --- dtype spec (pure) + actual parquet dtypes (pandas, server) -------------

def test_dtype_spec_features_float32():
    for name in FEATURE_COLUMNS:
        assert DTYPE_SPEC[name] == "float32"
    assert DTYPE_SPEC["event_time_ns"] == "int64"
    assert DTYPE_SPEC["label_code"] == "int8"
    assert DTYPE_SPEC["split"] == "category"
    assert DTYPE_SPEC["label_class"] == "category"
    assert DTYPE_SPEC["close_t"] == "float64"


def test_parquet_part_dtypes_roundtrip(tmp_path):
    pd = pytest.importorskip("pandas")
    pytest.importorskip("pyarrow")
    from research.dataset_writer import parquet_part_writer

    parts, _ = build_dataset_partitioned(_boundary_bars())
    sp, m, rows = parts[0]
    dest = tmp_path / "part.parquet"
    parquet_part_writer(rows, dest)
    df = pd.read_parquet(dest)
    for name in FEATURE_COLUMNS:
        assert str(df[name].dtype) == "float32", name
    assert str(df["event_time_ns"].dtype) == "int64"
    assert str(df["label_code"].dtype) == "int8"


# --- source scan ------------------------------------------------------------

def test_writer_source_clean():
    import research.dataset_writer as w

    src = inspect.getsource(w)
    assert "import nautilus_trader" not in src and "from nautilus_trader" not in src
    for banned in ("import sklearn", "import lightgbm", "import scipy",
                   "import torch", "import tensorflow"):
        assert banned not in src
    for net in ("import websocket", "import aiohttp", "import requests",
                "import urllib", "import socket"):
        assert net not in src
    for order in ("api_key", "secret", "place_order", "new_order",
                  "cancel_order", "/api/v3/order", "/sapi/"):
        assert order not in src
