"""Tests for research/label_builder_v2.py (pure-Python, no pandas)."""
from __future__ import annotations

import inspect
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from research.label_builder_v2 import (  # noqa: E402
    BINARY_CODES,
    LONG_ONLY_BINARY,
    MULTICLASS,
    MULTICLASS_CODES,
    build_labels_variant,
    classify_long_binary,
    classify_multiclass,
    label_distribution_variant,
    task_codes,
    variant_label_threshold_meta,
)

_MIN = 60_000_000_000
_START = int(datetime(2024, 7, 1, tzinfo=timezone.utc).timestamp()) * 1_000_000_000


def _isnan(x):
    return isinstance(x, float) and math.isnan(x)


def _series(closes):
    n = len(closes)
    return {"event_time_ns": [_START + i * _MIN for i in range(n)],
            "close": list(closes), "instrument_id": ["X"] * n}


# --- A. multiclass symmetric horizon sweep ----------------------------------

def test_horizon_5_uses_close_t_plus_5():
    closes = [100.0 * (1.0 + 0.001 * i) for i in range(20)]
    lab = build_labels_variant(_series(closes), task=MULTICLASS, horizon=5)
    t = 3
    assert lab["label_horizon"][t] == 5
    assert abs(lab["future_return_15m"][t] - (closes[t + 5] / closes[t] - 1.0)) < 1e-12
    assert lab["label_horizon_ts"][t] == _START + (t + 5) * _MIN
    # last 5 rows invalid (no t+5)
    assert lab["is_valid_label"][-1] is False and _isnan(lab["future_return_15m"][-1])


def test_horizon_30_uses_close_t_plus_30():
    closes = [100.0 + i for i in range(50)]
    lab = build_labels_variant(_series(closes), task=MULTICLASS, horizon=30)
    t = 5
    assert lab["label_horizon"][t] == 30
    assert abs(lab["future_return_15m"][t] - (closes[t + 30] / closes[t] - 1.0)) < 1e-12


def test_multiclass_symmetric_classification():
    # +0.5% up, -0.5% down, flat -> LONG/SHORT/NO_TRADE at threshold 0.0015
    assert classify_multiclass(0.005, 0.0015, 0.0015) == "LONG"
    assert classify_multiclass(-0.005, 0.0015, 0.0015) == "SHORT"
    assert classify_multiclass(0.0005, 0.0015, 0.0015) == "NO_TRADE"
    assert _isnan(float("nan")) and classify_multiclass(float("nan"), 0.0015, 0.0015) == "NO_TRADE"


# --- B. asymmetric thresholds -----------------------------------------------

def test_asymmetric_thresholds_applied_separately():
    # long_thr=0.0015, short_thr=0.0020: a -0.0018 move is NOT SHORT (needs < -0.0020)
    assert classify_multiclass(-0.0018, 0.0015, 0.0020) == "NO_TRADE"
    assert classify_multiclass(-0.0025, 0.0015, 0.0020) == "SHORT"
    assert classify_multiclass(0.0016, 0.0015, 0.0020) == "LONG"
    meta = variant_label_threshold_meta(MULTICLASS, 15, 0.0015, 0.0020)
    assert meta["long_threshold"] == 0.0015 and meta["short_threshold"] == 0.0020


def test_asymmetric_in_build_labels():
    closes = [100.0, 100.16, 99.82, 99.75, 100.0]
    lab = build_labels_variant(_series(closes), task=MULTICLASS, horizon=1,
                               long_threshold=0.0015, short_threshold=0.0020)
    # 1-bar returns: +0.0016 LONG ; -0.0034 SHORT (< -0.0020) ; -0.0007 NO_TRADE ; +0.0025 LONG
    assert lab["label_class"][0] == "LONG"
    assert lab["label_class"][1] == "SHORT"
    assert lab["label_class"][2] == "NO_TRADE"      # -0.0007 inside the wider short band
    assert lab["label_class"][3] == "LONG"


# --- C. long-only binary ----------------------------------------------------

def test_binary_long_only_mapping_and_classification():
    assert BINARY_CODES == {"REST": 0, "LONG": 1}
    assert task_codes(LONG_ONLY_BINARY) == {"REST": 0, "LONG": 1}
    assert classify_long_binary(0.005, 0.0015) == "LONG"
    assert classify_long_binary(-0.005, 0.0015) == "REST"     # negative is REST, not SHORT
    assert classify_long_binary(0.0005, 0.0015) == "REST"


def test_binary_build_labels_are_zero_one():
    closes = [100.0, 100.5, 99.0, 100.2, 100.0]
    lab = build_labels_variant(_series(closes), task=LONG_ONLY_BINARY, horizon=1,
                               long_threshold=0.0015)
    valid_codes = [c for c in lab["label_code"] if c is not None]
    assert set(valid_codes) <= {0, 1}
    assert "SHORT" not in lab["label_class"]                  # no SHORT class ever
    assert lab["label_class"][0] == "LONG"                    # +0.5%
    assert lab["label_class"][1] == "REST"                    # big drop -> REST


def test_binary_metadata_records_task_type():
    meta = variant_label_threshold_meta(LONG_ONLY_BINARY, 30, 0.0012, 0.0015)
    assert meta["task_type"] == "long_only_binary"
    assert meta["label_mapping"] == {"REST": 0, "LONG": 1}
    assert meta["short_threshold"] is None                    # not used in binary
    assert meta["horizon"] == 30


# --- distribution / validation ----------------------------------------------

def test_label_distribution_variant():
    closes = [100.0 + (i % 3 - 1) * 0.5 for i in range(10)]
    lab = build_labels_variant(_series(closes), task=MULTICLASS, horizon=1)
    dist = label_distribution_variant(lab["label_class"], MULTICLASS, valid_mask=lab["is_valid_label"])
    assert set(dist) == {"SHORT", "NO_TRADE", "LONG"}
    assert sum(dist.values()) == sum(1 for v in lab["is_valid_label"] if v)


def test_invalid_task_and_params_raise():
    with pytest.raises(ValueError, match="unknown task"):
        build_labels_variant(_series([1, 2, 3]), task="bogus")
    with pytest.raises(ValueError, match="horizon"):
        build_labels_variant(_series([1, 2, 3]), horizon=0)
    with pytest.raises(ValueError, match="thresholds"):
        build_labels_variant(_series([1, 2, 3]), long_threshold=0)


def test_no_future_beyond_horizon_marked_invalid():
    closes = [100.0 + i for i in range(10)]
    lab = build_labels_variant(_series(closes), task=MULTICLASS, horizon=3)
    assert all(lab["is_valid_label"][t] for t in range(7))    # 0..6 have t+3
    assert all(not lab["is_valid_label"][t] for t in range(7, 10))


# --- source scan ------------------------------------------------------------

def test_label_builder_v2_source_clean():
    import research.label_builder_v2 as mod

    src = inspect.getsource(mod)
    assert "import nautilus_trader" not in src and "from nautilus_trader" not in src
    for banned in ("import sklearn", "import lightgbm", "import torch", "import tensorflow"):
        assert banned not in src, banned
    for net in ("import websocket", "import aiohttp", "import requests", "import socket"):
        assert net not in src
