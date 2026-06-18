"""Tests for research/features_v2.py (pure-Python, no pandas)."""
from __future__ import annotations

import inspect
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from research.features import _zscore  # noqa: E402
from research.features_v2 import (  # noqa: E402
    FEATURE_COLUMNS_V2,
    MAX_WARMUP_BARS_V2,
    compute_features_v2,
    feature_row_v2,
)

_START = int(datetime(2024, 7, 1, tzinfo=timezone.utc).timestamp()) * 1_000_000_000


def _isnan(x):
    return isinstance(x, float) and math.isnan(x)


def _bars(n=160, seed_offset=0.0):
    close = [100.0 + 5.0 * math.sin(i / 7.0) + 0.01 * i + seed_offset for i in range(n)]
    vol = [100.0 + 10.0 * math.sin(i / 5.0) + 0.1 * i for i in range(n)]
    # taker-buy fraction wiggles around 0.5
    frac = [0.5 + 0.2 * math.sin(i / 3.0) for i in range(n)]
    return {
        "event_time_ns": [_START + i * 60_000_000_000 for i in range(n)],
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


# --- 1/2. feature columns prefix + stable list ------------------------------

def test_all_columns_f2_prefixed_and_stable():
    assert all(name.startswith("f2_") for name in FEATURE_COLUMNS_V2)
    assert len(FEATURE_COLUMNS_V2) == len(set(FEATURE_COLUMNS_V2)) == 46
    assert MAX_WARMUP_BARS_V2 == 120
    cols = _bars()
    assert set(feature_row_v2(cols, 140)) == set(FEATURE_COLUMNS_V2)


# --- 3. no future leakage ---------------------------------------------------

def test_no_future_leakage():
    cols = _bars()
    before = feature_row_v2(cols, 125)
    mutated = {k: list(v) for k, v in cols.items()}
    for key in ("close", "high", "low", "open", "volume", "quote_volume",
                "trade_count", "taker_buy_volume", "taker_buy_quote_volume"):
        mutated[key][130] = mutated[key][130] * 2 + 1   # change a FUTURE bar
    after = feature_row_v2(mutated, 125)
    for name in FEATURE_COLUMNS_V2:
        a, b = before[name], after[name]
        assert (a == b) or (_isnan(a) and _isnan(b)), name


# --- 4-8. order-flow formulas -----------------------------------------------

def test_taker_ratio_and_imbalance_formula():
    cols = _bars()
    t = 140
    f = feature_row_v2(cols, t)
    ratio = cols["taker_buy_volume"][t] / cols["volume"][t]
    assert f["f2_taker_buy_volume_ratio"] == ratio
    assert f["f2_taker_imbalance"] == 2.0 * ratio - 1.0
    qratio = cols["taker_buy_quote_volume"][t] / cols["quote_volume"][t]
    assert f["f2_taker_buy_quote_ratio"] == qratio
    assert f["f2_taker_quote_imbalance"] == 2.0 * qratio - 1.0


def test_signed_quote_volume_and_avg_trade_size():
    cols = _bars()
    t = 140
    f = feature_row_v2(cols, t)
    qimb = 2.0 * (cols["taker_buy_quote_volume"][t] / cols["quote_volume"][t]) - 1.0
    assert f["f2_signed_quote_volume"] == cols["quote_volume"][t] * qimb
    assert f["f2_avg_trade_size"] == cols["volume"][t] / cols["trade_count"][t]


def test_trade_count_z_formula():
    cols = _bars()
    t = 140
    f = feature_row_v2(cols, t)
    assert f["f2_trade_count_z_30"] == _zscore(cols["trade_count"][t], cols["trade_count"][t - 29:t + 1])


# --- 9-13. directional formulas ---------------------------------------------

def test_clv_formula():
    cols = _bars()
    t = 100
    o, h, l, c = (cols[k][t] for k in ("open", "high", "low", "close"))
    f = feature_row_v2(cols, t)
    expected = ((c - l) - (h - c)) / ((h - l) + 1e-12)
    assert abs(f["f2_clv"] - expected) < 1e-9
    assert -1.0001 <= f["f2_clv"] <= 1.0001


def test_semivariance_formula():
    cols = _bars()
    t = 100
    rets = [cols["close"][i] / cols["close"][i - 1] - 1.0 for i in range(t - 29, t + 1)]
    up = math.sqrt(sum((r if r > 0 else 0.0) ** 2 for r in rets) / 30)
    dn = math.sqrt(sum((r if r < 0 else 0.0) ** 2 for r in rets) / 30)
    f = feature_row_v2(cols, t)
    assert abs(f["f2_upside_vol_30"] - up) < 1e-12
    assert abs(f["f2_downside_vol_30"] - dn) < 1e-12
    assert abs(f["f2_signed_vol_ratio_30"] - (up - dn) / (up + dn + 1e-12)) < 1e-9


def test_trend_consistency_and_consecutive_bars():
    # strictly increasing closes -> consistency 1.0, consecutive_up capped at 30/30
    n = 60
    cols = _bars(n)
    cols["close"] = [100.0 + i for i in range(n)]
    cols["high"] = [c + 0.5 for c in cols["close"]]
    cols["low"] = [c - 0.5 for c in cols["close"]]
    f = feature_row_v2(cols, 59)
    assert abs(f["f2_trend_consistency_30"] - 1.0) < 1e-12
    assert f["f2_consecutive_up_bars"] == 1.0       # 30/30 cap
    assert f["f2_consecutive_down_bars"] == 0.0


def test_orderflow_price_confirm():
    cols = _bars()
    t = 140
    f = feature_row_v2(cols, t)
    r15 = cols["close"][t] / cols["close"][t - 15] - 1.0
    imb = [2.0 * (cols["taker_buy_volume"][j] / cols["volume"][j]) - 1.0 for j in range(t - 14, t + 1)]
    expected = (1.0 if r15 > 0 else (-1.0 if r15 < 0 else 0.0)) * (sum(imb) / 15)
    assert abs(f["f2_orderflow_price_confirm_15"] - expected) < 1e-9


# --- 14. zero division handling ---------------------------------------------

def test_zero_volume_and_trade_count_give_nan():
    cols = _bars()
    t = 140
    cols["volume"][t] = 0.0
    cols["quote_volume"][t] = 0.0
    cols["trade_count"][t] = 0
    f = feature_row_v2(cols, t)
    assert _isnan(f["f2_taker_buy_volume_ratio"])
    assert _isnan(f["f2_taker_imbalance"])
    assert _isnan(f["f2_taker_buy_quote_ratio"])
    assert _isnan(f["f2_signed_quote_volume"])
    assert _isnan(f["f2_avg_trade_size"])


# --- 15/16. warmup NaN vs valid finite --------------------------------------

def test_warmup_nan_then_valid_finite():
    cols = _bars()
    early = feature_row_v2(cols, 0)
    assert _isnan(early["f2_return_1m"]) and _isnan(early["f2_ma_distance_120"])
    assert _isnan(early["f2_vol_regime_60"])
    valid = feature_row_v2(cols, MAX_WARMUP_BARS_V2 - 1)   # index 119
    for name in FEATURE_COLUMNS_V2:
        assert not _isnan(valid[name]), name


def test_compute_features_v2_requires_orderflow_columns():
    cols = _bars()
    del cols["taker_buy_volume"]
    try:
        compute_features_v2(cols)
        assert False, "expected KeyError"
    except KeyError as e:
        assert "taker_buy_volume" in str(e)


def test_compute_features_v2_matrix_shape():
    cols = _bars()
    feats = compute_features_v2(cols)
    assert set(feats) == set(FEATURE_COLUMNS_V2)
    assert all(len(v) == 160 for v in feats.values())


# --- source scan ------------------------------------------------------------

def test_features_v2_source_clean():
    import research.features_v2 as mod

    src = inspect.getsource(mod)
    assert "import nautilus_trader" not in src and "from nautilus_trader" not in src
    for banned in ("import sklearn", "import lightgbm", "import scipy",
                   "import torch", "import tensorflow"):
        assert banned not in src, banned
    for net in ("import websocket", "import aiohttp", "import requests", "import socket"):
        assert net not in src
