"""Convenience builders for common :class:`FeatureSpec` shapes.

These let strategy authors declare features without hand-writing the
``params={"type": ...}`` plumbing that the compute layer keys off. Import them
from the public facade::

    from feature_engine.api import rolling_mean_spec
"""
from __future__ import annotations

from feature_engine.compute import FeatureSpec


def rolling_mean_spec(
    name: str,
    *,
    input_type: str = "bar",
    input_field: str = "close",
    window: int,
) -> FeatureSpec:
    """Build a rolling-mean :class:`FeatureSpec`.

    Hides the ``params={"type": "rolling_mean"}`` detail so strategy code reads
    as intent ("a rolling mean of ``input_field`` over ``window``") rather than
    backend wiring.
    """
    return FeatureSpec(
        name,
        input_type=input_type,
        input_field=input_field,
        window=window,
        params={"type": "rolling_mean"},
    )


# ===========================================================================
# OHLCV feature-library builders (pure Python; PythonBackend)
# ===========================================================================
# Each returns a FeatureSpec with the explicit ``params["type"]`` set, so the
# backend dispatches deterministically without relying on name inference.


# --- A. price / bar structure ---------------------------------------------

def rolling_range_spec(name: str, *, input_type: str = "bar") -> FeatureSpec:
    """Intrabar range ``high - low``."""
    return FeatureSpec(name, input_type=input_type, params={"type": "rolling_range"})


def true_range_spec(name: str, *, input_type: str = "bar") -> FeatureSpec:
    """True range ``max(high-low, |high-prev_close|, |low-prev_close|)``."""
    return FeatureSpec(name, input_type=input_type, params={"type": "true_range"})


def candle_body_ratio_spec(name: str, *, input_type: str = "bar") -> FeatureSpec:
    """``|close - open| / max(high - low, eps)``."""
    return FeatureSpec(name, input_type=input_type, params={"type": "candle_body_ratio"})


def upper_shadow_ratio_spec(name: str, *, input_type: str = "bar") -> FeatureSpec:
    """``(high - max(open, close)) / max(high - low, eps)``."""
    return FeatureSpec(name, input_type=input_type, params={"type": "upper_shadow_ratio"})


def lower_shadow_ratio_spec(name: str, *, input_type: str = "bar") -> FeatureSpec:
    """``(min(open, close) - low) / max(high - low, eps)``."""
    return FeatureSpec(name, input_type=input_type, params={"type": "lower_shadow_ratio"})


# --- B. trend / momentum ---------------------------------------------------

def return_n_spec(
    name: str, *, window: int, input_field: str = "close", input_type: str = "bar",
) -> FeatureSpec:
    """N-bar simple return ``close / close[-n] - 1`` (``window`` == n)."""
    return FeatureSpec(
        name, input_type=input_type, input_field=input_field,
        window=window, params={"type": "return_n"},
    )


def momentum_n_spec(
    name: str, *, window: int, input_field: str = "close", input_type: str = "bar",
) -> FeatureSpec:
    """N-bar momentum ``close - close[-n]`` (``window`` == n)."""
    return FeatureSpec(
        name, input_type=input_type, input_field=input_field,
        window=window, params={"type": "momentum_n"},
    )


def price_position_spec(name: str, *, window: int, input_type: str = "bar") -> FeatureSpec:
    """``(close - min(low, n)) / max(max(high, n) - min(low, n), eps)``."""
    return FeatureSpec(
        name, input_type=input_type, window=window, params={"type": "price_position"},
    )


def drawdown_from_rolling_high_spec(
    name: str, *, window: int, input_field: str = "close", input_type: str = "bar",
) -> FeatureSpec:
    """``close / max(close, n) - 1``."""
    return FeatureSpec(
        name, input_type=input_type, input_field=input_field,
        window=window, params={"type": "drawdown_from_rolling_high"},
    )


def breakout_up_spec(name: str, *, window: int, input_type: str = "bar") -> FeatureSpec:
    """``close > previous rolling_max(high, n)`` (bool)."""
    return FeatureSpec(
        name, input_type=input_type, window=window, params={"type": "breakout_up"},
    )


def breakout_down_spec(name: str, *, window: int, input_type: str = "bar") -> FeatureSpec:
    """``close < previous rolling_min(low, n)`` (bool)."""
    return FeatureSpec(
        name, input_type=input_type, window=window, params={"type": "breakout_down"},
    )


# --- C. volatility ---------------------------------------------------------

def atr_spec(name: str, *, window: int, input_type: str = "bar") -> FeatureSpec:
    """Average True Range — rolling mean of true range over ``window`` bars."""
    return FeatureSpec(
        name, input_type=input_type, window=window, params={"type": "atr"},
    )


def volatility_ratio_spec(
    name: str, *, short_window: int, long_window: int,
    input_field: str = "close", input_type: str = "bar",
) -> FeatureSpec:
    """``realized_vol(short) / max(realized_vol(long), eps)``."""
    return FeatureSpec(
        name, input_type=input_type, input_field=input_field,
        params={"type": "volatility_ratio",
                "short_window": short_window, "long_window": long_window},
    )


def bollinger_width_spec(
    name: str, *, window: int, k: float = 2.0,
    input_field: str = "close", input_type: str = "bar",
) -> FeatureSpec:
    """``(upper - lower) / max(middle, eps)`` for k-sigma Bollinger bands."""
    return FeatureSpec(
        name, input_type=input_type, input_field=input_field,
        window=window, params={"type": "bollinger_width", "k": k},
    )


def bollinger_percent_b_spec(
    name: str, *, window: int, k: float = 2.0,
    input_field: str = "close", input_type: str = "bar",
) -> FeatureSpec:
    """``(close - lower) / max(upper - lower, eps)`` for k-sigma Bollinger bands."""
    return FeatureSpec(
        name, input_type=input_type, input_field=input_field,
        window=window, params={"type": "bollinger_percent_b", "k": k},
    )


# --- D. normalization / volume --------------------------------------------

def zscore_spec(
    name: str, *, window: int, input_field: str = "close", input_type: str = "bar",
) -> FeatureSpec:
    """``(x - mean(x, n)) / max(std(x, n), eps)``."""
    return FeatureSpec(
        name, input_type=input_type, input_field=input_field,
        window=window, params={"type": "zscore"},
    )


def volume_zscore_spec(name: str, *, window: int, input_type: str = "bar") -> FeatureSpec:
    """Z-score of volume over ``window`` bars."""
    return FeatureSpec(
        name, input_type=input_type, input_field="volume",
        window=window, params={"type": "volume_zscore"},
    )


def volume_ratio_spec(
    name: str, *, window: int, input_field: str = "volume", input_type: str = "bar",
) -> FeatureSpec:
    """``volume / max(mean(volume, n), eps)``."""
    return FeatureSpec(
        name, input_type=input_type, input_field=input_field,
        window=window, params={"type": "volume_ratio"},
    )


def quote_volume_spec(name: str, *, input_type: str = "bar") -> FeatureSpec:
    """Quote volume: event ``quote_volume`` if present, else ``close * volume``."""
    return FeatureSpec(name, input_type=input_type, params={"type": "quote_volume"})


def vwap_distance_spec(
    name: str, *, window: int | None = None, window_unit: str | None = None,
    price_field: str = "close", volume_field: str = "volume", input_type: str = "bar",
) -> FeatureSpec:
    """``close / max(vwap, eps) - 1`` (session VWAP unless a window is given)."""
    return FeatureSpec(
        name, input_type=input_type, window=window, window_unit=window_unit,
        params={"type": "vwap_distance",
                "price_field": price_field, "volume_field": volume_field},
    )
