"""Point-in-time ML V2 feature functions (pure-Python, stdlib only).

V2 extends V1 with **directional price-action** and **bar-level order-flow**
features. The order-flow bucket is the key addition: it derives aggressor
pressure from columns already present in the 1m bar parquet
(``taker_buy_volume``, ``taker_buy_quote_volume``, ``trade_count``,
``quote_volume``) - which V1 never used - directly targeting the broken SHORT
side seen in B1/B2. No aggTrades, no order-book depth, no new data.

All ``f2_*`` features are strictly point-in-time (index ``t`` reads only bars
``<= t``); warmup rows return ``NaN`` and the dataset builder drops them. No
forward-fill, no future read. Reuses the V1 pure-Python rolling helpers so the
two feature sets stay numerically consistent. Imports no sklearn/lightgbm and no
nautilus_trader; the V1 module is left untouched (this is additive).
"""
from __future__ import annotations

import math
from typing import Any

from research.features import (
    _atr_pct,
    _donchian_high_dist,
    _donchian_low_dist,
    _is_nan,
    _ma_distance,
    _return_n,
    _rolling_vol,
    _time_of_day,
    _trend_slope,
    _zscore,
    to_columns,
)

_NAN = float("nan")
_EPS = 1e-12
_REQUIRED_FIELDS_V2 = (
    "event_time_ns", "open", "high", "low", "close", "volume",
    "quote_volume", "trade_count", "taker_buy_volume", "taker_buy_quote_volume",
)

# Canonical ordered V2 feature names (all f2_-prefixed). Dropped from V1 as
# low-importance/redundant: bollinger_z_60, volume_z_60, high_low_range_pct,
# candle_body_pct (volume info now flows through the order-flow bucket).
FEATURE_COLUMNS_V2: list[str] = [
    # --- A. retained OHLCV ---
    "f2_return_1m", "f2_return_3m", "f2_return_5m", "f2_return_15m", "f2_return_30m",
    "f2_rolling_vol_30", "f2_rolling_vol_60", "f2_atr_pct_30",
    "f2_ma_distance_30", "f2_ma_distance_120",
    "f2_trend_slope_30", "f2_trend_slope_120",
    "f2_donchian_high_dist_60", "f2_donchian_low_dist_60",
    "f2_time_of_day_sin", "f2_time_of_day_cos",
    # --- B. directional price-action ---
    "f2_return_accel_5_15", "f2_upside_vol_30", "f2_downside_vol_30",
    "f2_signed_vol_ratio_30", "f2_clv",
    "f2_breakout_strength_high_60", "f2_breakout_strength_low_60",
    "f2_trend_consistency_30", "f2_consecutive_up_bars", "f2_consecutive_down_bars",
    "f2_pullback_from_high_60", "f2_rebound_from_low_60",
    # --- C. bar-level order-flow ---
    "f2_taker_buy_volume_ratio", "f2_taker_buy_quote_ratio",
    "f2_taker_imbalance", "f2_taker_quote_imbalance",
    "f2_taker_imbalance_5", "f2_taker_imbalance_15", "f2_taker_imbalance_30",
    "f2_taker_imbalance_momentum_5_30",
    "f2_signed_quote_volume", "f2_signed_quote_volume_z_30",
    "f2_trade_count_z_30", "f2_avg_trade_size", "f2_avg_trade_size_z_30",
    "f2_large_activity_proxy", "f2_orderflow_price_confirm_15",
    # --- D. regime interaction ---
    "f2_vol_regime_60", "f2_trend_flow_confirm_30", "f2_time_vol_interaction",
]

# Longest warmup: ma_distance_120 / trend_slope_120 need 120 closes (valid at
# t>=119); vol_regime_60 (z-score of 60 rolling_vol_60 values) is also valid at
# t>=119. So warmup matches V1.
MAX_WARMUP_BARS_V2 = 120


def _sign(x: float) -> float:
    return 1.0 if x > 0 else (-1.0 if x < 0 else 0.0)


def _zero(x: Any) -> bool:
    return x is None or x == 0


# --- bar-level value helpers (single index j; read nothing future) -----------

def _semivar(close: list, t: int, w: int) -> tuple[float, float]:
    """(upside, downside) realized semivol over the last w one-bar returns."""
    if t < w:
        return _NAN, _NAN
    rets = [close[i] / close[i - 1] - 1.0 for i in range(t - w + 1, t + 1)]
    up = math.sqrt(sum((r if r > 0 else 0.0) ** 2 for r in rets) / w)
    dn = math.sqrt(sum((r if r < 0 else 0.0) ** 2 for r in rets) / w)
    return up, dn


def _clv(o, h, l, c, t: int) -> float:
    rng = (h[t] - l[t])
    return ((c[t] - l[t]) - (h[t] - c[t])) / (rng + _EPS)


def _atr_abs(c, h, l, t: int, w: int) -> float:
    atrp = _atr_pct(c, h, l, t, w)
    return _NAN if _is_nan(atrp) else atrp * c[t]


def _breakout_high(c, h, t: int, w: int, atr_abs: float) -> float:
    if t < w - 1 or _is_nan(atr_abs) or atr_abs == 0:
        return _NAN
    return (c[t] - max(h[t - w + 1: t + 1])) / atr_abs


def _breakout_low(c, l, t: int, w: int, atr_abs: float) -> float:
    if t < w - 1 or _is_nan(atr_abs) or atr_abs == 0:
        return _NAN
    return (min(l[t - w + 1: t + 1]) - c[t]) / atr_abs


def _trend_consistency(c, t: int, w: int) -> float:
    if t < w:
        return _NAN
    s = sum(1 if c[i] > c[i - 1] else (-1 if c[i] < c[i - 1] else 0)
            for i in range(t - w + 1, t + 1))
    return s / w


def _consecutive(c, t: int, *, up: bool, cap: int = 30) -> float:
    cnt, i = 0, t
    while i >= 1 and cnt < cap and ((c[i] > c[i - 1]) if up else (c[i] < c[i - 1])):
        cnt += 1
        i -= 1
    return cnt / cap


def _pullback_high(c, h, t: int, w: int) -> float:
    if t < w - 1:
        return _NAN
    hh = max(h[t - w + 1: t + 1])
    return c[t] / hh - 1.0 if hh != 0 else _NAN


def _rebound_low(c, l, t: int, w: int) -> float:
    if t < w - 1:
        return _NAN
    ll = min(l[t - w + 1: t + 1])
    return c[t] / ll - 1.0 if ll != 0 else _NAN


def _roll_mean(fn, t: int, w: int) -> float:
    if t < w - 1:
        return _NAN
    vals = [fn(j) for j in range(t - w + 1, t + 1)]
    if any(_is_nan(x) for x in vals):
        return _NAN
    return sum(vals) / w


def _roll_z(fn, t: int, w: int) -> float:
    if t < w - 1:
        return _NAN
    vals = [fn(j) for j in range(t - w + 1, t + 1)]
    if any(_is_nan(x) for x in vals):
        return _NAN
    return _zscore(vals[-1], vals)


def feature_row_v2(cols: dict[str, list], t: int) -> dict[str, float]:
    """Compute all V2 features at index ``t`` using only bars ``<= t``."""
    o, h, l = cols["open"], cols["high"], cols["low"]
    c, v, ts = cols["close"], cols["volume"], cols["event_time_ns"]
    qv, tc = cols["quote_volume"], cols["trade_count"]
    tbv, tbqv = cols["taker_buy_volume"], cols["taker_buy_quote_volume"]
    tod_sin, tod_cos = _time_of_day(int(ts[t]))

    def imb(j):                       # taker volume imbalance at bar j
        return _NAN if _zero(v[j]) else 2.0 * (tbv[j] / v[j]) - 1.0

    def qimb(j):                      # taker quote imbalance at bar j
        return _NAN if _zero(qv[j]) else 2.0 * (tbqv[j] / qv[j]) - 1.0

    def sqv(j):                       # signed quote volume at bar j
        x = qimb(j)
        return _NAN if _is_nan(x) else qv[j] * x

    def ats(j):                       # avg trade size at bar j
        return _NAN if _zero(tc[j]) else v[j] / tc[j]

    f: dict[str, float] = {}
    # --- A. retained OHLCV ---
    f["f2_return_1m"] = _return_n(c, t, 1)
    f["f2_return_3m"] = _return_n(c, t, 3)
    f["f2_return_5m"] = _return_n(c, t, 5)
    f["f2_return_15m"] = _return_n(c, t, 15)
    f["f2_return_30m"] = _return_n(c, t, 30)
    f["f2_rolling_vol_30"] = _rolling_vol(c, t, 30)
    f["f2_rolling_vol_60"] = _rolling_vol(c, t, 60)
    f["f2_atr_pct_30"] = _atr_pct(c, h, l, t, 30)
    f["f2_ma_distance_30"] = _ma_distance(c, t, 30)
    f["f2_ma_distance_120"] = _ma_distance(c, t, 120)
    f["f2_trend_slope_30"] = _trend_slope(c, t, 30)
    f["f2_trend_slope_120"] = _trend_slope(c, t, 120)
    f["f2_donchian_high_dist_60"] = _donchian_high_dist(c, h, t, 60)
    f["f2_donchian_low_dist_60"] = _donchian_low_dist(c, l, t, 60)
    f["f2_time_of_day_sin"] = tod_sin
    f["f2_time_of_day_cos"] = tod_cos

    # --- B. directional price-action ---
    r5, r15 = _return_n(c, t, 5), _return_n(c, t, 15)
    f["f2_return_accel_5_15"] = _NAN if (_is_nan(r5) or _is_nan(r15)) else r5 - r15
    up, dn = _semivar(c, t, 30)
    f["f2_upside_vol_30"], f["f2_downside_vol_30"] = up, dn
    f["f2_signed_vol_ratio_30"] = _NAN if (_is_nan(up) or _is_nan(dn)) else (up - dn) / (up + dn + _EPS)
    f["f2_clv"] = _clv(o, h, l, c, t)
    atr_abs = _atr_abs(c, h, l, t, 30)
    f["f2_breakout_strength_high_60"] = _breakout_high(c, h, t, 60, atr_abs)
    f["f2_breakout_strength_low_60"] = _breakout_low(c, l, t, 60, atr_abs)
    f["f2_trend_consistency_30"] = _trend_consistency(c, t, 30)
    f["f2_consecutive_up_bars"] = _consecutive(c, t, up=True)
    f["f2_consecutive_down_bars"] = _consecutive(c, t, up=False)
    f["f2_pullback_from_high_60"] = _pullback_high(c, h, t, 60)
    f["f2_rebound_from_low_60"] = _rebound_low(c, l, t, 60)

    # --- C. bar-level order-flow ---
    f["f2_taker_buy_volume_ratio"] = _NAN if _zero(v[t]) else tbv[t] / v[t]
    f["f2_taker_buy_quote_ratio"] = _NAN if _zero(qv[t]) else tbqv[t] / qv[t]
    f["f2_taker_imbalance"] = imb(t)
    f["f2_taker_quote_imbalance"] = qimb(t)
    f["f2_taker_imbalance_5"] = _roll_mean(imb, t, 5)
    f["f2_taker_imbalance_15"] = _roll_mean(imb, t, 15)
    f["f2_taker_imbalance_30"] = _roll_mean(imb, t, 30)
    i5, i30 = f["f2_taker_imbalance_5"], f["f2_taker_imbalance_30"]
    f["f2_taker_imbalance_momentum_5_30"] = _NAN if (_is_nan(i5) or _is_nan(i30)) else i5 - i30
    f["f2_signed_quote_volume"] = sqv(t)
    f["f2_signed_quote_volume_z_30"] = _roll_z(sqv, t, 30)
    f["f2_trade_count_z_30"] = _zscore(tc[t], tc[t - 29: t + 1]) if t >= 29 else _NAN
    f["f2_avg_trade_size"] = ats(t)
    f["f2_avg_trade_size_z_30"] = _roll_z(ats, t, 30)
    qz = _zscore(qv[t], qv[t - 29: t + 1]) if t >= 29 else _NAN
    tz = f["f2_trade_count_z_30"]
    f["f2_large_activity_proxy"] = _NAN if (_is_nan(qz) or _is_nan(tz)) else qz + tz
    rm15 = _roll_mean(imb, t, 15)
    f["f2_orderflow_price_confirm_15"] = _NAN if (_is_nan(r15) or _is_nan(rm15)) else _sign(r15) * rm15

    # --- D. regime interaction ---
    f["f2_vol_regime_60"] = _roll_z(lambda j: _rolling_vol(c, j, 60), t, 60)
    ts30, fm30 = _trend_slope(c, t, 30), _roll_mean(imb, t, 30)
    f["f2_trend_flow_confirm_30"] = _NAN if (_is_nan(ts30) or _is_nan(fm30)) else _sign(ts30) * fm30
    rv60 = _rolling_vol(c, t, 60)
    f["f2_time_vol_interaction"] = _NAN if _is_nan(rv60) else tod_sin * rv60
    return f


def compute_features_v2(table: Any) -> dict[str, list]:
    """Compute the V2 feature matrix for a whole bar series (columnar output).

    Requires the extended order-flow columns; raises ``KeyError`` if any of
    :data:`_REQUIRED_FIELDS_V2` is missing. Assumes ascending time order.
    """
    cols = to_columns(table)
    for field in _REQUIRED_FIELDS_V2:
        if field not in cols:
            raise KeyError(f"input table missing required field {field!r}")
    n = len(cols["close"])
    out: dict[str, list] = {name: [] for name in FEATURE_COLUMNS_V2}
    for t in range(n):
        row = feature_row_v2(cols, t)
        for name in FEATURE_COLUMNS_V2:
            out[name].append(row[name])
    return out
