"""Tests for research/label_builder.py (pure-Python, no pandas)."""
from __future__ import annotations

import math
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from research.label_builder import (  # noqa: E402
    CODE_TO_LABEL,
    LABEL_CODES,
    build_labels,
    classify_return,
    label_distribution,
    label_threshold,
)


def _isnan(x):
    return isinstance(x, float) and math.isnan(x)


def _cols(close):
    return {
        "open": list(close), "high": list(close), "low": list(close),
        "close": list(close), "volume": [1.0] * len(close),
        "event_time_ns": [i * 60_000_000_000 for i in range(len(close))],
    }


# --- 1 + 8. future_return uses t+H exactly (no off-by-one) ------------------

def test_future_return_uses_t_plus_15_exactly():
    close = [100.0] * 31
    close[15] = 110.0  # only the t+15 bar (from t=0) is marked
    out = build_labels(_cols(close), horizon=15)
    assert abs(out["future_return_15m"][0] - (110.0 / 100.0 - 1.0)) < 1e-12  # read index 15
    assert abs(out["future_return_15m"][1] - 0.0) < 1e-12                    # index 16 == 100


# --- 2/3/4. thresholds ------------------------------------------------------

def test_threshold_value():
    assert abs(label_threshold(0.0005, 0.0005) - 0.0015) < 1e-12


def test_long_short_no_trade_bands():
    thr = label_threshold()
    assert classify_return(0.002, thr) == "LONG"
    assert classify_return(-0.002, thr) == "SHORT"
    assert classify_return(0.001, thr) == "NO_TRADE"
    assert classify_return(0.0015, thr) == "NO_TRADE"   # strictly greater required
    assert classify_return(0.0016, thr) == "LONG"


def test_build_labels_classes_via_horizon_1():
    # horizon=1 lets us set the forward return directly via consecutive closes.
    close = [100.0, 100.2, 99.8, 100.1, 100.0]  # fr: +0.002, -0.004, +0.003..., -0.001
    out = build_labels(_cols(close), horizon=1)
    assert out["label_class"][0] == "LONG"    # +0.2% > 0.15%
    assert out["label_class"][1] == "SHORT"   # -0.4% < -0.15%
    assert out["label_class"][3] == "NO_TRADE"  # -0.1% within band


# --- 5. horizon tail invalid ------------------------------------------------

def test_horizon_tail_marked_invalid():
    close = [100.0 + i for i in range(20)]
    out = build_labels(_cols(close), horizon=15)
    assert out["is_valid_label"][4] is True    # t=4 -> t+15=19 < 20
    assert out["is_valid_label"][5] is False   # t=5 -> t+15=20 out of range
    assert out["label_horizon_ts"][5] is None
    assert _isnan(out["future_return_15m"][5])


# --- 6. label_code mapping stable ------------------------------------------

def test_label_code_mapping_stable():
    assert LABEL_CODES == {"SHORT": 0, "NO_TRADE": 1, "LONG": 2}
    assert CODE_TO_LABEL == {0: "SHORT", 1: "NO_TRADE", 2: "LONG"}
    close = [100.0, 100.2]
    out = build_labels(_cols(close), horizon=1)
    assert out["label_code"][0] == LABEL_CODES[out["label_class"][0]]


# --- horizon_ts correctness + distribution ---------------------------------

def test_label_horizon_ts_points_at_t_plus_h():
    close = [100.0] * 20
    out = build_labels(_cols(close), horizon=15)
    assert out["label_horizon_ts"][0] == 15 * 60_000_000_000  # ts of bar t+15


def test_label_distribution_counts_valid_only():
    close = [100.0, 100.2, 99.8, 100.0]
    out = build_labels(_cols(close), horizon=1)
    dist = label_distribution(out["label_class"], valid_mask=out["is_valid_label"])
    assert dist["LONG"] + dist["SHORT"] + dist["NO_TRADE"] == sum(out["is_valid_label"])
