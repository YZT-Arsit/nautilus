"""Tests for research/features.py V1 ML features (pure-Python, no pandas)."""
from __future__ import annotations

import math
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from research.features import (  # noqa: E402
    FEATURE_COLUMNS,
    _atr_pct,
    _bollinger_z,
    _donchian_high_dist,
    _donchian_low_dist,
    _ma_distance,
    _pop_std,
    _return_n,
    _rolling_vol,
    _slope,
    _time_of_day,
    _trend_slope,
    _volume_z,
    _zscore,
    compute_features,
    feature_row,
)

_NAN = float("nan")


def _approx(a, b, eps=1e-9):
    return abs(a - b) <= eps


def _isnan(x):
    return isinstance(x, float) and math.isnan(x)


# --- 1. return_n off-by-one -------------------------------------------------

def test_return_n_off_by_one():
    c = [10.0, 11.0, 12.0, 13.0, 14.0, 15.0]
    assert _approx(_return_n(c, 5, 1), 15.0 / 14.0 - 1.0)
    assert _approx(_return_n(c, 5, 3), 15.0 / 12.0 - 1.0)
    assert _isnan(_return_n(c, 2, 3))  # not enough history


# --- 2. rolling_vol uses only past, matches pop std of returns --------------

def test_rolling_vol_matches_pop_std_of_returns():
    c = [10.0, 11.0, 12.0, 13.0]
    rets = [c[i] / c[i - 1] - 1.0 for i in range(2, 4)]  # window w=2 ending at t=3
    assert _approx(_rolling_vol(c, 3, 2), _pop_std(rets))
    assert _isnan(_rolling_vol(c, 1, 2))


# --- 3. ATR percent ---------------------------------------------------------

def test_atr_pct_known_case():
    c = [10.0, 10.0, 12.0]
    h = [10.0, 11.0, 13.0]
    l = [9.0, 9.0, 11.0]
    # TR1 = max(11-9,|11-10|,|9-10|)=2 ; TR2 = max(13-11,|13-10|,|11-10|)=3 ; ATR=2.5
    assert _approx(_atr_pct(c, h, l, 2, 2), 2.5 / 12.0)


# --- 4. MA distance ---------------------------------------------------------

def test_ma_distance_known_case():
    c = [10.0, 20.0, 30.0]
    assert _approx(_ma_distance(c, 2, 3), 30.0 / 20.0 - 1.0)  # SMA=20


# --- 5. trend slope direction ----------------------------------------------

def test_trend_slope_direction_and_value():
    assert _approx(_slope([1.0, 2.0, 3.0, 4.0]), 1.0)
    assert _approx(_slope([4.0, 3.0, 2.0, 1.0]), -1.0)
    up = _trend_slope([1.0, 2.0, 3.0, 4.0], 3, 4)
    down = _trend_slope([4.0, 3.0, 2.0, 1.0], 3, 4)
    assert up > 0 and down < 0


# --- 6. Donchian high/low distance -----------------------------------------

def test_donchian_high_low_distance():
    c = [10.0, 12.0, 11.0]
    h = [10.0, 13.0, 11.0]
    l = [9.0, 8.0, 10.0]
    assert _approx(_donchian_high_dist(c, h, 2, 3), 11.0 / 13.0 - 1.0)  # max high=13
    assert _approx(_donchian_low_dist(c, l, 2, 3), 11.0 / 8.0 - 1.0)    # min low=8


# --- 7. Bollinger z ---------------------------------------------------------

def test_bollinger_z_and_zscore_degenerate_rule():
    c = [1.0, 2.0, 3.0]
    expected = (3.0 - 2.0) / _pop_std([1.0, 2.0, 3.0])
    assert _approx(_bollinger_z(c, 2, 3), expected)
    # std == 0 and value == mean -> 0.0 ; value != mean -> NaN
    assert _zscore(5.0, [5.0, 5.0, 5.0]) == 0.0
    assert _isnan(_zscore(6.0, [5.0, 5.0, 5.0]))


# --- 8. volume z ------------------------------------------------------------

def test_volume_z():
    v = [10.0, 20.0, 30.0]
    expected = (30.0 - 20.0) / _pop_std([10.0, 20.0, 30.0])
    assert _approx(_volume_z(v, 2, 3), expected)


# --- 9. range / body pct ----------------------------------------------------

def test_range_and_body_pct():
    cols = {"open": [100.0], "high": [105.0], "low": [98.0], "close": [102.0],
            "volume": [1.0], "event_time_ns": [0]}
    r = feature_row(cols, 0)
    assert _approx(r["f_high_low_range_pct"], (105.0 - 98.0) / 102.0)
    assert _approx(r["f_candle_body_pct"], (102.0 - 100.0) / 102.0)


# --- 10. time-of-day sin/cos -----------------------------------------------

def test_time_of_day_range_and_midnight():
    s0, c0 = _time_of_day(0)  # minute 0 -> sin 0, cos 1
    assert _approx(s0, 0.0) and _approx(c0, 1.0)
    for ts in (0, 7_200_000_000_000, 43_200_000_000_000, 86_399_000_000_000):
        s, c = _time_of_day(ts)
        assert -1.0 <= s <= 1.0 and -1.0 <= c <= 1.0


# --- 11. all feature columns f_-prefixed -----------------------------------

def test_all_feature_columns_have_f_prefix():
    assert FEATURE_COLUMNS and all(name.startswith("f_") for name in FEATURE_COLUMNS)
    cols = _ramp_cols(5)
    row = feature_row(cols, 4)
    assert set(row.keys()) == set(FEATURE_COLUMNS)
    assert all(k.startswith("f_") for k in row)


# --- 12. no future use ------------------------------------------------------

def _ramp_cols(n):
    close = [100.0 + 5.0 * math.sin(i / 7.0) + 0.01 * i for i in range(n)]
    return {
        "open": [c - 0.1 for c in close],
        "high": [c + 0.5 for c in close],
        "low": [c - 0.5 for c in close],
        "close": close,
        "volume": [100.0 + 10.0 * math.sin(i / 5.0) + 0.1 * i for i in range(n)],
        "event_time_ns": [i * 60_000_000_000 for i in range(n)],
    }


def test_features_do_not_use_future_bars():
    cols = _ramp_cols(140)
    before = feature_row(cols, 125)
    # spike a strictly-future bar (index 130) and recompute the same row.
    cols2 = {k: list(v) for k, v in cols.items()}
    cols2["close"][130] = 9999.0
    cols2["high"][130] = 10000.0
    cols2["low"][130] = 1.0
    after = feature_row(cols2, 125)
    for name in FEATURE_COLUMNS:
        a, b = before[name], after[name]
        assert (a == b) or (_isnan(a) and _isnan(b)), name


def test_compute_features_warmup_then_finite():
    cols = _ramp_cols(140)
    out = compute_features(cols)
    assert set(out.keys()) == set(FEATURE_COLUMNS)
    # early row has NaN (120-window not ready); a late row is fully finite.
    assert any(_isnan(out["f_ma_distance_120"][0:5][i]) for i in range(5))
    assert all(not _isnan(out[name][139]) for name in FEATURE_COLUMNS)
