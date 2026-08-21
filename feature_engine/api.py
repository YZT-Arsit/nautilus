"""Stable public API for feature-strategy authors.

Import the feature engine's user-facing types from **here**, not from the deep
``feature_engine.compute.*`` modules::

    from feature_engine.api import FeatureSpec, FeatureSnapshot

This module is the supported surface. The ``compute`` package underneath is the
low-level implementation (feature operators, backends, watermarks, …) and may
change without notice; this facade will not.

Exposed types
-------------
FeatureSpec
    Declarative description of one feature (name, input, window, params).
FeatureValue
    One feature's value + readiness at a point in time.
FeatureSnapshot
    All feature values for an instrument at one point in time; the object a
    strategy reads via ``snapshot.value(name)`` / ``snapshot.is_ready(name)``.
SpecFeatureEngine
    Spec-driven engine that turns events into snapshots. Most strategy code
    should prefer :class:`feature_engine.runner.FeatureStrategyRunner`,
    which wraps engine construction and the live loop.
rolling_mean_spec
    Convenience builder for a rolling-mean ``FeatureSpec`` (hides ``params``).
"""
from feature_engine.builders import (
    adx_spec,
    aroon_spec,
    atr_spec,
    awesome_oscillator_spec,
    avg_trade_size_spec,
    bollinger_percent_b_spec,
    bollinger_width_spec,
    breakout_down_spec,
    breakout_up_spec,
    candle_body_ratio_spec,
    drawdown_from_rolling_high_spec,
    ema_spec,
    cci_spec,
    confirmed_fractal_spec,
    hlc_mean_spec,
    hma_spec,
    large_trade_ratio_spec,
    lower_shadow_ratio_spec,
    macd_spec,
    momentum_n_spec,
    minus_di_spec,
    price_position_spec,
    psar_spec,
    supertrend_spec,
    plus_di_spec,
    quote_volume_spec,
    return_n_spec,
    rsi_spec,
    rolling_mean_spec,
    rolling_range_spec,
    signed_trade_volume_spec,
    trade_count_spec,
    trade_imbalance_spec,
    trade_intensity_spec,
    trade_price_mean_spec,
    trade_quote_volume_sum_spec,
    trade_volume_sum_spec,
    trade_vwap_spec,
    true_range_spec,
    upper_shadow_ratio_spec,
    volatility_ratio_spec,
    volume_ratio_spec,
    volume_zscore_spec,
    vwap_distance_spec,
    zscore_spec,
)
from feature_engine.compute import (
    FeatureSnapshot,
    FeatureSpec,
    FeatureValue,
    SpecFeatureEngine,
)

__all__ = [
    "FeatureSpec",
    "FeatureValue",
    "FeatureSnapshot",
    "SpecFeatureEngine",
    "rolling_mean_spec",
    "hma_spec",
    "cci_spec",
    "hlc_mean_spec",
    "adx_spec",
    "plus_di_spec",
    "minus_di_spec",
    "ema_spec",
    "rsi_spec",
    "awesome_oscillator_spec",
    "aroon_spec",
    "macd_spec",
    "confirmed_fractal_spec",
    "psar_spec",
    "supertrend_spec",
    # OHLCV feature-library builders
    "rolling_range_spec",
    "true_range_spec",
    "candle_body_ratio_spec",
    "upper_shadow_ratio_spec",
    "lower_shadow_ratio_spec",
    "return_n_spec",
    "momentum_n_spec",
    "price_position_spec",
    "drawdown_from_rolling_high_spec",
    "breakout_up_spec",
    "breakout_down_spec",
    "atr_spec",
    "volatility_ratio_spec",
    "bollinger_width_spec",
    "bollinger_percent_b_spec",
    "zscore_spec",
    "volume_zscore_spec",
    "volume_ratio_spec",
    "quote_volume_spec",
    "vwap_distance_spec",
    # trade (tick) feature builders
    "trade_count_spec",
    "trade_volume_sum_spec",
    "trade_quote_volume_sum_spec",
    "avg_trade_size_spec",
    "signed_trade_volume_spec",
    "trade_imbalance_spec",
    "trade_vwap_spec",
    "large_trade_ratio_spec",
    "trade_intensity_spec",
    "trade_price_mean_spec",
]
