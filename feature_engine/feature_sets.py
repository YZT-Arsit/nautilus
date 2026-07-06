"""Curated ``FeatureSpec`` sets for offline ``feature_data`` materialisation.

A *feature set* is a named list of :class:`FeatureSpec` — the exact same spec
objects a strategy would declare in ``build_specs`` — so features computed offline
by :class:`~feature_engine.offline.HistoricalFeatureBuilder` are identical to the
streaming ones (one ``SpecFeatureEngine`` code path, not two).

These sets are the reusable feature matrix persisted under ``feature_data`` for
(a) cross-strategy reuse and (b) downstream model training. Everything here is
**bar-based** (``input_type="bar"``): the 1-minute OHLCV bars in ``market_data``.
Trade-flow features (``feature_engine.builders.trade_*``) need tick input and are
intentionally excluded from the bar feature matrix.

Add a new set by writing a ``def <name>() -> list[FeatureSpec]`` and registering
it in ``FEATURE_SETS``. Keep feature *names* stable once persisted — they become
the parquet column names / model input names.
"""
from __future__ import annotations

from feature_engine.builders import (
    atr_spec,
    bollinger_percent_b_spec,
    bollinger_width_spec,
    breakout_down_spec,
    breakout_up_spec,
    candle_body_ratio_spec,
    drawdown_from_rolling_high_spec,
    lower_shadow_ratio_spec,
    momentum_n_spec,
    price_position_spec,
    return_n_spec,
    rolling_mean_spec,
    rolling_range_spec,
    true_range_spec,
    upper_shadow_ratio_spec,
    volatility_ratio_spec,
    volume_ratio_spec,
    volume_zscore_spec,
    zscore_spec,
)
from feature_engine.compute import FeatureSpec


def technical_v1() -> list[FeatureSpec]:
    """Baseline technical feature matrix (25 bar features) for reuse + training.

    Grouped by family; windows are in bars (1m). Warm-up rows emit ``None``
    (point-in-time safe) and are dropped downstream. Names are the persisted
    parquet columns — keep them stable.
    """
    return [
        # --- trend / momentum ---
        return_n_spec("ret_1", window=1),
        return_n_spec("ret_5", window=5),
        return_n_spec("ret_15", window=15),
        return_n_spec("ret_60", window=60),
        momentum_n_spec("mom_10", window=10),
        momentum_n_spec("mom_30", window=30),
        rolling_mean_spec("sma_20", window=20),
        rolling_mean_spec("sma_60", window=60),
        price_position_spec("price_pos_20", window=20),
        price_position_spec("price_pos_60", window=60),
        drawdown_from_rolling_high_spec("dd_from_high_60", window=60),
        # --- volatility ---
        true_range_spec("true_range"),
        atr_spec("atr_14", window=14),
        volatility_ratio_spec("vol_ratio_10_60", short_window=10, long_window=60),
        bollinger_width_spec("boll_width_20", window=20),
        bollinger_percent_b_spec("boll_pctb_20", window=20),
        rolling_range_spec("bar_range"),
        # --- breakout ---
        breakout_up_spec("brk_up_20", window=20),
        breakout_down_spec("brk_dn_20", window=20),
        # --- normalisation / volume ---
        zscore_spec("z_close_20", window=20),
        volume_zscore_spec("vol_z_20", window=20),
        volume_ratio_spec("vol_ratio_20", window=20),
        # --- candle structure ---
        candle_body_ratio_spec("body_ratio"),
        upper_shadow_ratio_spec("upper_shadow"),
        lower_shadow_ratio_spec("lower_shadow"),
    ]


FEATURE_SETS = {
    "technical_v1": technical_v1,
}


def build_feature_set(name: str) -> list[FeatureSpec]:
    """Return the specs for feature set ``name`` (raises on an unknown name)."""
    if name not in FEATURE_SETS:
        raise KeyError(
            f"unknown feature set {name!r}; available: {sorted(FEATURE_SETS)}"
        )
    return FEATURE_SETS[name]()


__all__ = ["FEATURE_SETS", "build_feature_set", "technical_v1"]
