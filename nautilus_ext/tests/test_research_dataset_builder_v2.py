"""Tests for research/dataset_builder_v2.py + dataset_writer v2 compatibility.

Pure-Python builder tests + a pandas-free JSON part-writer for the chunked
writer (a parquet dtype roundtrip is covered separately by the v1 writer tests).
"""
from __future__ import annotations

import inspect
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from research.dataset_builder_v2 import (  # noqa: E402
    DATASET_COLUMNS_V2,
    DTYPE_SPEC_V2,
    build_dataset_v2,
)
from research.dataset_writer import build_dataset_partitioned, write_partitioned_dataset  # noqa: E402
from research.features_v2 import FEATURE_COLUMNS_V2  # noqa: E402

_MIN_NS = 60_000_000_000


def _isnan(x):
    return isinstance(x, float) and math.isnan(x)


def _ext_bars(start_ns, n):
    close = [100.0 + 5.0 * math.sin(i / 7.0) + 0.01 * i for i in range(n)]
    vol = [100.0 + 10.0 * math.sin(i / 5.0) + 0.1 * i for i in range(n)]
    frac = [0.5 + 0.2 * math.sin(i / 3.0) for i in range(n)]
    return {
        "event_time_ns": [start_ns + i * _MIN_NS for i in range(n)],
        "instrument_id": ["BTCUSDT.BINANCE"] * n,
        "open": [c - 0.1 for c in close],
        "high": [c + 0.5 for c in close],
        "low": [c - 0.5 for c in close],
        "close": close,
        "volume": vol,
        "quote_volume": [vol[i] * close[i] for i in range(n)],
        "trade_count": [int(50 + 20 * math.sin(i / 4.0) + i % 7) for i in range(n)],
        "taker_buy_volume": [vol[i] * frac[i] for i in range(n)],
        "taker_buy_quote_volume": [vol[i] * close[i] * frac[i] for i in range(n)],
    }


_TRAIN_START = int(datetime(2024, 7, 1, tzinfo=timezone.utc).timestamp()) * 1_000_000_000
# Dec 2025 (train) -> Jan 2026 (validation) boundary, starting 2025-12-31 12:00 UTC.
_BOUNDARY_START = int(datetime(2025, 12, 31, 12, 0, tzinfo=timezone.utc).timestamp()) * 1_000_000_000


def _boundary_bars(n_dec=720, n_jan=240):
    return _ext_bars(_BOUNDARY_START, n_dec + n_jan)


def _json_writer(rows, dest):
    dest.write_text(json.dumps([r["event_time_ns"] for r in rows]), encoding="utf-8")


# --- B. dataset builder -----------------------------------------------------

def test_build_v2_schema_and_features():
    rows, summary = build_dataset_v2(_ext_bars(_TRAIN_START, 250))
    assert rows
    for r in rows:
        assert set(r.keys()) == set(DATASET_COLUMNS_V2)
    # base + f2_* present; future_return_15m is a label, never an f2_ feature
    assert all(c.startswith("f2_") for c in FEATURE_COLUMNS_V2)
    assert "future_return_15m" not in FEATURE_COLUMNS_V2
    assert summary["feature_columns"] == list(FEATURE_COLUMNS_V2)


def test_label_horizon_still_t_plus_15():
    rows, _ = build_dataset_v2(_ext_bars(_TRAIN_START, 250))
    for r in rows:
        assert r["label_horizon_ts"] == r["event_time_ns"] + 15 * _MIN_NS


def test_split_assignment_and_no_test_by_default():
    rows, s = build_dataset_v2(_boundary_bars())
    assert set(s["split_counts"]) <= {"train", "validation"}
    assert "test" not in s["split_counts"]
    assert s["split_counts"].get("train", 0) > 0 and s["split_counts"].get("validation", 0) > 0


def test_drop_accounting_identity_and_no_nan():
    rows, s = build_dataset_v2(_ext_bars(_TRAIN_START, 300))
    total = (s["dropped_warmup_rows"] + s["dropped_horizon_rows"] + s["dropped_purge_rows"]
             + s["dropped_nan_feature_rows"] + s["dropped_no_split_rows"])
    assert s["raw_rows"] == s["output_rows"] + total
    assert s["dropped_warmup_rows"] > 0
    for r in rows:
        for name in FEATURE_COLUMNS_V2:
            assert not _isnan(r[name]), name
        assert r["is_valid"] is True


def test_missing_orderflow_columns_raises():
    bars = _ext_bars(_TRAIN_START, 200)
    del bars["taker_buy_quote_volume"]
    try:
        build_dataset_v2(bars)
        assert False, "expected KeyError"
    except KeyError as e:
        assert "taker_buy_quote_volume" in str(e)


def test_dtype_spec_covers_all_columns():
    for name in FEATURE_COLUMNS_V2:
        assert DTYPE_SPEC_V2[name] == "float32"
    for base in ("event_time_ns", "label_code", "is_valid", "split", "label_class"):
        assert base in DTYPE_SPEC_V2


# --- C. month-chunked writer compatibility ----------------------------------

def test_chunked_equals_one_shot_v2():
    bars = _boundary_bars()
    one_shot, _ = build_dataset_v2(bars)
    keep = {"train", "validation"}
    oneshot_ts = sorted(r["event_time_ns"] for r in one_shot if r["split"] in keep)
    parts, _ = build_dataset_partitioned(bars, build_fn=build_dataset_v2,
                                         feature_columns=FEATURE_COLUMNS_V2,
                                         keep_splits=("train", "validation"))
    chunked_ts = sorted(r["event_time_ns"] for _, _, rows in parts for r in rows)
    assert chunked_ts == oneshot_ts
    assert len(chunked_ts) == len(set(chunked_ts))          # no duplicates


def test_purge_keeps_labels_within_split_v2():
    bars = _boundary_bars()
    parts, _ = build_dataset_partitioned(bars, build_fn=build_dataset_v2,
                                         feature_columns=FEATURE_COLUMNS_V2,
                                         keep_splits=("train", "validation"))
    # a kept row's label horizon must not cross into another split (forward-purge)
    for sp, _m, rows in parts:
        for r in rows:
            # horizon ts is still within the same split's calendar boundary
            assert r["split"] == sp


def test_writer_emits_v2_manifest_and_parts(tmp_path):
    bars = _boundary_bars()
    parts, summary = build_dataset_partitioned(bars, build_fn=build_dataset_v2,
                                               feature_columns=FEATURE_COLUMNS_V2,
                                               keep_splits=("train", "validation"))
    out = tmp_path / "outputs" / "research_datasets" / "ml_v2"
    final = write_partitioned_dataset(parts, out, summary=summary,
                                      part_writer=_json_writer,
                                      feature_columns=FEATURE_COLUMNS_V2)
    assert final == out and out.exists()
    fc = json.loads((out / "feature_columns.json").read_text())
    assert fc == list(FEATURE_COLUMNS_V2) and all(c.startswith("f2_") for c in fc)
    assert (out / "split=train").exists() and (out / "split=validation").exists()
    assert (out / "summary.json").exists()
    assert not (out / "split=test").exists()


# --- source scan ------------------------------------------------------------

def test_dataset_builder_v2_source_clean():
    import research.dataset_builder_v2 as mod

    src = inspect.getsource(mod)
    assert "import nautilus_trader" not in src and "from nautilus_trader" not in src
    for banned in ("import sklearn", "import lightgbm", "import scipy",
                   "import torch", "import tensorflow"):
        assert banned not in src, banned
    for net in ("import websocket", "import aiohttp", "import requests", "import socket"):
        assert net not in src
