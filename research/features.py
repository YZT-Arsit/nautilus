"""Point-in-time ML V1 feature functions (pure-Python, stdlib only).

These compute the 20 V1 features for ML signal research. They are deliberately
**not** ``feature_engine`` specs: ``feature_engine`` cannot express several of
them (rolling realized vol, ATR%, MA distance, trend slope, donchian-low
distance, range%, time-of-day). Putting them here keeps ``feature_engine``
untouched and makes this module the **single source of truth** shared by the
offline dataset builder and (later) the live ``ml_score_strategy`` - so
train/inference parity is guaranteed by shared code, not by matching two spec
lists.

Strict point-in-time rule
-------------------------
Every feature at index ``t`` uses only bars with index ``<= t``. Rolling windows
**include the current bar t** (allowed: the signal is generated after bar t
closes, and execution happens on bar t+1 via next_bar). Warmup rows (window not
yet full) produce ``NaN`` (``float('nan')``); this module never forward-fills and
never reads future bars. Labels (future returns) live in ``label_builder``, never
here.

Input accepts a pandas ``DataFrame``, a dict-of-lists, or a list-of-dicts - all
normalized to columnar lists internally (no pandas required).
"""
from __future__ import annotations

import math
from typing import Any

# Canonical ordered feature names (all f_-prefixed). The dataset builder and the
# future ml_score_strategy both import this list so the columns never drift.
FEATURE_COLUMNS: list[str] = [
    "f_return_1m",
    "f_return_3m",
    "f_return_5m",
    "f_return_15m",
    "f_return_30m",
    "f_rolling_vol_30",
    "f_rolling_vol_60",
    "f_atr_pct_30",
    "f_ma_distance_30",
    "f_ma_distance_120",
    "f_trend_slope_30",
    "f_trend_slope_120",
    "f_donchian_high_dist_60",
    "f_donchian_low_dist_60",
    "f_bollinger_z_60",
    "f_volume_z_60",
    "f_high_low_range_pct",
    "f_candle_body_pct",
    "f_time_of_day_sin",
    "f_time_of_day_cos",
]

_NAN = float("nan")
_REQUIRED_FIELDS = ("event_time_ns", "open", "high", "low", "close", "volume")
_MINUTE_NS = 60_000_000_000
_BARS_PER_DAY = 1440


def _is_nan(x: Any) -> bool:
    return isinstance(x, float) and math.isnan(x)


def to_columns(table: Any) -> dict[str, list]:
    """Normalize a DataFrame / dict-of-lists / list-of-dicts to dict-of-lists.

    Does not require pandas: a pandas ``DataFrame`` is detected by its
    ``to_dict`` method and converted via ``to_dict('list')``; everything else is
    handled in pure Python. The original input is never mutated.
    """
    # pandas DataFrame (duck-typed; only used when the caller passed one).
    to_dict = getattr(table, "to_dict", None)
    if callable(to_dict) and not isinstance(table, dict):
        cols = table.to_dict("list")
        return {k: list(v) for k, v in cols.items()}
    if isinstance(table, dict):
        return {k: list(v) for k, v in table.items()}
    # list-of-dicts (rows)
    rows = list(table)
    if not rows:
        return {f: [] for f in _REQUIRED_FIELDS}
    keys: list[str] = []
    for r in rows:
        for k in r:
            if k not in keys:
                keys.append(k)
    return {k: [r.get(k) for r in rows] for k in keys}


# --- small pure-Python rolling stats (population, window includes bar t) ----

def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs)


def _pop_std(xs: list[float]) -> float:
    """Population standard deviation (divide by N)."""
    m = _mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / len(xs))


def _zscore(value: float, window: list[float]) -> float:
    """Z-score with an explicit degenerate rule.

    ``(value - mean) / std``. When ``std == 0`` the score is **0.0 iff value
    equals the mean**, otherwise ``NaN`` (an undefined deviation we refuse to
    fabricate). This is the one std==0 convention used across the features.
    """
    m = _mean(window)
    s = _pop_std(window)
    if s == 0.0:
        return 0.0 if value == m else _NAN
    return (value - m) / s


def _slope(ys: list[float]) -> float:
    """Least-squares slope of ``ys`` against x = 0,1,...,n-1 (per-bar units)."""
    n = len(ys)
    xbar = (n - 1) / 2.0
    ybar = _mean(ys)
    num = sum((i - xbar) * (ys[i] - ybar) for i in range(n))
    den = sum((i - xbar) ** 2 for i in range(n))
    return num / den if den != 0.0 else _NAN


# --- per-index feature computation (only indices <= t) ----------------------

def _return_n(close: list[float], t: int, n: int) -> float:
    if t < n or close[t - n] == 0:
        return _NAN
    return close[t] / close[t - n] - 1.0


def _rolling_vol(close: list[float], t: int, w: int) -> float:
    # population std of the last w one-bar returns ending at t (needs close[t-w..t]).
    if t < w:
        return _NAN
    rets = [close[i] / close[i - 1] - 1.0 for i in range(t - w + 1, t + 1)]
    return _pop_std(rets)


def _atr_pct(close: list[float], high: list[float], low: list[float], t: int, w: int) -> float:
    # ATR = mean of true range over w bars, each TR needs a prev close -> t >= w.
    if t < w or close[t] == 0:
        return _NAN
    trs = []
    for i in range(t - w + 1, t + 1):
        pc = close[i - 1]
        trs.append(max(high[i] - low[i], abs(high[i] - pc), abs(low[i] - pc)))
    return _mean(trs) / close[t]


def _ma_distance(close: list[float], t: int, n: int) -> float:
    if t < n - 1:
        return _NAN
    sma = _mean(close[t - n + 1: t + 1])
    return close[t] / sma - 1.0 if sma != 0 else _NAN


def _trend_slope(close: list[float], t: int, n: int) -> float:
    # least-squares slope over the last n closes, normalized by close[t].
    if t < n - 1 or close[t] == 0:
        return _NAN
    s = _slope(close[t - n + 1: t + 1])
    return s / close[t] if not _is_nan(s) else _NAN


def _donchian_high_dist(close: list[float], high: list[float], t: int, w: int) -> float:
    if t < w - 1:
        return _NAN
    hh = max(high[t - w + 1: t + 1])
    return close[t] / hh - 1.0 if hh != 0 else _NAN


def _donchian_low_dist(close: list[float], low: list[float], t: int, w: int) -> float:
    if t < w - 1:
        return _NAN
    ll = min(low[t - w + 1: t + 1])
    return close[t] / ll - 1.0 if ll != 0 else _NAN


def _bollinger_z(close: list[float], t: int, w: int) -> float:
    if t < w - 1:
        return _NAN
    return _zscore(close[t], close[t - w + 1: t + 1])


def _volume_z(volume: list[float], t: int, w: int) -> float:
    if t < w - 1:
        return _NAN
    return _zscore(volume[t], volume[t - w + 1: t + 1])


def _time_of_day(ts_ns: int) -> tuple[float, float]:
    minute_of_day = int((ts_ns // _MINUTE_NS) % _BARS_PER_DAY)
    angle = 2.0 * math.pi * minute_of_day / _BARS_PER_DAY
    return math.sin(angle), math.cos(angle)


def feature_row(cols: dict[str, list], t: int) -> dict[str, float]:
    """Compute all V1 features at index ``t`` using only bars ``<= t``.

    ``cols`` is a dict-of-lists with at least open/high/low/close/volume/
    event_time_ns. Returns a dict of the 20 ``f_*`` values (``NaN`` during
    warmup). Pure: never touches indices ``> t``.
    """
    o, h, l = cols["open"], cols["high"], cols["low"]
    c, v, ts = cols["close"], cols["volume"], cols["event_time_ns"]
    tod_sin, tod_cos = _time_of_day(int(ts[t]))
    body = (c[t] - o[t]) / c[t] if c[t] != 0 else _NAN
    hlr = (h[t] - l[t]) / c[t] if c[t] != 0 else _NAN
    return {
        "f_return_1m": _return_n(c, t, 1),
        "f_return_3m": _return_n(c, t, 3),
        "f_return_5m": _return_n(c, t, 5),
        "f_return_15m": _return_n(c, t, 15),
        "f_return_30m": _return_n(c, t, 30),
        "f_rolling_vol_30": _rolling_vol(c, t, 30),
        "f_rolling_vol_60": _rolling_vol(c, t, 60),
        "f_atr_pct_30": _atr_pct(c, h, l, t, 30),
        "f_ma_distance_30": _ma_distance(c, t, 30),
        "f_ma_distance_120": _ma_distance(c, t, 120),
        "f_trend_slope_30": _trend_slope(c, t, 30),
        "f_trend_slope_120": _trend_slope(c, t, 120),
        "f_donchian_high_dist_60": _donchian_high_dist(c, h, t, 60),
        "f_donchian_low_dist_60": _donchian_low_dist(c, l, t, 60),
        "f_bollinger_z_60": _bollinger_z(c, t, 60),
        "f_volume_z_60": _volume_z(v, t, 60),
        "f_high_low_range_pct": hlr,
        "f_candle_body_pct": body,
        "f_time_of_day_sin": tod_sin,
        "f_time_of_day_cos": tod_cos,
    }


def compute_features(table: Any) -> dict[str, list]:
    """Compute the V1 feature matrix for a whole bar series (columnar output).

    Accepts a pandas DataFrame / dict-of-lists / list-of-dicts; returns a
    dict-of-lists with exactly :data:`FEATURE_COLUMNS` (each a list of length N,
    ``NaN`` during warmup). Assumes rows are already in ascending time order
    (the dataset builder sorts before calling). Pure point-in-time.
    """
    cols = to_columns(table)
    for f in _REQUIRED_FIELDS:
        if f not in cols:
            raise KeyError(f"input table missing required field {f!r}")
    n = len(cols["close"])
    out: dict[str, list] = {name: [] for name in FEATURE_COLUMNS}
    for t in range(n):
        row = feature_row(cols, t)
        for name in FEATURE_COLUMNS:
            out[name].append(row[name])
    return out


# Longest warmup among the V1 features: ma_distance_120 / trend_slope_120 need
# 120 closes (valid at t >= 119). The dataset builder uses this to drop warmup.
MAX_WARMUP_BARS = 120
