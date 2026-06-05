"""
Tests for nautilus_ext.features.compute.

Coverage
--------
State containers:
    RollingWindowState  — push, eviction, sum, mean, variance, std, min, max, state_dict
    TimeWindowState     — push, eviction, running sum, state_dict
    EWMAState           — initial value, alpha from span, update formula, state_dict
    VWAPState           — unbounded, count-based rolling, time-based rolling, zero volume

Feature classes:
    RollingMeanFeature  — incremental values match full-window reference; is_ready; trigger
    RollingStdFeature   — incremental std matches pandas reference
    RollingMinFeature   — correctness, is_ready
    RollingMaxFeature   — correctness, is_ready
    VWAPFeature         — session VWAP, rolling window VWAP
    SimpleReturnFeature — formula, first-bar edge case
    LogReturnFeature    — formula, zero/negative guard
    EWMAFeature         — first-bar seed, subsequent updates
    SpreadFeature       — ask - bid
    MidPriceFeature     — (ask + bid) / 2
    BookImbalanceFeature — list[tuple] and scalar attribute paths

SpecFeatureEngine:
    - routing by event input_type (bar vs quote events are handled separately)
    - warmup pre-heats state
    - on_event returns FeatureSnapshot with correct ts_event / instrument_id
    - get() and is_ready() per-feature
    - state_dict / load_state_dict round-trip
    - reset clears all state

TriggerPolicy:
    - on_event: triggers every event
    - on_n_bars: triggers only on every Nth bar
    - on_timer: triggers only when interval_ms has elapsed

WarmupRequirement / backend readiness:
    - is_ready False during warmup, True after window filled

Backend:
    - PythonBackend dispatches by params["type"]
    - PythonBackend dispatches by name prefix
    - BackendRegistry raises on unknown backend name
    - Same spec, different backend → same FeatureBase interface

No full-history recomputation:
    - After warmup the running sum buffer has exactly window entries
    - update() call count is O(1) per event regardless of window size
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import pytest

from nautilus_ext.features.compute.backend import (
    BackendRegistry,
    PythonBackend,
    build_default_registry,
)
from nautilus_ext.features.compute.engine import SpecFeatureEngine
from nautilus_ext.features.compute.feature_base import FeatureBase
from nautilus_ext.features.compute.features import (
    BookImbalanceFeature,
    EWMAFeature,
    LogReturnFeature,
    MidPriceFeature,
    RollingMaxFeature,
    RollingMeanFeature,
    RollingMinFeature,
    RollingStdFeature,
    SimpleReturnFeature,
    SpreadFeature,
    VWAPFeature,
)
from nautilus_ext.features.compute.spec import (
    FeatureSnapshot,
    FeatureSpec,
    FeatureValue,
    TriggerPolicy,
    WarmupRequirement,
)
from nautilus_ext.features.compute.state import (
    EWMAState,
    RollingWindowState,
    TimeWindowState,
    VWAPState,
)


# ---------------------------------------------------------------------------
# Minimal event fixtures
# ---------------------------------------------------------------------------

@dataclass
class Bar:
    open: float = 0.0
    high: float = 0.0
    low: float = 0.0
    close: float = 0.0
    volume: float = 1.0
    ts_event: int = 0
    instrument_id: str = "BTC/USDT"
    event_type: str = "bar"


@dataclass
class Quote:
    bid_price: float = 0.0
    ask_price: float = 0.0
    bid_size: float = 1.0
    ask_size: float = 1.0
    ts_event: int = 0
    instrument_id: str = "BTC/USDT"
    event_type: str = "quote_tick"


@dataclass
class OrderBook:
    bids: list = None
    asks: list = None
    ts_event: int = 0
    instrument_id: str = "BTC/USDT"
    event_type: str = "orderbook"


def bars(closes, *, volumes=None, ts_step=1000):
    """Helper: create a list of Bar events from a list of close prices."""
    if volumes is None:
        volumes = [1.0] * len(closes)
    return [
        Bar(close=c, volume=v, ts_event=i * ts_step)
        for i, (c, v) in enumerate(zip(closes, volumes))
    ]


def _rolling_mean_ref(values, window):
    """Pure-Python rolling mean reference."""
    return [
        sum(values[i - window + 1: i + 1]) / window
        for i in range(window - 1, len(values))
    ]


def _rolling_std_ref(values, window):
    """Pure-Python rolling sample std reference."""
    results = []
    for i in range(window - 1, len(values)):
        win = values[i - window + 1: i + 1]
        mean = sum(win) / window
        var = sum((x - mean) ** 2 for x in win) / (window - 1)
        results.append(math.sqrt(max(0.0, var)))
    return results


# ===========================================================================
# RollingWindowState
# ===========================================================================

class TestRollingWindowState:
    def test_initial_state(self):
        s = RollingWindowState(5)
        assert s.count == 0
        assert not s.is_full
        assert s.sum == 0.0
        assert s.mean is None
        assert s.min is None
        assert s.max is None

    def test_push_before_full(self):
        s = RollingWindowState(3)
        s.push(2.0)
        s.push(4.0)
        assert s.count == 2
        assert not s.is_full
        assert s.sum == 6.0
        assert s.mean == pytest.approx(3.0)

    def test_eviction_on_full(self):
        s = RollingWindowState(3)
        for v in [1.0, 2.0, 3.0, 4.0]:
            s.push(v)
        # Window should contain [2, 3, 4]
        assert s.count == 3
        assert s.is_full
        assert s.sum == pytest.approx(9.0)
        assert s.mean == pytest.approx(3.0)

    def test_running_sum_stays_exact(self):
        """sum must equal the exact sum of the window after many evictions."""
        window = 5
        s = RollingWindowState(window)
        data = [float(i) for i in range(20)]
        for v in data:
            s.push(v)
        assert s.sum == pytest.approx(sum(data[-window:]))

    def test_variance_and_std(self):
        s = RollingWindowState(4, track_squares=True)
        for v in [2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0]:
            s.push(v)
        # Window is [5.0, 5.0, 7.0, 9.0] — sample std
        win = [5.0, 5.0, 7.0, 9.0]
        mean = sum(win) / 4
        ref_var = sum((x - mean) ** 2 for x in win) / 3
        assert s.variance == pytest.approx(ref_var, rel=1e-9)
        assert s.std == pytest.approx(math.sqrt(ref_var), rel=1e-9)

    def test_variance_requires_track_squares(self):
        s = RollingWindowState(4, track_squares=False)
        s.push(1.0)
        s.push(2.0)
        assert s.variance is None
        assert s.std is None

    def test_min_max(self):
        s = RollingWindowState(4)
        for v in [3.0, 1.0, 4.0, 1.0, 5.0]:
            s.push(v)
        assert s.min == pytest.approx(1.0)
        assert s.max == pytest.approx(5.0)

    def test_reset(self):
        s = RollingWindowState(3, track_squares=True)
        for v in [1.0, 2.0, 3.0]:
            s.push(v)
        s.reset()
        assert s.count == 0
        assert s.sum == 0.0
        assert s.mean is None

    def test_state_dict_round_trip(self):
        s = RollingWindowState(4, track_squares=True)
        for v in [1.0, 2.0, 3.0, 4.0, 5.0]:
            s.push(v)
        snap = s.state_dict()
        s2 = RollingWindowState(4, track_squares=True)
        s2.load_state_dict(snap)
        assert s2.sum == pytest.approx(s.sum)
        assert s2.mean == pytest.approx(s.mean)
        assert s2.std == pytest.approx(s.std)


# ===========================================================================
# TimeWindowState
# ===========================================================================

class TestTimeWindowState:
    def test_push_and_sum(self):
        s = TimeWindowState(window_ms=5000)
        s.push(1000, 10.0)
        s.push(3000, 20.0)
        assert s.count == 2
        assert s.sum == pytest.approx(30.0)
        assert s.mean == pytest.approx(15.0)

    def test_eviction(self):
        s = TimeWindowState(window_ms=5000)
        s.push(1000, 10.0)
        s.push(3000, 20.0)
        s.push(6001, 30.0)  # evicts ts=1000 (cutoff = 6001 - 5000 = 1001)
        assert s.count == 2
        assert s.sum == pytest.approx(50.0)  # 20 + 30

    def test_running_sum_after_many_evictions(self):
        s = TimeWindowState(window_ms=3000)
        data = [(i * 1000, float(i)) for i in range(10)]
        for ts, v in data:
            s.push(ts, v)
        # After ts=9000: window is [7000,8000,9000] → values [7,8,9]
        assert s.sum == pytest.approx(7 + 8 + 9)

    def test_timestamps(self):
        s = TimeWindowState(window_ms=10_000)
        s.push(1000, 1.0)
        s.push(5000, 2.0)
        assert s.timestamps == [1000, 5000]

    def test_reset(self):
        s = TimeWindowState(window_ms=5000)
        s.push(1000, 10.0)
        s.reset()
        assert s.count == 0
        assert s.sum == 0.0

    def test_state_dict_round_trip(self):
        s = TimeWindowState(window_ms=5000)
        s.push(1000, 10.0)
        s.push(3000, 20.0)
        snap = s.state_dict()
        s2 = TimeWindowState(window_ms=5000)
        s2.load_state_dict(snap)
        assert s2.sum == pytest.approx(s.sum)
        assert s2.timestamps == s.timestamps


# ===========================================================================
# EWMAState
# ===========================================================================

class TestEWMAState:
    def test_alpha_from_span(self):
        s = EWMAState(span=9)
        assert s.alpha == pytest.approx(2.0 / 10)

    def test_initial_value_seeded(self):
        s = EWMAState(span=9)
        s.push(5.0)
        assert s.value == pytest.approx(5.0)

    def test_update_formula(self):
        alpha = 0.5
        s = EWMAState(alpha=alpha)
        s.push(4.0)   # → 4.0
        s.push(8.0)   # → 0.5*8 + 0.5*4 = 6.0
        assert s.value == pytest.approx(6.0)

    def test_count(self):
        s = EWMAState(span=5)
        s.push(1.0)
        s.push(2.0)
        assert s.count == 2

    def test_reset(self):
        s = EWMAState(span=5)
        s.push(10.0)
        s.reset()
        assert s.value is None
        assert s.count == 0

    def test_requires_span_or_alpha(self):
        with pytest.raises(ValueError):
            EWMAState()

    def test_state_dict_round_trip(self):
        s = EWMAState(span=5)
        for v in [1.0, 2.0, 3.0]:
            s.push(v)
        snap = s.state_dict()
        s2 = EWMAState(span=5)
        s2.load_state_dict(snap)
        assert s2.value == pytest.approx(s.value)
        assert s2.count == s.count


# ===========================================================================
# VWAPState
# ===========================================================================

class TestVWAPState:
    def test_session_vwap(self):
        s = VWAPState()
        s.push(100.0, 10.0)  # pv=1000, v=10
        s.push(200.0, 5.0)   # pv=1000, v=5
        # vwap = 2000/15 ≈ 133.33
        assert s.vwap == pytest.approx(2000.0 / 15.0)

    def test_zero_volume(self):
        s = VWAPState()
        assert s.vwap is None

    def test_count_based_rolling(self):
        s = VWAPState(window=2)
        s.push(100.0, 1.0)
        s.push(200.0, 1.0)
        s.push(300.0, 1.0)  # evicts first: window=[200,300]
        assert s.vwap == pytest.approx(250.0)
        assert s.count == 2

    def test_time_based_rolling(self):
        s = VWAPState(window_ms=3000)
        s.push(100.0, 1.0, ts_ms=1000)
        s.push(200.0, 1.0, ts_ms=3000)
        s.push(300.0, 1.0, ts_ms=4001)  # evicts ts=1000
        # window contains ts=3000 (200) and ts=4001 (300)
        assert s.vwap == pytest.approx(250.0)

    def test_reset(self):
        s = VWAPState(window=5)
        s.push(100.0, 1.0)
        s.reset()
        assert s.vwap is None
        assert s.count == 0

    def test_state_dict_round_trip(self):
        s = VWAPState(window=3)
        s.push(100.0, 2.0)
        s.push(150.0, 1.0)
        snap = s.state_dict()
        s2 = VWAPState(window=3)
        s2.load_state_dict(snap)
        assert s2.vwap == pytest.approx(s.vwap)
        assert s2.count == s.count


# ===========================================================================
# RollingMeanFeature — incremental correctness
# ===========================================================================

class TestRollingMeanFeature:
    def test_is_not_ready_during_warmup(self):
        spec = FeatureSpec(name="m5", input_type="bar", input_field="close", window=5)
        f = RollingMeanFeature(spec)
        for bar in bars([1.0, 2.0, 3.0, 4.0]):
            u = f.update(bar)
            assert not u.value.is_ready
        assert not f.is_ready

    def test_is_ready_after_window(self):
        spec = FeatureSpec(name="m5", input_type="bar", input_field="close", window=5)
        f = RollingMeanFeature(spec)
        for bar in bars([1.0, 2.0, 3.0, 4.0, 5.0]):
            f.update(bar)
        assert f.is_ready

    def test_incremental_matches_reference(self):
        """Incremental rolling mean must match the naive full-window computation."""
        closes = [10.0, 11.0, 12.0, 9.0, 13.0, 14.0, 8.0, 15.0, 16.0, 17.0]
        window = 5
        spec = FeatureSpec(name="m", input_type="bar", input_field="close", window=window)
        f = RollingMeanFeature(spec)

        incremental = []
        for bar in bars(closes):
            u = f.update(bar)
            if u.value.is_ready:
                incremental.append(u.value.value)

        reference = _rolling_mean_ref(closes, window)
        assert len(incremental) == len(reference)
        for inc, ref in zip(incremental, reference):
            assert inc == pytest.approx(ref, rel=1e-12)

    def test_running_sum_is_window_only(self):
        """After 200 bars with window=50, internal buffer has exactly 50 entries."""
        spec = FeatureSpec(name="m50", input_type="bar", input_field="close", window=50)
        f = RollingMeanFeature(spec)
        data = [float(i) for i in range(200)]
        for bar in bars(data):
            f.update(bar)
        assert f._state.count == 50
        assert f._state.sum == pytest.approx(sum(data[-50:]))

    def test_on_n_bars_trigger(self):
        """on_n_bars=3 should emit triggered=True only every 3rd bar."""
        spec = FeatureSpec(
            name="m5",
            input_type="bar",
            input_field="close",
            window=5,
            trigger=TriggerPolicy(kind="on_n_bars", n=3),
        )
        f = RollingMeanFeature(spec)
        triggered_at = []
        for i, bar in enumerate(bars([float(x) for x in range(1, 16)])):
            u = f.update(bar)
            if u.triggered:
                triggered_at.append(i + 1)  # 1-indexed
        # triggered at event counts 3, 6, 9, 12, 15
        assert triggered_at == [3, 6, 9, 12, 15]

    def test_state_dict_round_trip(self):
        spec = FeatureSpec(name="m5", input_type="bar", input_field="close", window=5)
        f = RollingMeanFeature(spec)
        for bar in bars([1.0, 2.0, 3.0, 4.0, 5.0, 6.0]):
            f.update(bar)
        snap = f.state_dict()

        f2 = RollingMeanFeature(spec)
        f2.load_state_dict(snap)
        assert f2.is_ready
        assert f2.value.value == pytest.approx(f.value.value)

    def test_reset(self):
        spec = FeatureSpec(name="m5", input_type="bar", input_field="close", window=5)
        f = RollingMeanFeature(spec)
        for bar in bars([1.0, 2.0, 3.0, 4.0, 5.0]):
            f.update(bar)
        f.reset()
        assert not f.is_ready
        assert f._state.count == 0


# ===========================================================================
# RollingStdFeature
# ===========================================================================

class TestRollingStdFeature:
    def test_incremental_matches_reference(self):
        closes = [10.0, 11.0, 12.0, 9.0, 13.0, 14.0, 8.0, 15.0, 16.0, 17.0]
        window = 5
        spec = FeatureSpec(
            name="std5", input_type="bar", input_field="close", window=window,
            params={"type": "rolling_std"},
        )
        f = RollingStdFeature(spec)

        incremental = []
        for bar in bars(closes):
            u = f.update(bar)
            if u.value.is_ready:
                incremental.append(u.value.value)

        reference = _rolling_std_ref(closes, window)
        assert len(incremental) == len(reference)
        for inc, ref in zip(incremental, reference):
            assert inc == pytest.approx(ref, rel=1e-6)

    def test_not_ready_below_window(self):
        spec = FeatureSpec(name="std3", input_type="bar", input_field="close", window=3)
        f = RollingStdFeature(spec)
        for bar in bars([1.0, 2.0]):
            u = f.update(bar)
            assert not u.value.is_ready


# ===========================================================================
# RollingMinFeature / RollingMaxFeature
# ===========================================================================

class TestRollingMinMax:
    def test_rolling_min(self):
        closes = [5.0, 3.0, 4.0, 1.0, 7.0, 6.0]
        window = 3
        spec = FeatureSpec(name="min3", input_type="bar", input_field="low", window=window)
        f = RollingMinFeature(spec)
        results = []
        for bar in [Bar(low=c, ts_event=i * 1000) for i, c in enumerate(closes)]:
            u = f.update(bar)
            if u.value.is_ready:
                results.append(u.value.value)
        # Window min at each position: [3,1,1,1]
        ref = [
            min(closes[i - window + 1: i + 1]) for i in range(window - 1, len(closes))
        ]
        assert results == pytest.approx(ref)

    def test_rolling_max(self):
        closes = [5.0, 3.0, 4.0, 1.0, 7.0, 6.0]
        window = 3
        spec = FeatureSpec(name="max3", input_type="bar", input_field="high", window=window)
        f = RollingMaxFeature(spec)
        results = []
        for bar in [Bar(high=c, ts_event=i * 1000) for i, c in enumerate(closes)]:
            u = f.update(bar)
            if u.value.is_ready:
                results.append(u.value.value)
        ref = [
            max(closes[i - window + 1: i + 1]) for i in range(window - 1, len(closes))
        ]
        assert results == pytest.approx(ref)


# ===========================================================================
# VWAPFeature
# ===========================================================================

class TestVWAPFeature:
    def test_session_vwap(self):
        spec = FeatureSpec(name="vwap", input_type="bar")
        f = VWAPFeature(spec)
        # First bar: close=100, vol=10 → vwap=100
        u = f.update(Bar(close=100.0, volume=10.0, ts_event=0))
        assert u.value.is_ready
        assert u.value.value == pytest.approx(100.0)
        # Second bar: close=200, vol=5 → pv=1000+1000=2000, v=15 → 133.33
        u = f.update(Bar(close=200.0, volume=5.0, ts_event=1000))
        assert u.value.value == pytest.approx(2000.0 / 15.0)

    def test_rolling_count_vwap(self):
        spec = FeatureSpec(name="vwap2", input_type="bar", window=2, window_unit="bars")
        f = VWAPFeature(spec)
        f.update(Bar(close=100.0, volume=1.0, ts_event=0))
        f.update(Bar(close=200.0, volume=1.0, ts_event=1000))
        u = f.update(Bar(close=300.0, volume=1.0, ts_event=2000))
        # Window=[200,300], equal volume → vwap=250
        assert u.value.value == pytest.approx(250.0)

    def test_custom_price_and_volume_fields(self):
        spec = FeatureSpec(
            name="vwap_hl",
            input_type="bar",
            params={"price_field": "high", "volume_field": "volume"},
        )
        f = VWAPFeature(spec)
        u = f.update(Bar(high=110.0, volume=2.0, ts_event=0))
        assert u.value.value == pytest.approx(110.0)


# ===========================================================================
# SimpleReturnFeature
# ===========================================================================

class TestSimpleReturnFeature:
    def test_first_bar_not_ready(self):
        spec = FeatureSpec(name="ret", input_type="bar", input_field="close")
        f = SimpleReturnFeature(spec)
        u = f.update(Bar(close=100.0))
        assert not u.value.is_ready

    def test_second_bar_ready(self):
        spec = FeatureSpec(name="ret", input_type="bar", input_field="close")
        f = SimpleReturnFeature(spec)
        f.update(Bar(close=100.0))
        u = f.update(Bar(close=110.0))
        assert u.value.is_ready
        assert u.value.value == pytest.approx(0.10)

    def test_negative_return(self):
        spec = FeatureSpec(name="ret", input_type="bar", input_field="close")
        f = SimpleReturnFeature(spec)
        f.update(Bar(close=200.0))
        u = f.update(Bar(close=150.0))
        assert u.value.value == pytest.approx(-0.25)

    def test_state_dict_round_trip(self):
        spec = FeatureSpec(name="ret", input_type="bar", input_field="close")
        f = SimpleReturnFeature(spec)
        f.update(Bar(close=100.0))
        f.update(Bar(close=110.0))
        snap = f.state_dict()
        f2 = SimpleReturnFeature(spec)
        f2.load_state_dict(snap)
        u = f2.update(Bar(close=121.0))
        assert u.value.value == pytest.approx(0.10)


# ===========================================================================
# LogReturnFeature
# ===========================================================================

class TestLogReturnFeature:
    def test_log_return(self):
        spec = FeatureSpec(name="logret", input_type="bar", input_field="close")
        f = LogReturnFeature(spec)
        f.update(Bar(close=100.0))
        u = f.update(Bar(close=110.0))
        assert u.value.is_ready
        assert u.value.value == pytest.approx(math.log(110.0 / 100.0))

    def test_zero_price_guard(self):
        spec = FeatureSpec(name="logret", input_type="bar", input_field="close")
        f = LogReturnFeature(spec)
        f.update(Bar(close=0.0))
        u = f.update(Bar(close=100.0))
        # Previous was zero — no valid return
        assert not u.value.is_ready


# ===========================================================================
# EWMAFeature
# ===========================================================================

class TestEWMAFeature:
    def test_first_bar_seeds_value(self):
        spec = FeatureSpec(name="ema5", input_type="bar", input_field="close", window=5)
        f = EWMAFeature(spec)
        u = f.update(Bar(close=10.0))
        assert u.value.is_ready
        assert u.value.value == pytest.approx(10.0)

    def test_update_formula(self):
        spec = FeatureSpec(
            name="ema", input_type="bar", input_field="close",
            params={"alpha": 0.5},
        )
        f = EWMAFeature(spec)
        f.update(Bar(close=4.0))   # → 4.0
        u = f.update(Bar(close=8.0))  # → 0.5*8 + 0.5*4 = 6.0
        assert u.value.value == pytest.approx(6.0)

    def test_state_dict_round_trip(self):
        spec = FeatureSpec(name="ema10", input_type="bar", input_field="close", window=10)
        f = EWMAFeature(spec)
        for bar in bars([1.0, 2.0, 3.0, 4.0, 5.0]):
            f.update(bar)
        snap = f.state_dict()
        f2 = EWMAFeature(spec)
        f2.load_state_dict(snap)
        assert f2.value.value == pytest.approx(f.value.value)


# ===========================================================================
# Quote-input features
# ===========================================================================

class TestSpreadFeature:
    def test_spread(self):
        spec = FeatureSpec(name="spread", input_type="quote")
        f = SpreadFeature(spec)
        u = f.update(Quote(bid_price=99.5, ask_price=100.5))
        assert u.value.is_ready
        assert u.value.value == pytest.approx(1.0)

    def test_bar_event_returns_no_change(self):
        spec = FeatureSpec(name="spread", input_type="quote")
        f = SpreadFeature(spec)
        # A Bar has no bid_price / ask_price
        u = f.update(Bar(close=100.0))
        assert not u.value.is_ready

    def test_state_dict_round_trip(self):
        spec = FeatureSpec(name="spread", input_type="quote")
        f = SpreadFeature(spec)
        f.update(Quote(bid_price=99.0, ask_price=101.0))
        snap = f.state_dict()
        f2 = SpreadFeature(spec)
        f2.load_state_dict(snap)
        assert f2._event_count == 1


class TestMidPriceFeature:
    def test_mid_price(self):
        spec = FeatureSpec(name="mid", input_type="quote")
        f = MidPriceFeature(spec)
        u = f.update(Quote(bid_price=99.0, ask_price=101.0))
        assert u.value.is_ready
        assert u.value.value == pytest.approx(100.0)


# ===========================================================================
# BookImbalanceFeature
# ===========================================================================

class TestBookImbalanceFeature:
    def test_from_order_book_lists(self):
        spec = FeatureSpec(name="imbalance", input_type="book_delta")
        f = BookImbalanceFeature(spec)
        book = OrderBook(
            bids=[(100.0, 3.0), (99.0, 2.0)],  # total bid = 5
            asks=[(101.0, 1.0)],                  # total ask = 1
        )
        u = f.update(book)
        assert u.value.is_ready
        # (5 - 1) / (5 + 1) = 4/6 ≈ 0.6667
        assert u.value.value == pytest.approx(4.0 / 6.0)

    def test_equal_sides(self):
        spec = FeatureSpec(name="imbalance", input_type="book_delta")
        f = BookImbalanceFeature(spec)
        book = OrderBook(bids=[(100.0, 1.0)], asks=[(101.0, 1.0)])
        u = f.update(book)
        assert u.value.value == pytest.approx(0.0)

    def test_all_bid(self):
        spec = FeatureSpec(name="imbalance", input_type="book_delta")
        f = BookImbalanceFeature(spec)
        book = OrderBook(bids=[(100.0, 5.0)], asks=[])
        u = f.update(book)
        assert u.value.value == pytest.approx(1.0)

    def test_empty_book(self):
        spec = FeatureSpec(name="imbalance", input_type="book_delta")
        f = BookImbalanceFeature(spec)
        book = OrderBook(bids=[], asks=[])
        u = f.update(book)
        assert not u.value.is_ready


# ===========================================================================
# TriggerPolicy — on_timer
# ===========================================================================

class TestTimerTrigger:
    def test_on_timer_trigger(self):
        """on_timer should only trigger when interval_ms has elapsed."""
        spec = FeatureSpec(
            name="m3",
            input_type="bar",
            input_field="close",
            window=3,
            trigger=TriggerPolicy(kind="on_timer", interval_ms=3000),
        )
        f = RollingMeanFeature(spec)
        # Events at ts=0, 1000, 2000, 3000, 4000
        # _last_trigger_ts starts at 0; first fire when (ts - 0) >= 3000
        triggered_at = []
        for i in range(5):
            bar = Bar(close=float(i + 1), ts_event=i * 1000)
            u = f.update(bar)
            if u.triggered:
                triggered_at.append(i * 1000)
        # ts=0: (0-0)=0 < 3000 → not triggered
        # ts=3000: (3000-0)=3000 >= 3000 → triggered; _last_trigger_ts=3000
        # ts=4000: (4000-3000)=1000 < 3000 → not triggered
        assert triggered_at == [3000]
        assert 0 not in triggered_at
        assert 1000 not in triggered_at


# ===========================================================================
# SpecFeatureEngine
# ===========================================================================

class TestSpecFeatureEngine:
    def _make_engine(self):
        specs = [
            FeatureSpec(name="mean5", input_type="bar", input_field="close", window=5,
                        params={"type": "rolling_mean"}),
            FeatureSpec(name="std5", input_type="bar", input_field="close", window=5,
                        params={"type": "rolling_std"}),
        ]
        return SpecFeatureEngine(specs=specs)

    def test_snapshot_keys(self):
        engine = self._make_engine()
        snap = engine.on_event(Bar(close=100.0, ts_event=0))
        assert "mean5" in snap.values
        assert "std5" in snap.values

    def test_snapshot_ts_and_instrument_id(self):
        engine = self._make_engine()
        snap = engine.on_event(Bar(close=100.0, ts_event=42000, instrument_id="ETH/USDT"))
        assert snap.ts_event == 42000
        assert snap.instrument_id == "ETH/USDT"

    def test_warmup_pre_heats_features(self):
        engine = self._make_engine()
        history = bars([float(i) for i in range(1, 6)])
        engine.warmup(history)
        assert engine.is_ready("mean5")
        assert engine.is_ready("std5")

    def test_get(self):
        engine = self._make_engine()
        for bar in bars([float(i) for i in range(1, 6)]):
            engine.on_event(bar)
        fv = engine.get("mean5")
        assert fv is not None
        assert fv.is_ready

    def test_is_ready_all(self):
        engine = self._make_engine()
        assert not engine.is_ready()
        for bar in bars([float(i) for i in range(1, 6)]):
            engine.on_event(bar)
        assert engine.is_ready()

    def test_routing_by_input_type(self):
        """Bar features should not update on quote events."""
        bar_spec = FeatureSpec(name="mean3", input_type="bar", input_field="close", window=3,
                               params={"type": "rolling_mean"})
        quote_spec = FeatureSpec(name="spread", input_type="quote")
        engine = SpecFeatureEngine(specs=[bar_spec, quote_spec])

        # Feed 3 bars to make mean3 ready
        for bar in bars([10.0, 20.0, 30.0]):
            engine.on_event(bar)
        snap = engine.on_event(Bar(close=40.0, ts_event=4000))
        assert snap.values["mean3"].is_ready

        # Feed a quote — spread should become ready, mean3 should NOT re-update
        mean_before = engine.get("mean3").value
        engine.on_event(Quote(bid_price=99.0, ask_price=101.0))
        mean_after = engine.get("mean3").value
        assert mean_before == mean_after  # bar feature unchanged by quote event

        spread_fv = engine.get("spread")
        assert spread_fv.is_ready
        assert spread_fv.value == pytest.approx(2.0)

    def test_state_dict_round_trip(self):
        engine = self._make_engine()
        for bar in bars([float(i) for i in range(1, 8)]):
            engine.on_event(bar)
        snap = engine.state_dict()

        engine2 = self._make_engine()
        engine2.load_state_dict(snap)
        # Next event should produce the same result
        bar = Bar(close=10.0, ts_event=8000)
        s1 = engine.on_event(bar)
        s2 = engine2.on_event(bar)
        assert s1.scalar("mean5") == pytest.approx(s2.scalar("mean5"))

    def test_reset(self):
        engine = self._make_engine()
        for bar in bars([float(i) for i in range(1, 6)]):
            engine.on_event(bar)
        assert engine.is_ready()
        engine.reset()
        assert not engine.is_ready()

    def test_feature_snapshot_all_ready(self):
        engine = self._make_engine()
        for bar in bars([float(i) for i in range(1, 6)]):
            engine.on_event(bar)
        snap = engine.on_event(Bar(close=6.0, ts_event=6000))
        assert snap.all_ready()

    def test_ready_values_only_ready(self):
        engine = self._make_engine()
        # Feed only 3 bars (window=5, nothing ready yet)
        for bar in bars([1.0, 2.0, 3.0]):
            snap = engine.on_event(bar)
        assert snap.ready_values() == {}


# ===========================================================================
# Backend — dispatch and registry
# ===========================================================================

class TestBackend:
    def test_python_backend_by_params_type(self):
        spec = FeatureSpec(name="my_mean", input_type="bar", input_field="close",
                           window=3, params={"type": "rolling_mean"})
        b = PythonBackend()
        f = b.create_feature(spec)
        assert isinstance(f, RollingMeanFeature)

    def test_python_backend_by_name_prefix(self):
        spec = FeatureSpec(name="rolling_std_close_5", input_type="bar",
                           input_field="close", window=5)
        b = PythonBackend()
        f = b.create_feature(spec)
        assert isinstance(f, RollingStdFeature)

    def test_python_backend_unknown_type_raises(self):
        spec = FeatureSpec(name="unknown_feature_xyz", input_type="bar")
        b = PythonBackend()
        with pytest.raises(ValueError, match="cannot determine"):
            b.create_feature(spec)

    def test_registry_unknown_backend_raises(self):
        registry = BackendRegistry()
        registry.register("python", PythonBackend())
        spec = FeatureSpec(name="rolling_mean_close_5", input_type="bar",
                           input_field="close", window=5, backend="rust")
        with pytest.raises(ValueError, match="no backend registered"):
            registry.create_feature(spec)

    def test_backend_swappable_same_api(self):
        """Replacing the backend doesn't change the FeatureBase interface."""

        class ConstantBackend:
            """Test backend that always returns a fixed value."""
            class _ConstantFeature:
                def __init__(self, spec): self._spec = spec; self._v = spec.params.get("val", 42.0)
                @property
                def spec(self): return self._spec
                def warmup_required(self): return WarmupRequirement(n_events=0, mandatory=False)
                def reset(self): pass
                def update(self, event):
                    from nautilus_ext.features.compute.spec import FeatureUpdate
                    fv = FeatureValue(name=self._spec.name, value=self._v, is_ready=True)
                    return FeatureUpdate(value=fv, triggered=True)
                @property
                def value(self): return FeatureValue(name=self._spec.name, value=self._v, is_ready=True)
                @property
                def is_ready(self): return True
                def state_dict(self): return {}
                def load_state_dict(self, s): pass
            def create_feature(self, spec): return self._ConstantFeature(spec)

        registry = BackendRegistry()
        registry.register("constant", ConstantBackend())
        spec = FeatureSpec(name="rolling_mean_close_5", input_type="bar",
                           input_field="close", window=5, backend="constant",
                           params={"val": 99.0})
        engine = SpecFeatureEngine(specs=[spec], backend_registry=registry)
        snap = engine.on_event(Bar(close=50.0, ts_event=0))
        # Value comes from ConstantBackend, not PythonBackend
        assert snap.scalar("rolling_mean_close_5") == pytest.approx(99.0)
        # But the FeatureSnapshot interface is identical
        assert isinstance(snap, FeatureSnapshot)

    def test_build_default_registry_has_python(self):
        r = build_default_registry()
        assert "python" in r.available_backends()


# ===========================================================================
# SpecDrivenFeatureEngine — integration with FeaturePipeline
# ===========================================================================

class TestSpecDrivenFeatureEngine:
    def test_schema_construction(self):
        from nautilus_ext.features.compute.engine import SpecDrivenFeatureEngine
        specs = [
            FeatureSpec(name="mean5", input_type="bar", input_field="close", window=5,
                        params={"type": "rolling_mean"}),
        ]
        eng = SpecDrivenFeatureEngine(specs=specs, feature_set_id="my_features_v1")
        schema = eng.schema
        assert schema.feature_set_id == "my_features_v1"
        assert any(f.name == "mean5" for f in schema.output_features)

    def test_update_returns_none_before_ready(self):
        from nautilus_ext.features.compute.engine import SpecDrivenFeatureEngine
        specs = [FeatureSpec(name="mean5", input_type="bar", input_field="close", window=5,
                             params={"type": "rolling_mean"})]
        eng = SpecDrivenFeatureEngine(specs=specs, feature_set_id="test_v1")
        result = eng.update(Bar(close=1.0, ts_event=0))
        assert result is None

    def test_update_returns_feature_event_when_ready(self):
        from nautilus_ext.features.compute.engine import SpecDrivenFeatureEngine
        from nautilus_ext.features.feature_event import FeatureEvent
        specs = [FeatureSpec(name="mean3", input_type="bar", input_field="close", window=3,
                             params={"type": "rolling_mean"})]
        eng = SpecDrivenFeatureEngine(specs=specs, feature_set_id="test_v1")
        for bar in bars([1.0, 2.0]):
            eng.update(bar)
        fe = eng.update(Bar(close=3.0, ts_event=3000, instrument_id="BTC/USDT"))
        assert isinstance(fe, FeatureEvent)
        assert fe.feature_set_id == "test_v1"
        assert "mean3" in fe.values
        assert fe.values["mean3"] == pytest.approx(2.0)

    def test_integration_with_feature_pipeline(self):
        """SpecDrivenFeatureEngine plugs into FeaturePipeline without errors."""
        from nautilus_ext.features.compute.engine import SpecDrivenFeatureEngine
        from nautilus_ext.features.feature_pipeline import FeaturePipeline

        specs = [FeatureSpec(name="mean3", input_type="bar", input_field="close", window=3,
                             params={"type": "rolling_mean"})]
        eng = SpecDrivenFeatureEngine(specs=specs, feature_set_id="pipeline_test_v1")
        pipeline = FeaturePipeline(feature_engines=[eng])

        pipeline.warmup(bars([1.0, 2.0, 3.0]))
        fes = pipeline.update(Bar(close=4.0, ts_event=4000, instrument_id="BTC/USDT"))
        assert len(fes) == 1
        assert fes[0].values["mean3"] == pytest.approx(3.0)

    def test_warmup_events_tagged(self):
        """FeaturePipeline tags warmup events with is_warmup=True."""
        from nautilus_ext.features.compute.engine import SpecDrivenFeatureEngine
        from nautilus_ext.features.feature_pipeline import FeaturePipeline

        specs = [FeatureSpec(name="mean3", input_type="bar", input_field="close", window=3,
                             params={"type": "rolling_mean"})]
        eng = SpecDrivenFeatureEngine(specs=specs, feature_set_id="warmup_test_v1")
        pipeline = FeaturePipeline(feature_engines=[eng])
        warmup_fes = pipeline.warmup(bars([1.0, 2.0, 3.0]))
        for fe in warmup_fes:
            assert fe.is_warmup


# ===========================================================================
# FeatureBase protocol check
# ===========================================================================

class TestFeatureBaseProtocol:
    def test_concrete_features_satisfy_protocol(self):
        for cls, kwargs in [
            (RollingMeanFeature, {"input_field": "close", "window": 5}),
            (RollingStdFeature, {"input_field": "close", "window": 5}),
            (VWAPFeature, {}),
            (SimpleReturnFeature, {}),
            (SpreadFeature, {}),
            (EWMAFeature, {}),
        ]:
            spec = FeatureSpec(name="test", **kwargs)
            f = cls(spec)
            assert isinstance(f, FeatureBase), f"{cls.__name__} does not satisfy FeatureBase"
