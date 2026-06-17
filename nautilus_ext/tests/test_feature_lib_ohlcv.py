"""Deterministic unit tests for the modular pure-Python OHLCV feature library
(``feature_engine/compute/feature_lib/``).

Every new feature is tested for:
  * warmup / not_ready before enough history,
  * exact ready output on a known bar sequence,
  * divide-by-zero protection (degenerate bars),
  * missing-field handling.

Plus:
  * PythonBackend.available_feature_types() exposes every new type,
  * each public builder produces a FeatureSpec the BackendRegistry can build,
  * no nautilus_trader import anywhere in feature_engine.compute (incl. feature_lib).

These run anywhere (pure Python, stdlib only) - no Nautilus, pandas, or network.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from feature_engine import api as feat_api
from feature_engine.compute.backend import (
    PythonBackend,
    build_default_registry,
)
from feature_engine.compute.feature_base import FeatureBase


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

@dataclass
class Bar:
    open: float = 0.0
    high: float = 0.0
    low: float = 0.0
    close: float = 0.0
    volume: float = 1.0
    quote_volume: float | None = None
    ts_event: int = 0
    event_time_ns: int | None = None
    instrument_id: str = "BTC/USDT"
    event_type: str = "bar"


def mk(i, o, h, lo, c, v=1.0, qv=None) -> Bar:
    return Bar(open=o, high=h, low=lo, close=c, volume=v, quote_volume=qv,
              ts_event=i * 1000, event_time_ns=i * 1_000_000_000)


def _feature(spec) -> FeatureBase:
    """Build a feature from a spec through the real BackendRegistry."""
    return build_default_registry().create_feature(spec)


def _vals(feature, events) -> list:
    return [feature.update(e).value.value for e in events]


def _updates(feature, events) -> list:
    return [feature.update(e) for e in events]


# ---------------------------------------------------------------------------
# A. price / bar structure
# ---------------------------------------------------------------------------

def test_rolling_range():
    f = _feature(feat_api.rolling_range_spec("rng"))
    u = f.update(mk(0, 9, 10, 4, 9))
    assert u.value.value == pytest.approx(6.0)
    assert u.value.is_ready


def test_true_range_first_bar_and_prev_close():
    f = _feature(feat_api.true_range_spec("tr"))
    out = _vals(f, [mk(0, 9, 10, 8, 9), mk(1, 9, 11, 9, 10), mk(2, 9, 12, 7, 8)])
    assert out[0] == pytest.approx(2.0)   # first bar: high - low
    assert out[1] == pytest.approx(2.0)   # max(2, |11-9|, |9-9|)
    assert out[2] == pytest.approx(5.0)   # max(5, |12-10|, |7-10|)


def test_candle_body_ratio_and_divzero():
    f = _feature(feat_api.candle_body_ratio_spec("body"))
    assert f.update(mk(0, 10, 13, 9, 12)).value.value == pytest.approx(0.5)
    f2 = _feature(feat_api.candle_body_ratio_spec("body2"))
    assert f2.update(mk(0, 10, 10, 10, 10)).value.value == pytest.approx(0.0)


def test_upper_and_lower_shadow_ratio():
    up = _feature(feat_api.upper_shadow_ratio_spec("up"))
    lo = _feature(feat_api.lower_shadow_ratio_spec("lo"))
    bar = mk(0, 10, 13, 9, 12)
    assert up.update(bar).value.value == pytest.approx(0.25)   # (13-12)/4
    assert lo.update(bar).value.value == pytest.approx(0.25)   # (10-9)/4


# ---------------------------------------------------------------------------
# B. trend / momentum
# ---------------------------------------------------------------------------

def test_return_n_warmup_and_value_and_divzero():
    f = _feature(feat_api.return_n_spec("ret2", window=2))
    bars = [mk(0, 0, 0, 0, 10), mk(1, 0, 0, 0, 11), mk(2, 0, 0, 0, 12)]
    us = _updates(f, bars)
    assert not us[0].value.is_ready and not us[1].value.is_ready  # warmup
    assert us[2].value.value == pytest.approx(0.2)                # 12/10 - 1
    g = _feature(feat_api.return_n_spec("ret2b", window=2))
    us2 = _updates(g, [mk(0, 0, 0, 0, 0), mk(1, 0, 0, 0, 5), mk(2, 0, 0, 0, 10)])
    assert not us2[2].value.is_ready                              # close[-n] == 0


def test_momentum_n():
    f = _feature(feat_api.momentum_n_spec("mom2", window=2))
    out = _updates(f, [mk(0, 0, 0, 0, 10), mk(1, 0, 0, 0, 11), mk(2, 0, 0, 0, 13)])
    assert not out[1].value.is_ready
    assert out[2].value.value == pytest.approx(3.0)              # 13 - 10


def test_price_position_and_divzero():
    f = _feature(feat_api.price_position_spec("pp", window=2))
    out = _updates(f, [mk(0, 0, 10, 5, 8), mk(1, 0, 12, 6, 11)])
    assert not out[0].value.is_ready
    assert out[1].value.value == pytest.approx((11 - 5) / (12 - 5))
    g = _feature(feat_api.price_position_spec("pp2", window=2))
    out2 = _updates(g, [mk(0, 0, 10, 10, 10), mk(1, 0, 10, 10, 10)])
    assert out2[1].value.value == pytest.approx(0.0)


def test_drawdown_from_rolling_high():
    f = _feature(feat_api.drawdown_from_rolling_high_spec("dd", window=2))
    out = _updates(f, [mk(0, 0, 0, 0, 10), mk(1, 0, 0, 0, 12), mk(2, 0, 0, 0, 11)])
    assert out[1].value.value == pytest.approx(0.0)              # 12 / 12 - 1
    assert out[2].value.value == pytest.approx(11 / 12 - 1)


def test_breakout_up_and_down():
    up = _feature(feat_api.breakout_up_spec("bo_up", window=2))
    ups = _updates(up, [mk(0, 0, 10, 0, 9), mk(1, 0, 11, 0, 10),
                        mk(2, 0, 9, 0, 8), mk(3, 0, 13, 0, 12)])
    assert not ups[0].value.is_ready and not ups[1].value.is_ready
    assert ups[2].value.value is False   # close 8 > max(10,11)=11 ? no
    assert ups[3].value.value is True    # close 12 > max(11,9)=11 ? yes

    dn = _feature(feat_api.breakout_down_spec("bo_dn", window=2))
    dns = _updates(dn, [mk(0, 0, 0, 10, 11), mk(1, 0, 0, 9, 10),
                        mk(2, 0, 0, 11, 12), mk(3, 0, 0, 5, 4)])
    assert dns[2].value.value is False   # close 12 < min(10,9)=9 ? no
    assert dns[3].value.value is True    # close 4 < min(9,11)=9 ? yes


# ---------------------------------------------------------------------------
# C. volatility
# ---------------------------------------------------------------------------

def test_atr_simple_moving_average_of_true_range():
    f = _feature(feat_api.atr_spec("atr2", window=2))
    out = _updates(f, [mk(0, 9, 10, 8, 9), mk(1, 9, 11, 9, 10), mk(2, 9, 12, 7, 8)])
    assert not out[0].value.is_ready
    assert out[1].value.value == pytest.approx(2.0)   # mean(2, 2)
    assert out[2].value.value == pytest.approx(3.5)   # mean(2, 5)


def test_volatility_ratio_constant_prices():
    f = _feature(feat_api.volatility_ratio_spec("vr", short_window=2, long_window=3))
    out = _updates(f, [mk(i, 0, 0, 0, 100.0) for i in range(5)])
    assert not out[2].value.is_ready
    assert out[-1].value.is_ready
    assert out[-1].value.value == pytest.approx(0.0)  # 0 / max(0, eps)


def test_bollinger_width_and_percent_b():
    w = _feature(feat_api.bollinger_width_spec("bw", window=2, k=2.0))
    pb = _feature(feat_api.bollinger_percent_b_spec("pb", window=2, k=2.0))
    bars = [mk(0, 0, 0, 0, 10), mk(1, 0, 0, 0, 12)]
    std = math.sqrt(2.0)  # sample std of [10, 12]
    assert _updates(w, bars)[1].value.value == pytest.approx(4.0 * std / 11.0)
    lower = 11.0 - 2.0 * std
    assert _updates(pb, bars)[1].value.value == pytest.approx((12 - lower) / (4.0 * std))


def test_bollinger_divzero_constant_prices():
    w = _feature(feat_api.bollinger_width_spec("bw0", window=2, k=2.0))
    pb = _feature(feat_api.bollinger_percent_b_spec("pb0", window=2, k=2.0))
    bars = [mk(0, 0, 0, 0, 10), mk(1, 0, 0, 0, 10)]
    assert _updates(w, bars)[1].value.value == pytest.approx(0.0)
    assert _updates(pb, bars)[1].value.value == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# D. normalization / volume
# ---------------------------------------------------------------------------

def test_zscore_value_and_divzero():
    f = _feature(feat_api.zscore_spec("z", window=3))
    out = _updates(f, [mk(0, 0, 0, 0, 10), mk(1, 0, 0, 0, 12), mk(2, 0, 0, 0, 14)])
    assert not out[1].value.is_ready
    assert out[2].value.value == pytest.approx(1.0)   # (14-12)/2
    g = _feature(feat_api.zscore_spec("z0", window=3))
    flat = _updates(g, [mk(i, 0, 0, 0, 5.0) for i in range(3)])
    assert flat[2].value.value == pytest.approx(0.0)  # std 0 -> guarded


def test_volume_zscore():
    f = _feature(feat_api.volume_zscore_spec("vz", window=3))
    out = _updates(f, [mk(0, 0, 0, 0, 1, v=10), mk(1, 0, 0, 0, 1, v=20),
                       mk(2, 0, 0, 0, 1, v=30)])
    assert out[2].value.value == pytest.approx(1.0)   # (30-20)/10


def test_volume_ratio_and_divzero():
    f = _feature(feat_api.volume_ratio_spec("vrat", window=2))
    out = _updates(f, [mk(0, 0, 0, 0, 1, v=10), mk(1, 0, 0, 0, 1, v=30)])
    assert out[1].value.value == pytest.approx(1.5)   # 30 / mean(10,30)=20
    g = _feature(feat_api.volume_ratio_spec("vrat0", window=2))
    out2 = _updates(g, [mk(0, 0, 0, 0, 1, v=0), mk(1, 0, 0, 0, 1, v=0)])
    assert out2[1].value.value == pytest.approx(0.0)  # 0 / max(0, eps)


def test_quote_volume_present_and_fallback():
    f = _feature(feat_api.quote_volume_spec("qv"))
    assert f.update(mk(0, 0, 0, 0, 10, v=3, qv=500.0)).value.value == pytest.approx(500.0)
    g = _feature(feat_api.quote_volume_spec("qv2"))
    assert g.update(mk(0, 0, 0, 0, 10, v=3)).value.value == pytest.approx(30.0)  # close*volume


def test_vwap_distance_session_and_divzero():
    f = _feature(feat_api.vwap_distance_spec("vd"))
    out = _updates(f, [mk(0, 0, 0, 0, 10, v=2), mk(1, 0, 0, 0, 20, v=2)])
    assert out[0].value.value == pytest.approx(0.0)          # vwap == close
    assert out[1].value.value == pytest.approx(20 / 15 - 1)  # vwap=(10*2+20*2)/4=15
    g = _feature(feat_api.vwap_distance_spec("vd0"))
    assert not g.update(mk(0, 0, 0, 0, 10, v=0)).value.is_ready  # zero volume


# ---------------------------------------------------------------------------
# Missing-field handling
# ---------------------------------------------------------------------------

def test_missing_field_multi_field_features():
    ev = SimpleNamespace(close=10.0, event_time_ns=0)  # no high/low
    for spec in (
        feat_api.rolling_range_spec("a"),
        feat_api.candle_body_ratio_spec("b"),
        feat_api.atr_spec("c", window=2),
        feat_api.price_position_spec("d", window=2),
    ):
        u = _feature(spec).update(ev)
        assert not u.value.is_ready


def test_missing_field_single_field_features_flag_skip():
    ev = SimpleNamespace(event_time_ns=0)  # no 'close'
    u = _feature(feat_api.zscore_spec("z", window=3)).update(ev)
    assert u.value.update_status == "skipped_missing_field"
    assert not u.value.is_ready


# ---------------------------------------------------------------------------
# Backend registration / API surface
# ---------------------------------------------------------------------------

_NEW_TYPES = [
    "rolling_range", "true_range", "candle_body_ratio", "upper_shadow_ratio",
    "lower_shadow_ratio", "return_n", "momentum_n", "price_position",
    "drawdown_from_rolling_high", "breakout_up", "breakout_down", "atr",
    "volatility_ratio", "bollinger_width", "bollinger_percent_b", "zscore",
    "volume_zscore", "volume_ratio", "quote_volume", "vwap_distance",
]


def test_python_backend_exposes_all_new_types():
    available = set(PythonBackend().available_feature_types())
    missing = [t for t in _NEW_TYPES if t not in available]
    assert not missing, f"missing from PythonBackend: {missing}"


def test_every_builder_spec_is_buildable_by_registry():
    specs = [
        feat_api.rolling_range_spec("f1"),
        feat_api.true_range_spec("f2"),
        feat_api.candle_body_ratio_spec("f3"),
        feat_api.upper_shadow_ratio_spec("f4"),
        feat_api.lower_shadow_ratio_spec("f5"),
        feat_api.return_n_spec("f6", window=3),
        feat_api.momentum_n_spec("f7", window=3),
        feat_api.price_position_spec("f8", window=3),
        feat_api.drawdown_from_rolling_high_spec("f9", window=3),
        feat_api.breakout_up_spec("f10", window=3),
        feat_api.breakout_down_spec("f11", window=3),
        feat_api.atr_spec("f12", window=3),
        feat_api.volatility_ratio_spec("f13", short_window=2, long_window=5),
        feat_api.bollinger_width_spec("f14", window=3),
        feat_api.bollinger_percent_b_spec("f15", window=3),
        feat_api.zscore_spec("f16", window=3),
        feat_api.volume_zscore_spec("f17", window=3),
        feat_api.volume_ratio_spec("f18", window=3),
        feat_api.quote_volume_spec("f19"),
        feat_api.vwap_distance_spec("f20", window=3, window_unit="bars"),
    ]
    registry = build_default_registry()
    for spec in specs:
        feature = registry.create_feature(spec)
        assert isinstance(feature, FeatureBase)
        assert feature.spec.params["type"] in _NEW_TYPES


def test_state_dict_round_trip_atr():
    spec = feat_api.atr_spec("atr_rt", window=2)
    a = _feature(spec)
    for b in [mk(0, 9, 10, 8, 9), mk(1, 9, 11, 9, 10), mk(2, 9, 12, 7, 8)]:
        a.update(b)
    b2 = _feature(spec)
    b2.load_state_dict(a.state_dict())
    assert b2.is_ready
    assert b2.value.value == pytest.approx(a.value.value)


def test_no_nautilus_import_in_feature_library():
    """The compute feature library must never import nautilus_trader.

    Checks for actual import statements (a passing mention in a comment is fine).
    """
    import inspect

    import feature_engine.builders as builders_mod
    import feature_engine.compute.backend as backend_mod
    import feature_engine.compute.feature_lib as lib_pkg
    import feature_engine.compute.feature_lib.base as lib_base
    import feature_engine.compute.feature_lib.normalization as lib_norm
    import feature_engine.compute.feature_lib.price_action as lib_price
    import feature_engine.compute.feature_lib.returns as lib_returns
    import feature_engine.compute.feature_lib.volatility as lib_vol
    import feature_engine.compute.feature_lib.volume as lib_volume
    import feature_engine.compute.features as features_mod
    import feature_engine.compute.state as state_mod

    for mod in (lib_pkg, lib_base, lib_price, lib_returns, lib_vol, lib_volume,
                lib_norm, features_mod, backend_mod, state_mod, builders_mod):
        src = inspect.getsource(mod)
        assert "import nautilus_trader" not in src, mod.__name__
        assert "from nautilus_trader" not in src, mod.__name__
