"""Deterministic unit tests for the trade feature library
(``feature_engine/compute/feature_lib/trade.py``).

Per feature: exact ready value, warmup/not_ready, missing-field, divide-by-zero.
Plus: PythonBackend exposes the 9 trade types, every builder builds via
BackendRegistry, input_type routing (trade specs only update on trade events),
and no nautilus_trader import in the trade module.

Pure Python / stdlib — no Nautilus, pandas, or network.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from feature_engine import api as feat_api
from feature_engine.compute.backend import PythonBackend, build_default_registry
from feature_engine.compute.feature_base import FeatureBase
from feature_engine.compute.engine import SpecFeatureEngine


def _feature(spec) -> FeatureBase:
    return build_default_registry().create_feature(spec)


def tr(i, price, qty, side, ts_s=None):
    """A minimal trade event (duck-typed)."""
    ts = (ts_s if ts_s is not None else i) * 1_000_000_000
    qq = price * qty
    is_maker = None if side is None else (side == "SELL")
    return SimpleNamespace(
        event_type="trade", instrument_id="X", event_time_ns=ts,
        price=price, quantity=qty, quote_quantity=qq, side=side, is_buyer_maker=is_maker,
    )


def _updates(feature, events):
    return [feature.update(e) for e in events]


# ---------------------------------------------------------------------------
# Count-window aggregates
# ---------------------------------------------------------------------------

def test_trade_volume_sum_warmup_and_value():
    f = _feature(feat_api.trade_volume_sum_spec("v", window=2))
    out = _updates(f, [tr(0, 100, 2, "BUY"), tr(1, 100, 4, "BUY")])
    assert not out[0].value.is_ready          # warmup
    assert out[1].value.value == pytest.approx(6.0)


def test_avg_trade_size():
    f = _feature(feat_api.avg_trade_size_spec("a", window=2))
    out = _updates(f, [tr(0, 100, 2, "BUY"), tr(1, 100, 4, "BUY")])
    assert out[1].value.value == pytest.approx(3.0)


def test_trade_quote_volume_sum():
    f = _feature(feat_api.trade_quote_volume_sum_spec("qv", window=2))
    out = _updates(f, [tr(0, 100, 2, "BUY"), tr(1, 110, 4, "SELL")])
    assert out[1].value.value == pytest.approx(200 + 440)


def test_signed_trade_volume():
    f = _feature(feat_api.signed_trade_volume_spec("s", window=2))
    out = _updates(f, [tr(0, 100, 2, "BUY"), tr(1, 100, 4, "SELL")])
    assert out[1].value.value == pytest.approx(-2.0)   # +2 (buy) - 4 (sell)


def test_trade_imbalance_and_divzero():
    f = _feature(feat_api.trade_imbalance_spec("im", window=2))
    out = _updates(f, [tr(0, 100, 2, "BUY"), tr(1, 100, 4, "SELL")])
    assert out[1].value.value == pytest.approx((2 - 4) / 6)
    # all-zero quantities -> guarded denominator -> 0.0
    g = _feature(feat_api.trade_imbalance_spec("im0", window=2))
    out2 = _updates(g, [tr(0, 100, 0, "BUY"), tr(1, 100, 0, "SELL")])
    assert out2[1].value.value == pytest.approx(0.0)


def test_trade_vwap():
    f = _feature(feat_api.trade_vwap_spec("vw", window=2))
    out = _updates(f, [tr(0, 100, 2, "BUY"), tr(1, 110, 4, "SELL")])
    assert not out[0].value.is_ready
    assert out[1].value.value == pytest.approx((100 * 2 + 110 * 4) / (2 + 4))


def test_large_trade_ratio():
    f = _feature(feat_api.large_trade_ratio_spec("lt", window=2, threshold=3))
    out = _updates(f, [tr(0, 100, 2, "BUY"), tr(1, 100, 4, "BUY")])
    assert out[1].value.value == pytest.approx(0.5)   # 1 of 2 trades >= 3


def test_large_trade_ratio_requires_threshold():
    with pytest.raises(ValueError):
        _feature(feat_api.large_trade_ratio_spec("lt", window=2, threshold=None))


# ---------------------------------------------------------------------------
# Time-window features
# ---------------------------------------------------------------------------

def test_trade_count_time_window():
    f = _feature(feat_api.trade_count_spec("c", window=3, window_unit="seconds"))
    out = _updates(f, [tr(i, 100, 1, "BUY", ts_s=i) for i in range(5)])
    # at t=4s with a 3s window: trades at t=2,3,4 remain -> count 3
    assert out[-1].value.value == pytest.approx(3.0)


def test_trade_intensity_time_window():
    f = _feature(feat_api.trade_intensity_spec("ti", window=3, window_unit="seconds"))
    out = _updates(f, [tr(i, 100, 1, "BUY", ts_s=i) for i in range(5)])
    assert out[-1].value.value == pytest.approx(1.0)   # 3 trades / 3 seconds


# ---------------------------------------------------------------------------
# Missing-field handling
# ---------------------------------------------------------------------------

def test_missing_field_single_field_flags_skip():
    ev = SimpleNamespace(event_type="trade", event_time_ns=0, price=100.0)  # no quantity
    u = _feature(feat_api.trade_volume_sum_spec("v", window=2)).update(ev)
    assert u.value.update_status == "skipped_missing_field"
    assert not u.value.is_ready


def test_missing_side_no_update():
    ev = SimpleNamespace(event_type="trade", event_time_ns=0, price=100.0, quantity=1.0)  # no side
    u = _feature(feat_api.signed_trade_volume_spec("s", window=2)).update(ev)
    assert not u.value.is_ready


# ---------------------------------------------------------------------------
# input_type routing
# ---------------------------------------------------------------------------

def test_trade_specs_route_only_on_trade_events():
    engine = SpecFeatureEngine(specs=[feat_api.trade_volume_sum_spec("tv", window=2)])
    # A bar event must NOT update a trade feature.
    bar = SimpleNamespace(event_type="bar", instrument_id="X", event_time_ns=0,
                          close=1.0, high=1.0, low=1.0, open=1.0, volume=1.0)
    snap = engine.on_event(bar)
    assert snap.value("tv") is None
    # Trade events drive it to ready.
    engine.on_event(tr(1, 100, 2, "BUY"))
    snap2 = engine.on_event(tr(2, 100, 4, "BUY"))
    assert snap2.value("tv") == pytest.approx(6.0)


# ---------------------------------------------------------------------------
# Backend registration / API surface
# ---------------------------------------------------------------------------

_TRADE_TYPES = [
    "trade_count", "trade_volume_sum", "trade_quote_volume_sum", "avg_trade_size",
    "signed_trade_volume", "trade_imbalance", "trade_vwap", "large_trade_ratio",
    "trade_intensity",
]


def test_python_backend_exposes_trade_types():
    available = set(PythonBackend().available_feature_types())
    missing = [t for t in _TRADE_TYPES if t not in available]
    assert not missing, f"missing: {missing}"


def test_every_trade_builder_is_buildable():
    specs = [
        feat_api.trade_count_spec("t1", window=5),
        feat_api.trade_volume_sum_spec("t2", window=5),
        feat_api.trade_quote_volume_sum_spec("t3", window=5),
        feat_api.avg_trade_size_spec("t4", window=5),
        feat_api.signed_trade_volume_spec("t5", window=5),
        feat_api.trade_imbalance_spec("t6", window=5),
        feat_api.trade_vwap_spec("t7", window=5),
        feat_api.large_trade_ratio_spec("t8", window=5, threshold=1.0),
        feat_api.trade_intensity_spec("t9", window=5),
    ]
    registry = build_default_registry()
    for spec in specs:
        feature = registry.create_feature(spec)
        assert isinstance(feature, FeatureBase)
        assert feature.spec.input_type == "trade"
        assert feature.spec.params["type"] in _TRADE_TYPES


def test_state_dict_round_trip_trade_imbalance():
    spec = feat_api.trade_imbalance_spec("imb_rt", window=2)
    a = _feature(spec)
    for e in [tr(0, 100, 2, "BUY"), tr(1, 100, 4, "SELL")]:
        a.update(e)
    b = _feature(spec)
    b.load_state_dict(a.state_dict())
    assert b.is_ready
    assert b.value.value == pytest.approx(a.value.value)


def _assert_value_equal(got, expected):
    if expected is None:
        assert got is None
    else:
        assert got == pytest.approx(expected)


def _assert_round_trip(spec, events, next_event):
    """After load_state_dict: value/is_ready match the source, the feature is
    internally consistent (is_ready == value.is_ready), and a further update on
    both features stays in lockstep (incremental state truly restored)."""
    a = _feature(spec)
    for e in events:
        a.update(e)
    b = _feature(spec)
    b.load_state_dict(a.state_dict())
    # restored value + readiness match the source feature
    _assert_value_equal(b.value.value, a.value.value)
    assert b.value.is_ready == a.value.is_ready
    # internal consistency that the bug violated (was is_ready=True, value=None)
    assert b.is_ready == b.value.is_ready
    # continuing to stream the same next event keeps both in lockstep
    ua = a.update(next_event)
    ub = b.update(next_event)
    assert ub.value.is_ready == ua.value.is_ready
    _assert_value_equal(ub.value.value, ua.value.value)


def test_state_dict_round_trip_trade_vwap():
    # count-window feature: previously is_ready=True but value=None after restore.
    _assert_round_trip(
        feat_api.trade_vwap_spec("vw_rt", window=2),
        [tr(0, 100, 2, "BUY"), tr(1, 110, 4, "SELL")],
        tr(2, 120, 1, "BUY"),
    )


def test_state_dict_round_trip_trade_count():
    # time-window feature: previously is_ready=False and value lost after restore.
    _assert_round_trip(
        feat_api.trade_count_spec("c_rt", window=3, window_unit="seconds"),
        [tr(i, 100, 1, "BUY", ts_s=i) for i in range(3)],
        tr(3, 100, 1, "BUY", ts_s=3),
    )


def test_state_dict_round_trip_trade_intensity():
    _assert_round_trip(
        feat_api.trade_intensity_spec("ti_rt", window=3, window_unit="seconds"),
        [tr(i, 100, 1, "BUY", ts_s=i) for i in range(3)],
        tr(3, 100, 1, "BUY", ts_s=3),
    )


def test_no_nautilus_import_in_trade_module():
    import inspect

    import feature_engine.compute.feature_lib.trade as trade_mod

    src = inspect.getsource(trade_mod)
    assert "import nautilus_trader" not in src
    assert "from nautilus_trader" not in src
