"""Tests for research/dataset_builder.py (pure-Python, no pandas)."""
from __future__ import annotations

import inspect
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from research.dataset_builder import DATASET_COLUMNS, build_dataset  # noqa: E402
from research.features import FEATURE_COLUMNS  # noqa: E402
from research.label_builder import classify_return, label_threshold  # noqa: E402


def _isnan(x):
    return isinstance(x, float) and math.isnan(x)


def _make_bars(n, start_ts_ns):
    close = [100.0 + 5.0 * math.sin(i / 7.0) + 0.01 * i for i in range(n)]
    return {
        "event_time_ns": [start_ts_ns + i * 60_000_000_000 for i in range(n)],
        "instrument_id": ["BTCUSDT.BINANCE"] * n,
        "open": [c - 0.1 for c in close],
        "high": [c + 0.5 for c in close],
        "low": [c - 0.5 for c in close],
        "close": close,
        "volume": [100.0 + 10.0 * math.sin(i / 5.0) + 0.1 * i for i in range(n)],
    }


_START = int(datetime(2024, 7, 1, tzinfo=timezone.utc).timestamp()) * 1_000_000_000


# --- 1/2. builds dataset + schema complete ----------------------------------

def test_build_dataset_and_schema():
    rows, summary = build_dataset(_make_bars(250, _START))
    assert rows, "expected non-empty dataset"
    for r in rows:
        assert set(r.keys()) == set(DATASET_COLUMNS)


# --- 3. summary complete ----------------------------------------------------

def test_summary_has_required_fields():
    _, summary = build_dataset(_make_bars(250, _START))
    required = {
        "raw_rows", "output_rows", "dropped_warmup_rows", "dropped_horizon_rows",
        "dropped_purge_rows", "dropped_nan_feature_rows", "split_counts",
        "label_distribution_total", "label_distribution_by_split", "feature_columns",
        "nan_counts", "first_ts", "last_ts",
    }
    assert required.issubset(summary.keys())
    assert summary["feature_columns"] == list(FEATURE_COLUMNS)


# --- 4. warmup / horizon drops + accounting identity ------------------------

def test_drop_accounting_identity():
    _, s = build_dataset(_make_bars(250, _START))
    total_dropped = (s["dropped_warmup_rows"] + s["dropped_horizon_rows"]
                     + s["dropped_purge_rows"] + s["dropped_nan_feature_rows"]
                     + s["dropped_no_split_rows"])
    assert s["raw_rows"] == s["output_rows"] + total_dropped
    assert s["dropped_warmup_rows"] > 0          # leading 120-window warmup
    assert s["dropped_horizon_rows"] == 15        # last H bars (all one split)
    assert s["dropped_purge_rows"] == 0 and s["dropped_no_split_rows"] == 0
    assert s["dropped_nan_feature_rows"] == 0     # smooth series -> no degenerate NaN


# --- 5. split counts consistent --------------------------------------------

def test_split_counts_sum_to_output():
    _, s = build_dataset(_make_bars(250, _START))
    assert sum(s["split_counts"].values()) == s["output_rows"]
    assert set(s["split_counts"]) <= {"train", "validation", "test"}  # no 'none' kept


# --- 6. label distribution sums to output ----------------------------------

def test_label_distribution_sums_to_output():
    _, s = build_dataset(_make_bars(250, _START))
    d = s["label_distribution_total"]
    assert d["LONG"] + d["SHORT"] + d["NO_TRADE"] == s["output_rows"]


# --- 7. no NaN in kept feature rows -----------------------------------------

def test_no_nan_in_valid_feature_rows():
    rows, _ = build_dataset(_make_bars(250, _START))
    for r in rows:
        for name in FEATURE_COLUMNS:
            assert not _isnan(r[name]), name
        assert r["is_valid"] is True


# --- 8. feature/label alignment ---------------------------------------------

def test_feature_label_alignment():
    rows, _ = build_dataset(_make_bars(250, _START))
    thr = label_threshold()
    for r in rows:
        assert not _isnan(r["future_return_15m"])
        assert r["label_class"] == classify_return(r["future_return_15m"], thr)
        assert r["label_horizon_ts"] == r["event_time_ns"] + 15 * 60_000_000_000


# --- sort independence ------------------------------------------------------

def test_unsorted_input_is_sorted_internally():
    bars = _make_bars(200, _START)
    # reverse the rows; builder must sort by event_time_ns before computing.
    rev = {k: list(reversed(v)) for k, v in bars.items()}
    rows_a, _ = build_dataset(bars)
    rows_b, _ = build_dataset(rev)
    ts_a = [r["event_time_ns"] for r in rows_a]
    ts_b = [r["event_time_ns"] for r in rows_b]
    assert ts_a == sorted(ts_a) and ts_b == sorted(ts_b)
    assert ts_a == ts_b


# --- 9/10. source scan ------------------------------------------------------

def test_research_modules_are_clean():
    import research.dataset_builder as dsb
    import research.features as feat
    import research.label_builder as lab
    import research.splits as spl

    for mod in (feat, lab, spl, dsb):
        src = inspect.getsource(mod)
        assert "import nautilus_trader" not in src and "from nautilus_trader" not in src
        for banned in ("import sklearn", "import lightgbm", "import scipy",
                       "import torch", "import tensorflow", "import xgboost"):
            assert banned not in src, f"{mod.__name__}: {banned}"
        for net in ("import websocket", "import aiohttp", "import requests",
                    "import urllib", "import socket"):
            assert net not in src, f"{mod.__name__}: {net}"
        for order in ("api_key", "secret", "place_order", "new_order",
                      "cancel_order", "/api/v3/order", "/sapi/"):
            assert order not in src, f"{mod.__name__}: {order}"
