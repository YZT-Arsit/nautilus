"""
Tests for nautilus_ext.features.compute.

Coverage
--------
State containers:
    RollingWindowState  — push, eviction, sum, mean, variance, std, min, max, state_dict
    TimeWindowState     — push (ns), eviction (ns), running sum, state_dict
    EWMAState           — initial value, alpha from span, update formula, state_dict
    VWAPState           — unbounded, count-based rolling, time-based rolling (ns), zero volume

EventTimestamps / extraction:
    extract_timestamps  — event_time_ns field, ts_event fallback (ms→ns), receive_time fallback
    select_timestamp    — event_time / receive_time / process_time dispatch
    EventTimestamps     — latency_ns, processing_latency_ns

WatermarkTracker:
    watermark_ns        — max_event_time_ns - allowed_lateness_ns
    is_late             — strict comparison to watermark
    is_late_for         — per-call allowed_lateness override
    should_finalize_window
    state_dict round-trip

Feature classes:
    RollingMeanFeature  — incremental vs reference; is_ready; on_n_bars trigger; state_dict
    RollingStdFeature   — incremental std vs reference
    RollingMinFeature / RollingMaxFeature — correctness, is_ready
    VWAPFeature         — session, rolling count, rolling time (ns)
    SimpleReturnFeature — formula, first-bar edge case
    LogReturnFeature    — formula, zero/negative guard
    EWMAFeature         — first-bar seed, alpha formula
    SpreadFeature       — ask - bid; bar event ignored
    MidPriceFeature     — (ask + bid) / 2
    BookImbalanceFeature — list[tuple] and scalar attribute paths

TriggerPolicy (full time semantics):
    on_event / on_bar_close — every event
    on_n_bars               — every N bars
    on_timer (interval_ns)  — elapsed ns since last trigger
    time_semantics          — event_time vs receive_time dispatch
    allowed_lateness_ns / late_event_policy interaction

SpecFeatureEngine:
    routing by event input_type
    warmup pre-heats state; no late-event check during warmup
    on_event returns FeatureSnapshot with ts_event=event_time_ns
    receive_time_ns / process_time_ns in snapshot
    watermark advances on each event
    late event: drop / log_only / update_if_not_finalized
    state_dict / load_state_dict round-trip
    reset clears features and watermark

SpecDrivenFeatureEngine / FeaturePipeline integration:
    schema construction; ts_event in ms in FeatureEvent; warmup tagging

Backend:
    PythonBackend dispatch by params["type"] and name prefix
    BackendRegistry raises on unknown backend
    Backend-swappable API test

No full-history recomputation:
    buffer count == window after 200 bars
"""
from __future__ import annotations

import math
import time
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
from nautilus_ext.features.compute.clock import ManualClock, SystemClock
from nautilus_ext.features.compute.engine import LateEventError
from nautilus_ext.features.compute.timestamps import (
    EventTimestamps,
    TimestampConfig,
    convert_legacy_ts_event_to_ns,
    extract_timestamps,
    select_timestamp,
)
from nautilus_ext.features.compute.watermark import StreamKey, WatermarkTracker


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
    ts_event: int = 0              # milliseconds (legacy)
    instrument_id: str = "BTC/USDT"
    event_type: str = "bar"
    event_time_ns: int | None = None   # nanoseconds
    receive_time_ns: int | None = None  # nanoseconds


@dataclass
class Quote:
    bid_price: float = 0.0
    ask_price: float = 0.0
    bid_size: float = 1.0
    ask_size: float = 1.0
    ts_event: int = 0
    instrument_id: str = "BTC/USDT"
    event_type: str = "quote_tick"
    event_time_ns: int | None = None
    receive_time_ns: int | None = None


@dataclass
class OrderBook:
    bids: list = None
    asks: list = None
    ts_event: int = 0
    instrument_id: str = "BTC/USDT"
    event_type: str = "orderbook"
    event_time_ns: int | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _s(n: int) -> int:
    """n seconds in nanoseconds."""
    return n * 1_000_000_000


def bars(closes, *, volumes=None, ts_step_s=1):
    """Create Bar events. ts_step_s seconds between bars (sets event_time_ns)."""
    if volumes is None:
        volumes = [1.0] * len(closes)
    return [
        Bar(
            close=c,
            volume=v,
            ts_event=i * ts_step_s * 1000,
            event_time_ns=i * _s(ts_step_s),
        )
        for i, (c, v) in enumerate(zip(closes, volumes))
    ]


def _rolling_mean_ref(values, window):
    return [
        sum(values[i - window + 1: i + 1]) / window
        for i in range(window - 1, len(values))
    ]


def _rolling_std_ref(values, window):
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
        assert s.sum == pytest.approx(6.0)
        assert s.mean == pytest.approx(3.0)

    def test_eviction_on_full(self):
        s = RollingWindowState(3)
        for v in [1.0, 2.0, 3.0, 4.0]:
            s.push(v)
        assert s.count == 3
        assert s.is_full
        assert s.sum == pytest.approx(9.0)
        assert s.mean == pytest.approx(3.0)

    def test_running_sum_stays_exact(self):
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
# TimeWindowState — nanosecond timestamps
# ===========================================================================

class TestTimeWindowState:
    def test_push_and_sum(self):
        # 5-second window
        s = TimeWindowState(window_ns=_s(5))
        s.push(_s(1), 10.0)
        s.push(_s(3), 20.0)
        assert s.count == 2
        assert s.sum == pytest.approx(30.0)
        assert s.mean == pytest.approx(15.0)

    def test_eviction(self):
        s = TimeWindowState(window_ns=_s(5))
        s.push(_s(1), 10.0)
        s.push(_s(3), 20.0)
        # cutoff = 6001ms_ns - 5s_ns = 1ms_ns: evicts ts=1s
        s.push(_s(6) + 1, 30.0)
        assert s.count == 2
        assert s.sum == pytest.approx(50.0)   # 20 + 30

    def test_running_sum_after_many_evictions(self):
        s = TimeWindowState(window_ns=_s(3))
        for i in range(10):
            s.push(_s(i), float(i))
        # After ts=9s: window is [7s,8s,9s] → values [7,8,9]
        assert s.sum == pytest.approx(7 + 8 + 9)

    def test_timestamps_ns(self):
        s = TimeWindowState(window_ns=_s(10))
        s.push(_s(1), 1.0)
        s.push(_s(5), 2.0)
        assert s.timestamps_ns == [_s(1), _s(5)]

    def test_oldest_newest(self):
        s = TimeWindowState(window_ns=_s(10))
        s.push(_s(2), 1.0)
        s.push(_s(7), 2.0)
        assert s.oldest_ts_ns == _s(2)
        assert s.newest_ts_ns == _s(7)

    def test_reset(self):
        s = TimeWindowState(window_ns=_s(5))
        s.push(_s(1), 10.0)
        s.reset()
        assert s.count == 0
        assert s.sum == 0.0

    def test_state_dict_round_trip(self):
        s = TimeWindowState(window_ns=_s(5))
        s.push(_s(1), 10.0)
        s.push(_s(3), 20.0)
        snap = s.state_dict()
        s2 = TimeWindowState(window_ns=_s(5))
        s2.load_state_dict(snap)
        assert s2.sum == pytest.approx(s.sum)
        assert s2.timestamps_ns == s.timestamps_ns


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
        s = EWMAState(alpha=0.5)
        s.push(4.0)    # → 4.0
        s.push(8.0)    # → 0.5*8 + 0.5*4 = 6.0
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
# VWAPState — nanosecond timestamps
# ===========================================================================

class TestVWAPState:
    def test_session_vwap(self):
        s = VWAPState()
        s.push(100.0, 10.0)
        s.push(200.0, 5.0)
        assert s.vwap == pytest.approx(2000.0 / 15.0)

    def test_zero_volume(self):
        s = VWAPState()
        assert s.vwap is None

    def test_count_based_rolling(self):
        s = VWAPState(window=2)
        s.push(100.0, 1.0)
        s.push(200.0, 1.0)
        s.push(300.0, 1.0)   # evicts first → [200, 300]
        assert s.vwap == pytest.approx(250.0)
        assert s.count == 2

    def test_time_based_rolling_ns(self):
        s = VWAPState(window_ns=_s(3))
        s.push(100.0, 1.0, ts_ns=_s(1))
        s.push(200.0, 1.0, ts_ns=_s(3))
        s.push(300.0, 1.0, ts_ns=_s(4) + 1)  # evicts ts=1s (cutoff=1s+1ns)
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
# EventTimestamps / extract_timestamps / select_timestamp
# ===========================================================================

class TestEventTimestamps:
    def test_from_event_time_ns_field(self):
        """Uses event_time_ns directly when present."""
        bar = Bar(event_time_ns=5_000_000_000, receive_time_ns=5_001_000_000)
        ts = extract_timestamps(bar)
        assert ts.event_time_ns == 5_000_000_000
        assert ts.receive_time_ns == 5_001_000_000

    def test_fallback_from_ts_event_ms(self):
        """Falls back to ts_event (ms) × 1_000_000 when event_time_ns absent."""
        bar = Bar(ts_event=3000)   # 3000 ms → 3_000_000_000 ns
        ts = extract_timestamps(bar)
        assert ts.event_time_ns == 3_000_000_000

    def test_receive_time_defaults_to_event_time(self):
        """receive_time_ns defaults to event_time_ns when absent."""
        bar = Bar(event_time_ns=7_000_000_000)
        ts = extract_timestamps(bar)
        assert ts.receive_time_ns == ts.event_time_ns

    def test_latency_ns(self):
        ts = EventTimestamps(event_time_ns=1_000_000_000, receive_time_ns=1_002_000_000)
        assert ts.latency_ns == 2_000_000

    def test_processing_latency_ns(self):
        ts = EventTimestamps(
            event_time_ns=1_000_000_000,
            receive_time_ns=1_002_000_000,
            process_time_ns=1_003_000_000,
        )
        assert ts.processing_latency_ns == 1_000_000

    def test_processing_latency_ns_none_when_process_time_absent(self):
        ts = EventTimestamps(event_time_ns=1_000_000_000, receive_time_ns=1_001_000_000)
        assert ts.processing_latency_ns is None

    def test_select_event_time(self):
        ts = EventTimestamps(event_time_ns=1_000, receive_time_ns=2_000, process_time_ns=3_000)
        assert select_timestamp(ts, "event_time") == 1_000

    def test_select_receive_time(self):
        ts = EventTimestamps(event_time_ns=1_000, receive_time_ns=2_000, process_time_ns=3_000)
        assert select_timestamp(ts, "receive_time") == 2_000

    def test_select_process_time(self):
        ts = EventTimestamps(event_time_ns=1_000, receive_time_ns=2_000, process_time_ns=3_000)
        assert select_timestamp(ts, "process_time") == 3_000

    def test_select_process_time_falls_back_when_absent(self):
        ts = EventTimestamps(event_time_ns=1_000, receive_time_ns=2_000, process_time_ns=None)
        assert select_timestamp(ts, "process_time") == 1_000   # falls back to event_time


# ===========================================================================
# WatermarkTracker
# ===========================================================================

class TestWatermarkTracker:
    def test_initial_watermark_zero(self):
        w = WatermarkTracker()
        assert w.watermark_ns == 0
        assert not w.is_initialized

    def test_is_not_late_before_any_events(self):
        w = WatermarkTracker()
        assert not w.is_late(0)
        assert not w.is_late(1_000_000)

    def test_watermark_advances(self):
        w = WatermarkTracker(allowed_lateness_ns=0)
        w.update(_s(1))
        w.update(_s(3))
        assert w.watermark_ns == _s(3)
        assert w.max_event_time_ns == _s(3)

    def test_watermark_monotonic(self):
        """Watermark must not decrease on out-of-order events."""
        w = WatermarkTracker(allowed_lateness_ns=0)
        w.update(_s(5))
        w.update(_s(2))   # earlier event
        assert w.watermark_ns == _s(5)

    def test_allowed_lateness_shifts_watermark(self):
        w = WatermarkTracker(allowed_lateness_ns=_s(1))
        w.update(_s(5))
        assert w.watermark_ns == _s(4)   # 5s - 1s

    def test_is_late_strict_below_watermark(self):
        w = WatermarkTracker(allowed_lateness_ns=0)
        w.update(_s(10))
        assert w.is_late(_s(9))     # strictly before watermark
        assert not w.is_late(_s(10))   # at watermark → on-time
        assert not w.is_late(_s(11))  # after watermark

    def test_is_late_for_per_call_lateness(self):
        w = WatermarkTracker(allowed_lateness_ns=0)
        w.update(_s(10))
        # With 2s lateness tolerance: watermark = 10s-2s = 8s
        assert w.is_late_for(_s(7), allowed_lateness_ns=_s(2))   # 7 < 8
        assert not w.is_late_for(_s(8), allowed_lateness_ns=_s(2))  # 8 == 8

    def test_should_finalize_window(self):
        w = WatermarkTracker(allowed_lateness_ns=0)
        w.update(_s(10))
        assert w.should_finalize_window(window_end_ns=_s(10))
        assert not w.should_finalize_window(window_end_ns=_s(11))

    def test_reset(self):
        w = WatermarkTracker()
        w.update(_s(5))
        w.reset()
        assert not w.is_initialized
        assert w.watermark_ns == 0

    def test_state_dict_round_trip(self):
        w = WatermarkTracker(allowed_lateness_ns=_s(1))
        w.update(_s(5))
        snap = w.state_dict()
        w2 = WatermarkTracker()
        w2.load_state_dict(snap)
        assert w2.watermark_ns == w.watermark_ns
        assert w2.max_event_time_ns == _s(5)


# ===========================================================================
# RollingMeanFeature
# ===========================================================================

class TestRollingMeanFeature:
    def test_is_not_ready_during_warmup(self):
        spec = FeatureSpec(name="m5", input_type="bar", input_field="close", window=5,
                           params={"type": "rolling_mean"})
        f = RollingMeanFeature(spec)
        for bar in bars([1.0, 2.0, 3.0, 4.0]):
            u = f.update(bar)
            assert not u.value.is_ready
        assert not f.is_ready

    def test_is_ready_after_window(self):
        spec = FeatureSpec(name="m5", input_type="bar", input_field="close", window=5,
                           params={"type": "rolling_mean"})
        f = RollingMeanFeature(spec)
        for bar in bars([1.0, 2.0, 3.0, 4.0, 5.0]):
            f.update(bar)
        assert f.is_ready

    def test_incremental_matches_reference(self):
        closes = [10.0, 11.0, 12.0, 9.0, 13.0, 14.0, 8.0, 15.0, 16.0, 17.0]
        window = 5
        spec = FeatureSpec(name="m", input_type="bar", input_field="close", window=window,
                           params={"type": "rolling_mean"})
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
        spec = FeatureSpec(name="m50", input_type="bar", input_field="close", window=50,
                           params={"type": "rolling_mean"})
        f = RollingMeanFeature(spec)
        data = [float(i) for i in range(200)]
        for bar in bars(data):
            f.update(bar)
        assert f._state.count == 50
        assert f._state.sum == pytest.approx(sum(data[-50:]))

    def test_on_n_bars_trigger(self):
        spec = FeatureSpec(
            name="m5", input_type="bar", input_field="close", window=5,
            trigger=TriggerPolicy(kind="on_n_bars", n=3),
            params={"type": "rolling_mean"},
        )
        f = RollingMeanFeature(spec)
        triggered_at = []
        for i, bar in enumerate(bars([float(x) for x in range(1, 16)])):
            u = f.update(bar)
            if u.triggered:
                triggered_at.append(i + 1)
        assert triggered_at == [3, 6, 9, 12, 15]

    def test_on_timer_trigger_ns(self):
        """on_timer with interval_ns=3s fires on the third second bar and beyond."""
        spec = FeatureSpec(
            name="m3", input_type="bar", input_field="close", window=3,
            trigger=TriggerPolicy(kind="on_timer", interval_ns=_s(3)),
            params={"type": "rolling_mean"},
        )
        f = RollingMeanFeature(spec)
        # ts_step_s=1 → event_time_ns = 0, 1e9, 2e9, 3e9, 4e9 ns
        triggered_at = []
        for i in range(5):
            bar = Bar(close=float(i + 1), event_time_ns=_s(i))
            u = f.update(bar)
            if u.triggered:
                triggered_at.append(_s(i))
        # ts=0: (0-0)=0 < 3e9 → not triggered
        # ts=3e9: (3e9-0)=3e9 >= 3e9 → triggered
        assert triggered_at == [_s(3)]

    def test_time_semantics_receive_time(self):
        """When time_semantics=receive_time, trigger uses receive_time_ns."""
        spec = FeatureSpec(
            name="m3", input_type="bar", input_field="close", window=3,
            trigger=TriggerPolicy(
                kind="on_timer",
                interval_ns=_s(3),
                time_semantics="receive_time",
            ),
            params={"type": "rolling_mean"},
        )
        f = RollingMeanFeature(spec)
        # event_time_ns=0 but receive_time_ns=5s → should trigger on first event
        bar = Bar(close=1.0, event_time_ns=0, receive_time_ns=_s(5))
        u = f.update(bar)
        # _last_trigger_ts=0, receive_ts=5e9, (5e9-0)>=3e9 → triggered
        assert u.triggered

    def test_state_dict_round_trip(self):
        spec = FeatureSpec(name="m5", input_type="bar", input_field="close", window=5,
                           params={"type": "rolling_mean"})
        f = RollingMeanFeature(spec)
        for bar in bars([1.0, 2.0, 3.0, 4.0, 5.0, 6.0]):
            f.update(bar)
        snap = f.state_dict()
        f2 = RollingMeanFeature(spec)
        f2.load_state_dict(snap)
        assert f2.is_ready
        assert f2.value.value == pytest.approx(f.value.value)

    def test_reset(self):
        spec = FeatureSpec(name="m5", input_type="bar", input_field="close", window=5,
                           params={"type": "rolling_mean"})
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
        spec = FeatureSpec(name="std5", input_type="bar", input_field="close", window=window,
                           params={"type": "rolling_std"})
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
        spec = FeatureSpec(name="std3", input_type="bar", input_field="close", window=3,
                           params={"type": "rolling_std"})
        f = RollingStdFeature(spec)
        for bar in bars([1.0, 2.0]):
            u = f.update(bar)
            assert not u.value.is_ready


# ===========================================================================
# RollingMinFeature / RollingMaxFeature
# ===========================================================================

class TestRollingMinMax:
    def test_rolling_min(self):
        lows = [5.0, 3.0, 4.0, 1.0, 7.0, 6.0]
        window = 3
        spec = FeatureSpec(name="min3", input_type="bar", input_field="low", window=window,
                           params={"type": "rolling_min"})
        f = RollingMinFeature(spec)
        results = []
        for i, v in enumerate(lows):
            u = f.update(Bar(low=v, event_time_ns=_s(i)))
            if u.value.is_ready:
                results.append(u.value.value)
        ref = [min(lows[i - window + 1: i + 1]) for i in range(window - 1, len(lows))]
        assert results == pytest.approx(ref)

    def test_rolling_max(self):
        highs = [5.0, 3.0, 4.0, 1.0, 7.0, 6.0]
        window = 3
        spec = FeatureSpec(name="max3", input_type="bar", input_field="high", window=window,
                           params={"type": "rolling_max"})
        f = RollingMaxFeature(spec)
        results = []
        for i, v in enumerate(highs):
            u = f.update(Bar(high=v, event_time_ns=_s(i)))
            if u.value.is_ready:
                results.append(u.value.value)
        ref = [max(highs[i - window + 1: i + 1]) for i in range(window - 1, len(highs))]
        assert results == pytest.approx(ref)


# ===========================================================================
# VWAPFeature
# ===========================================================================

class TestVWAPFeature:
    def test_session_vwap(self):
        spec = FeatureSpec(name="vwap", input_type="bar", params={"type": "vwap"})
        f = VWAPFeature(spec)
        u = f.update(Bar(close=100.0, volume=10.0, event_time_ns=0))
        assert u.value.is_ready
        assert u.value.value == pytest.approx(100.0)
        u = f.update(Bar(close=200.0, volume=5.0, event_time_ns=_s(1)))
        assert u.value.value == pytest.approx(2000.0 / 15.0)

    def test_rolling_count_vwap(self):
        spec = FeatureSpec(name="vwap2", input_type="bar", window=2, window_unit="bars",
                           params={"type": "vwap"})
        f = VWAPFeature(spec)
        f.update(Bar(close=100.0, volume=1.0, event_time_ns=0))
        f.update(Bar(close=200.0, volume=1.0, event_time_ns=_s(1)))
        u = f.update(Bar(close=300.0, volume=1.0, event_time_ns=_s(2)))
        assert u.value.value == pytest.approx(250.0)

    def test_rolling_time_vwap_seconds(self):
        """5-second time-based VWAP using window_unit='seconds'."""
        spec = FeatureSpec(name="vwap5s", input_type="bar", window=5, window_unit="seconds",
                           params={"type": "vwap"})
        f = VWAPFeature(spec)
        f.update(Bar(close=100.0, volume=1.0, event_time_ns=_s(1)))
        f.update(Bar(close=200.0, volume=1.0, event_time_ns=_s(5)))
        # t=6s+1ns: cutoff=1s+1ns → evicts t=1s
        u = f.update(Bar(close=300.0, volume=1.0, event_time_ns=_s(6) + 1))
        assert u.value.value == pytest.approx(250.0)

    def test_custom_price_and_volume_fields(self):
        spec = FeatureSpec(name="vwap_hl", input_type="bar",
                           params={"type": "vwap", "price_field": "high", "volume_field": "volume"})
        f = VWAPFeature(spec)
        u = f.update(Bar(high=110.0, volume=2.0, event_time_ns=0))
        assert u.value.value == pytest.approx(110.0)


# ===========================================================================
# SimpleReturnFeature / LogReturnFeature
# ===========================================================================

class TestSimpleReturnFeature:
    def test_first_bar_not_ready(self):
        spec = FeatureSpec(name="ret", input_type="bar", input_field="close",
                           params={"type": "simple_return"})
        f = SimpleReturnFeature(spec)
        u = f.update(Bar(close=100.0))
        assert not u.value.is_ready

    def test_second_bar_ready(self):
        spec = FeatureSpec(name="ret", input_type="bar", input_field="close",
                           params={"type": "simple_return"})
        f = SimpleReturnFeature(spec)
        f.update(Bar(close=100.0))
        u = f.update(Bar(close=110.0))
        assert u.value.is_ready
        assert u.value.value == pytest.approx(0.10)

    def test_negative_return(self):
        spec = FeatureSpec(name="ret", input_type="bar", input_field="close",
                           params={"type": "simple_return"})
        f = SimpleReturnFeature(spec)
        f.update(Bar(close=200.0))
        u = f.update(Bar(close=150.0))
        assert u.value.value == pytest.approx(-0.25)

    def test_state_dict_round_trip(self):
        spec = FeatureSpec(name="ret", input_type="bar", input_field="close",
                           params={"type": "simple_return"})
        f = SimpleReturnFeature(spec)
        f.update(Bar(close=100.0))
        f.update(Bar(close=110.0))
        snap = f.state_dict()
        f2 = SimpleReturnFeature(spec)
        f2.load_state_dict(snap)
        u = f2.update(Bar(close=121.0))
        assert u.value.value == pytest.approx(0.10)


class TestLogReturnFeature:
    def test_log_return(self):
        spec = FeatureSpec(name="logret", input_type="bar", input_field="close",
                           params={"type": "log_return"})
        f = LogReturnFeature(spec)
        f.update(Bar(close=100.0))
        u = f.update(Bar(close=110.0))
        assert u.value.is_ready
        assert u.value.value == pytest.approx(math.log(110.0 / 100.0))

    def test_zero_price_guard(self):
        spec = FeatureSpec(name="logret", input_type="bar", input_field="close",
                           params={"type": "log_return"})
        f = LogReturnFeature(spec)
        f.update(Bar(close=0.0))
        u = f.update(Bar(close=100.0))
        assert not u.value.is_ready


# ===========================================================================
# EWMAFeature
# ===========================================================================

class TestEWMAFeature:
    def test_first_bar_seeds_value(self):
        spec = FeatureSpec(name="ema5", input_type="bar", input_field="close", window=5,
                           params={"type": "ewma"})
        f = EWMAFeature(spec)
        u = f.update(Bar(close=10.0))
        assert u.value.is_ready
        assert u.value.value == pytest.approx(10.0)

    def test_update_formula(self):
        spec = FeatureSpec(name="ema", input_type="bar", input_field="close",
                           params={"type": "ewma", "alpha": 0.5})
        f = EWMAFeature(spec)
        f.update(Bar(close=4.0))
        u = f.update(Bar(close=8.0))
        assert u.value.value == pytest.approx(6.0)

    def test_state_dict_round_trip(self):
        spec = FeatureSpec(name="ema10", input_type="bar", input_field="close", window=10,
                           params={"type": "ewma"})
        f = EWMAFeature(spec)
        for bar in bars([1.0, 2.0, 3.0, 4.0, 5.0]):
            f.update(bar)
        snap = f.state_dict()
        f2 = EWMAFeature(spec)
        f2.load_state_dict(snap)
        assert f2.value.value == pytest.approx(f.value.value)


# ===========================================================================
# Quote / Book features
# ===========================================================================

class TestSpreadFeature:
    def test_spread(self):
        spec = FeatureSpec(name="spread", input_type="quote", params={"type": "spread"})
        f = SpreadFeature(spec)
        u = f.update(Quote(bid_price=99.5, ask_price=100.5))
        assert u.value.is_ready
        assert u.value.value == pytest.approx(1.0)

    def test_bar_event_gives_no_change(self):
        spec = FeatureSpec(name="spread", input_type="quote", params={"type": "spread"})
        f = SpreadFeature(spec)
        u = f.update(Bar(close=100.0))
        assert not u.value.is_ready


class TestMidPriceFeature:
    def test_mid_price(self):
        spec = FeatureSpec(name="mid", input_type="quote", params={"type": "mid_price"})
        f = MidPriceFeature(spec)
        u = f.update(Quote(bid_price=99.0, ask_price=101.0))
        assert u.value.is_ready
        assert u.value.value == pytest.approx(100.0)


class TestBookImbalanceFeature:
    def test_from_order_book_lists(self):
        spec = FeatureSpec(name="imb", input_type="book_delta",
                           params={"type": "book_imbalance"})
        f = BookImbalanceFeature(spec)
        book = OrderBook(
            bids=[(100.0, 3.0), (99.0, 2.0)],
            asks=[(101.0, 1.0)],
        )
        u = f.update(book)
        assert u.value.is_ready
        assert u.value.value == pytest.approx(4.0 / 6.0)

    def test_equal_sides(self):
        spec = FeatureSpec(name="imb", input_type="book_delta",
                           params={"type": "book_imbalance"})
        f = BookImbalanceFeature(spec)
        u = f.update(OrderBook(bids=[(100.0, 1.0)], asks=[(101.0, 1.0)]))
        assert u.value.value == pytest.approx(0.0)

    def test_empty_book(self):
        spec = FeatureSpec(name="imb", input_type="book_delta",
                           params={"type": "book_imbalance"})
        f = BookImbalanceFeature(spec)
        u = f.update(OrderBook(bids=[], asks=[]))
        assert not u.value.is_ready


# ===========================================================================
# SpecFeatureEngine — routing, timestamps, watermark, late events
# ===========================================================================

class TestSpecFeatureEngine:
    def _make_engine(self, stamp_process_time=False):
        specs = [
            FeatureSpec(name="mean5", input_type="bar", input_field="close", window=5,
                        params={"type": "rolling_mean"}),
            FeatureSpec(name="std5", input_type="bar", input_field="close", window=5,
                        params={"type": "rolling_std"}),
        ]
        return SpecFeatureEngine(specs=specs, stamp_process_time=stamp_process_time)

    def test_snapshot_keys(self):
        engine = self._make_engine()
        snap = engine.on_event(Bar(close=100.0, event_time_ns=0))
        assert "mean5" in snap.values
        assert "std5" in snap.values

    def test_snapshot_ts_event_is_event_time_ns(self):
        engine = self._make_engine()
        snap = engine.on_event(Bar(close=100.0, event_time_ns=_s(42)))
        assert snap.ts_event == _s(42)

    def test_snapshot_receive_time_ns(self):
        engine = self._make_engine()
        snap = engine.on_event(Bar(
            close=100.0,
            event_time_ns=_s(1),
            receive_time_ns=_s(1) + 500_000,  # 0.5ms latency
        ))
        assert snap.receive_time_ns == _s(1) + 500_000

    def test_snapshot_process_time_stamped(self):
        engine = self._make_engine(stamp_process_time=True)
        before = time.time_ns()
        snap = engine.on_event(Bar(close=100.0, event_time_ns=_s(1)))
        after = time.time_ns()
        assert snap.process_time_ns is not None
        assert before <= snap.process_time_ns <= after

    def test_snapshot_process_time_not_stamped_when_disabled(self):
        engine = self._make_engine(stamp_process_time=False)
        snap = engine.on_event(Bar(close=100.0, event_time_ns=_s(1)))
        assert snap.process_time_ns is None

    def test_snapshot_latency_ns(self):
        engine = self._make_engine()
        snap = engine.on_event(Bar(
            close=100.0,
            event_time_ns=_s(5),
            receive_time_ns=_s(5) + 2_000_000,  # 2ms
        ))
        assert snap.latency_ns() == 2_000_000

    def test_watermark_advances_on_events(self):
        engine = self._make_engine()
        engine.on_event(Bar(close=1.0, event_time_ns=_s(1)))
        engine.on_event(Bar(close=2.0, event_time_ns=_s(3)))
        assert engine.max_event_time_ns == _s(3)
        assert engine.watermark_ns == _s(3)

    def test_warmup_pre_heats_features(self):
        engine = self._make_engine()
        engine.warmup(bars([float(i) for i in range(1, 6)]))
        assert engine.is_ready("mean5")
        assert engine.is_ready("std5")

    def test_routing_by_input_type(self):
        bar_spec = FeatureSpec(name="mean3", input_type="bar", input_field="close", window=3,
                               params={"type": "rolling_mean"})
        quote_spec = FeatureSpec(name="spread", input_type="quote",
                                 params={"type": "spread"})
        engine = SpecFeatureEngine(specs=[bar_spec, quote_spec])

        for b in bars([10.0, 20.0, 30.0, 40.0]):
            engine.on_event(b)
        mean_before = engine.get("mean3").value

        # Watermarks are partitioned by stream (instrument_id + input_type).
        # The bar stream watermark does NOT affect the quote stream, so a quote
        # at any timestamp is not late from the bar perspective.
        # We still set an explicit timestamp as good practice.
        engine.on_event(Quote(bid_price=99.0, ask_price=101.0, event_time_ns=_s(4)))
        assert engine.get("mean3").value == mean_before   # bar feature unchanged by quote
        assert engine.get("spread").is_ready
        assert engine.get("spread").value == pytest.approx(2.0)

    def test_state_dict_round_trip(self):
        engine = self._make_engine()
        for b in bars([float(i) for i in range(1, 8)]):
            engine.on_event(b)
        snap = engine.state_dict()

        engine2 = self._make_engine()
        engine2.load_state_dict(snap)
        bar = Bar(close=10.0, event_time_ns=_s(8))
        s1 = engine.on_event(bar)
        s2 = engine2.on_event(bar)
        assert s1.scalar("mean5") == pytest.approx(s2.scalar("mean5"))
        assert s1.scalar("std5") == pytest.approx(s2.scalar("std5"))

    def test_reset_clears_features_and_watermark(self):
        engine = self._make_engine()
        for b in bars([float(i) for i in range(1, 6)]):
            engine.on_event(b)
        assert engine.is_ready()
        engine.reset()
        assert not engine.is_ready()
        assert engine.watermark_ns == 0

    # ------------------------------------------------------------------
    # Late event policies
    # ------------------------------------------------------------------

    def _late_engine(self, policy: str, allowed_lateness_ns: int = 0):
        specs = [FeatureSpec(
            name="rolling_mean_close_5",
            input_type="bar",
            input_field="close",
            window=5,
            trigger=TriggerPolicy(
                kind="on_event",
                allowed_lateness_ns=allowed_lateness_ns,
                late_event_policy=policy,
            ),
        )]
        return SpecFeatureEngine(specs=specs, stamp_process_time=False)

    def test_late_event_drop(self):
        engine = self._late_engine("drop")
        for i in range(5):
            engine.on_event(Bar(close=float(i + 1), event_time_ns=_s(i + 1)))
        mean_before = engine.get("rolling_mean_close_5").value

        engine.on_event(Bar(close=999.0, event_time_ns=_s(1)))   # late!
        assert engine.get("rolling_mean_close_5").value == mean_before

    def test_late_event_log_only(self, caplog):
        import logging
        engine = self._late_engine("log_only")
        for i in range(5):
            engine.on_event(Bar(close=float(i + 1), event_time_ns=_s(i + 1)))
        mean_before = engine.get("rolling_mean_close_5").value

        with caplog.at_level(logging.WARNING):
            engine.on_event(Bar(close=999.0, event_time_ns=_s(1)))

        assert engine.get("rolling_mean_close_5").value == mean_before
        assert "Late event" in caplog.text

    def test_late_event_update_if_not_finalized(self):
        engine = self._late_engine("update_if_not_finalized")
        for i in range(5):
            engine.on_event(Bar(close=float(i + 1), event_time_ns=_s(i + 1)))
        mean_before = engine.get("rolling_mean_close_5").value

        engine.on_event(Bar(close=999.0, event_time_ns=_s(1)))   # late!
        mean_after = engine.get("rolling_mean_close_5").value
        # State was updated (not dropped)
        assert mean_after != mean_before

    def test_late_event_recompute_for_backtest_drops_in_live(self):
        engine = self._late_engine("recompute_for_backtest_only")
        for i in range(5):
            engine.on_event(Bar(close=float(i + 1), event_time_ns=_s(i + 1)))
        mean_before = engine.get("rolling_mean_close_5").value

        engine.on_event(Bar(close=999.0, event_time_ns=_s(1)))   # late!
        # In live mode (not warmup): should drop
        assert engine.get("rolling_mean_close_5").value == mean_before

    def test_in_order_events_never_late(self):
        engine = self._late_engine("drop")
        for i in range(5):
            snap = engine.on_event(Bar(close=float(i + 1), event_time_ns=_s(i + 1)))
        assert snap.values["rolling_mean_close_5"].is_ready

    def test_allowed_lateness_accepts_slightly_late_events(self):
        engine = self._late_engine("drop", allowed_lateness_ns=_s(2))
        for i in range(5):
            engine.on_event(Bar(close=float(i + 1), event_time_ns=_s(i + 5)))
        mean_before = engine.get("rolling_mean_close_5").value

        # Watermark = 9s - 2s = 7s. Event at 7s is on-time (not late).
        engine.on_event(Bar(close=999.0, event_time_ns=_s(7)))
        mean_after = engine.get("rolling_mean_close_5").value
        assert mean_after != mean_before   # accepted, state updated


# ===========================================================================
# Backend — dispatch and registry
# ===========================================================================

class TestBackend:
    def test_python_backend_by_params_type(self):
        spec = FeatureSpec(name="my_mean", input_type="bar", input_field="close",
                           window=3, params={"type": "rolling_mean"})
        f = PythonBackend().create_feature(spec)
        assert isinstance(f, RollingMeanFeature)

    def test_python_backend_by_name_prefix(self):
        spec = FeatureSpec(name="rolling_std_close_5", input_type="bar",
                           input_field="close", window=5)
        f = PythonBackend().create_feature(spec)
        assert isinstance(f, RollingStdFeature)

    def test_python_backend_unknown_type_raises(self):
        spec = FeatureSpec(name="unknown_feature_xyz", input_type="bar")
        with pytest.raises(ValueError, match="cannot determine"):
            PythonBackend().create_feature(spec)

    def test_registry_unknown_backend_raises(self):
        registry = BackendRegistry()
        registry.register("python", PythonBackend())
        spec = FeatureSpec(name="rolling_mean_close_5", input_type="bar",
                           input_field="close", window=5, backend="rust")
        with pytest.raises(ValueError, match="no backend registered"):
            registry.create_feature(spec)

    def test_backend_swappable_same_api(self):
        """Replacing the backend doesn't change the FeatureBase interface."""
        from nautilus_ext.features.compute.spec import FeatureUpdate

        class ConstantBackend:
            class _ConstantFeature:
                def __init__(self, spec): self._spec = spec; self._v = spec.params.get("val", 42.0)
                @property
                def spec(self): return self._spec
                def warmup_required(self): return WarmupRequirement(n_events=0, mandatory=False)
                def reset(self): pass
                def update(self, event):
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
        snap = engine.on_event(Bar(close=50.0, event_time_ns=0))
        assert snap.scalar("rolling_mean_close_5") == pytest.approx(99.0)
        assert isinstance(snap, FeatureSnapshot)

    def test_build_default_registry_has_python(self):
        r = build_default_registry()
        assert "python" in r.available_backends()


# ===========================================================================
# SpecDrivenFeatureEngine — FeaturePipeline integration
# ===========================================================================

class TestSpecDrivenFeatureEngine:
    def _specs(self):
        return [FeatureSpec(name="mean3", input_type="bar", input_field="close", window=3,
                            params={"type": "rolling_mean"})]

    def test_schema_construction(self):
        from nautilus_ext.features.compute.engine import SpecDrivenFeatureEngine
        eng = SpecDrivenFeatureEngine(specs=self._specs(), feature_set_id="my_v1")
        schema = eng.schema
        assert schema.feature_set_id == "my_v1"
        assert any(f.name == "mean3" for f in schema.output_features)

    def test_update_returns_none_before_ready(self):
        from nautilus_ext.features.compute.engine import SpecDrivenFeatureEngine
        eng = SpecDrivenFeatureEngine(specs=self._specs(), feature_set_id="test_v1")
        result = eng.update(Bar(close=1.0, event_time_ns=0))
        assert result is None

    def test_update_returns_feature_event_when_ready(self):
        from nautilus_ext.features.compute.engine import SpecDrivenFeatureEngine
        from nautilus_ext.features.feature_event import FeatureEvent
        eng = SpecDrivenFeatureEngine(specs=self._specs(), feature_set_id="test_v1")
        for b in bars([1.0, 2.0]):
            eng.update(b)
        fe = eng.update(Bar(close=3.0, event_time_ns=_s(3), instrument_id="BTC/USDT"))
        assert isinstance(fe, FeatureEvent)
        assert fe.feature_set_id == "test_v1"
        assert "mean3" in fe.values
        assert fe.values["mean3"] == pytest.approx(2.0)

    def test_ts_event_in_milliseconds(self):
        """FeatureEvent.ts_event must be in milliseconds (not nanoseconds)."""
        from nautilus_ext.features.compute.engine import SpecDrivenFeatureEngine
        eng = SpecDrivenFeatureEngine(specs=self._specs(), feature_set_id="test_v1")
        for b in bars([1.0, 2.0]):
            eng.update(b)
        # event_time_ns = 5 seconds = 5_000_000_000 ns → ts_event = 5000 ms
        fe = eng.update(Bar(close=3.0, event_time_ns=_s(5), instrument_id="BTC/USDT"))
        assert fe is not None
        assert fe.ts_event == 5000   # milliseconds

    def test_integration_with_feature_pipeline(self):
        from nautilus_ext.features.compute.engine import SpecDrivenFeatureEngine
        from nautilus_ext.features.feature_pipeline import FeaturePipeline
        eng = SpecDrivenFeatureEngine(specs=self._specs(), feature_set_id="pipe_v1")
        pipeline = FeaturePipeline(feature_engines=[eng])
        pipeline.warmup(bars([1.0, 2.0, 3.0]))
        fes = pipeline.update(Bar(close=4.0, event_time_ns=_s(4), instrument_id="BTC/USDT"))
        assert len(fes) == 1
        assert fes[0].values["mean3"] == pytest.approx(3.0)

    def test_warmup_events_tagged(self):
        from nautilus_ext.features.compute.engine import SpecDrivenFeatureEngine
        from nautilus_ext.features.feature_pipeline import FeaturePipeline
        eng = SpecDrivenFeatureEngine(specs=self._specs(), feature_set_id="warmup_v1")
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
            (RollingMeanFeature, {"input_field": "close", "window": 5, "params": {"type": "rolling_mean"}}),
            (RollingStdFeature,  {"input_field": "close", "window": 5, "params": {"type": "rolling_std"}}),
            (VWAPFeature, {"params": {"type": "vwap"}}),
            (SimpleReturnFeature, {"params": {"type": "simple_return"}}),
            (SpreadFeature, {"params": {"type": "spread"}}),
            (EWMAFeature, {"params": {"type": "ewma"}}),
        ]:
            spec = FeatureSpec(name="test", **kwargs)
            f = cls(spec)
            assert isinstance(f, FeatureBase), f"{cls.__name__} does not satisfy FeatureBase"


# ===========================================================================
# TimestampConfig — legacy unit conversion and live strictness
# ===========================================================================

@dataclass
class _LegacyEvent:
    ts_event: int = 0
    event_type: str = "bar"


@dataclass
class _NsEvent:
    event_time_ns: int = 0
    event_type: str = "bar"


class TestTimestampConfig:
    def test_convert_ms_default(self):
        """Default config treats ts_event as milliseconds."""
        ts = extract_timestamps(_LegacyEvent(ts_event=5000))
        assert ts.event_time_ns == 5_000_000_000   # 5000 ms → 5s in ns

    def test_convert_us(self):
        config = TimestampConfig(legacy_ts_event_unit="us")
        ts = extract_timestamps(_LegacyEvent(ts_event=5_000_000), config)
        assert ts.event_time_ns == 5_000_000_000   # 5_000_000 μs → 5s in ns

    def test_convert_ns(self):
        config = TimestampConfig(legacy_ts_event_unit="ns")
        ts = extract_timestamps(_LegacyEvent(ts_event=5_000_000_000), config)
        assert ts.event_time_ns == 5_000_000_000   # already ns, no conversion

    def test_require_event_time_ns_raises_in_live_mode(self):
        config = TimestampConfig(require_event_time_ns_for_live=True)
        with pytest.raises(RuntimeError, match="event_time_ns"):
            extract_timestamps(_LegacyEvent(ts_event=1000), config, is_live=True)

    def test_require_event_time_ns_no_raise_when_field_present(self):
        config = TimestampConfig(require_event_time_ns_for_live=True)
        ts = extract_timestamps(_NsEvent(event_time_ns=_s(3)), config, is_live=True)
        assert ts.event_time_ns == _s(3)

    def test_require_event_time_ns_no_raise_in_warmup(self):
        config = TimestampConfig(require_event_time_ns_for_live=True)
        # is_live=False → no raise even if event_time_ns is absent
        ts = extract_timestamps(_LegacyEvent(ts_event=2000), config, is_live=False)
        assert ts.event_time_ns == 2_000_000_000

    def test_convert_helper_ms(self):
        assert convert_legacy_ts_event_to_ns(5000, "ms") == 5_000_000_000

    def test_convert_helper_us(self):
        assert convert_legacy_ts_event_to_ns(5_000_000, "us") == 5_000_000_000

    def test_convert_helper_ns(self):
        assert convert_legacy_ts_event_to_ns(5_000_000_000, "ns") == 5_000_000_000

    def test_convert_helper_unknown_unit_raises(self):
        with pytest.raises(ValueError, match="Unknown legacy_ts_event_unit"):
            convert_legacy_ts_event_to_ns(5000, "seconds")


# ===========================================================================
# Partitioned watermarks — multi-stream correctness
# ===========================================================================

class TestPartitionedWatermarks:
    def _bar_engine(self):
        return SpecFeatureEngine(
            specs=[FeatureSpec(name="m3", input_type="bar", input_field="close",
                               window=3, params={"type": "rolling_mean"})],
            stamp_process_time=False,
        )

    def test_bar_stream_watermark_advances_independently(self):
        """BTC bar watermark does not affect ETH bar watermark."""
        engine = self._bar_engine()
        for i in range(1, 6):
            engine.on_event(Bar(close=float(i), event_time_ns=_s(i), instrument_id="BTC/USDT"))

        assert engine.watermark_for("BTC/USDT", "bar") == _s(5)
        assert engine.watermark_for("ETH/USDT", "bar") == 0   # no ETH events

    def test_quote_stream_not_affected_by_bar_watermark(self):
        """Quote events use their own stream watermark, not the bar watermark."""
        bar_spec = FeatureSpec(name="m3", input_type="bar", input_field="close",
                               window=3, params={"type": "rolling_mean"})
        quote_spec = FeatureSpec(name="spread", input_type="quote",
                                 params={"type": "spread"})
        engine = SpecFeatureEngine(specs=[bar_spec, quote_spec], stamp_process_time=False)

        # Advance bar stream to 10s
        for i in range(1, 11):
            engine.on_event(Bar(close=float(i), event_time_ns=_s(i)))

        # Quote at t=1s: quote stream is fresh (watermark=0), NOT late
        snap = engine.on_event(Quote(bid_price=99.0, ask_price=101.0, event_time_ns=_s(1)))
        assert snap.values["spread"].is_ready
        assert snap.values["spread"].value == pytest.approx(2.0)

        assert engine.watermark_for("BTC/USDT", "bar") == _s(10)
        assert engine.watermark_for("BTC/USDT", "quote") == _s(1)

    def test_all_watermarks_returns_per_stream_dict(self):
        engine = self._bar_engine()
        engine.on_event(Bar(close=1.0, event_time_ns=_s(1), instrument_id="BTC/USDT"))
        engine.on_event(Bar(close=2.0, event_time_ns=_s(5), instrument_id="ETH/USDT"))

        wms = engine.all_watermarks()
        assert wms[StreamKey("BTC/USDT", "bar")] == _s(1)
        assert wms[StreamKey("ETH/USDT", "bar")] == _s(5)

    def test_reset_clears_all_watermarks(self):
        engine = self._bar_engine()
        engine.on_event(Bar(close=1.0, event_time_ns=_s(3), instrument_id="BTC/USDT"))
        engine.reset()
        assert engine.watermark_ns == 0
        assert engine.all_watermarks() == {}

    def test_state_dict_round_trip_multi_stream(self):
        engine = self._bar_engine()
        for i in range(1, 6):
            engine.on_event(Bar(close=float(i), event_time_ns=_s(i), instrument_id="BTC/USDT"))

        state = engine.state_dict()
        engine2 = self._bar_engine()
        engine2.load_state_dict(state)

        assert engine2.watermark_for("BTC/USDT", "bar") == engine.watermark_for("BTC/USDT", "bar")
        assert engine2.get("m3").value == pytest.approx(engine.get("m3").value)

    def test_watermark_ns_returns_max_across_streams(self):
        """watermark_ns property returns max across all streams for backward compat."""
        engine = self._bar_engine()
        engine.on_event(Bar(close=1.0, event_time_ns=_s(1), instrument_id="BTC/USDT"))
        engine.on_event(Bar(close=2.0, event_time_ns=_s(7), instrument_id="ETH/USDT"))
        # max(1s, 7s) = 7s
        assert engine.watermark_ns == _s(7)
        assert engine.max_event_time_ns == _s(7)


# ===========================================================================
# Clock abstraction — deterministic process_time_ns
# ===========================================================================

class TestClockAbstraction:
    def _spec(self):
        return [FeatureSpec(name="m3", input_type="bar", input_field="close",
                            window=3, params={"type": "rolling_mean"})]

    def test_manual_clock_stamps_deterministic_process_time(self):
        clock = ManualClock(initial_ns=1_000_000_000)
        engine = SpecFeatureEngine(specs=self._spec(), stamp_process_time=True, clock=clock)
        snap = engine.on_event(Bar(close=1.0, event_time_ns=_s(1)))
        assert snap.process_time_ns == 1_000_000_000

    def test_manual_clock_advance_reflects_in_latency(self):
        clock = ManualClock(initial_ns=0)
        engine = SpecFeatureEngine(specs=self._spec(), stamp_process_time=True, clock=clock)
        clock.set(_s(5))
        snap = engine.on_event(Bar(close=1.0, event_time_ns=_s(1), receive_time_ns=_s(1)))
        # processing_latency = process_time - receive_time = 5s - 1s = 4s
        assert snap.processing_latency_ns() == _s(4)

    def test_system_clock_is_positive(self):
        sc = SystemClock()
        assert sc.now_ns() > 0

    def test_manual_clock_set_and_advance(self):
        clock = ManualClock(initial_ns=100)
        clock.advance(50)
        assert clock.now_ns() == 150
        clock.set(9_000_000_000)
        assert clock.now_ns() == 9_000_000_000

    def test_no_clock_stamp_when_disabled(self):
        clock = ManualClock(initial_ns=999)
        engine = SpecFeatureEngine(specs=self._spec(), stamp_process_time=False, clock=clock)
        snap = engine.on_event(Bar(close=1.0, event_time_ns=_s(1)))
        assert snap.process_time_ns is None

    def test_manual_clock_satisfies_clock_protocol(self):
        from nautilus_ext.features.compute.clock import Clock
        assert isinstance(ManualClock(), Clock)
        assert isinstance(SystemClock(), Clock)


# ===========================================================================
# Late event policies — explicit per-policy tests
# ===========================================================================

class TestLateEventPoliciesExplicit:
    """One test per late event policy; verifies both state and exception semantics."""

    def _make_ready_engine(self, policy: str, allowed_lateness_ns: int = 0):
        """Engine with 5 ready bars. Returns (engine, value_before_late_event)."""
        spec = FeatureSpec(
            name="mean5",
            input_type="bar",
            input_field="close",
            window=5,
            trigger=TriggerPolicy(
                kind="on_event",
                allowed_lateness_ns=allowed_lateness_ns,
                late_event_policy=policy,
            ),
            params={"type": "rolling_mean"},
        )
        engine = SpecFeatureEngine(specs=[spec], stamp_process_time=False)
        for i in range(5):
            engine.on_event(Bar(close=float(i + 1), event_time_ns=_s(i + 1)))
        return engine, engine.get("mean5").value

    def test_drop_leaves_state_unchanged(self):
        engine, val_before = self._make_ready_engine("drop")
        engine.on_event(Bar(close=9999.0, event_time_ns=_s(1)))  # late
        assert engine.get("mean5").value == val_before

    def test_log_only_leaves_state_unchanged_and_emits_warning(self, caplog):
        import logging
        engine, val_before = self._make_ready_engine("log_only")
        with caplog.at_level(logging.WARNING):
            engine.on_event(Bar(close=9999.0, event_time_ns=_s(1)))
        assert engine.get("mean5").value == val_before
        assert "Late event dropped" in caplog.text

    def test_update_if_not_finalized_incorporates_late_value(self):
        engine, val_before = self._make_ready_engine("update_if_not_finalized")
        engine.on_event(Bar(close=9999.0, event_time_ns=_s(1)))  # late, but accepted
        assert engine.get("mean5").value != val_before   # state changed

    def test_raise_raises_late_event_error(self):
        engine, _ = self._make_ready_engine("raise")
        with pytest.raises(LateEventError) as exc_info:
            engine.on_event(Bar(close=9999.0, event_time_ns=_s(1)))
        err = exc_info.value
        assert err.feature_name == "mean5"
        assert err.trigger_ts_ns == _s(1)
        assert err.watermark_ns > 0

    def test_raise_error_has_full_context(self):
        engine, _ = self._make_ready_engine("raise", allowed_lateness_ns=_s(1))
        # bars at 1..5s → max=5s, allowed=1s → effective watermark=4s
        # event at 3s: 3 < 4 → late → should raise
        with pytest.raises(LateEventError) as exc_info:
            engine.on_event(Bar(close=9999.0, event_time_ns=_s(3)))
        err = exc_info.value
        assert err.allowed_lateness_ns == _s(1)

    def test_recompute_for_backtest_drops_in_live(self):
        engine, val_before = self._make_ready_engine("recompute_for_backtest_only")
        engine.on_event(Bar(close=9999.0, event_time_ns=_s(1)))
        assert engine.get("mean5").value == val_before   # dropped in live mode

    def test_recompute_for_backtest_processes_in_warmup(self):
        spec = FeatureSpec(
            name="mean5",
            input_type="bar",
            input_field="close",
            window=5,
            trigger=TriggerPolicy(late_event_policy="recompute_for_backtest_only"),
            params={"type": "rolling_mean"},
        )
        engine = SpecFeatureEngine(specs=[spec], stamp_process_time=False)
        # During warmup all events are processed regardless of policy
        engine.warmup(bars([1.0, 2.0, 3.0, 4.0, 5.0]))
        assert engine.is_ready("mean5")


# ===========================================================================
# Window metadata — FeatureValue.window_start_ns / window_end_ns / source_event_time_ns
# ===========================================================================

class TestWindowMetadata:
    def test_vwap_time_window_populates_bounds(self):
        """Time-based VWAP emits window_start_ns and window_end_ns."""
        spec = FeatureSpec(name="vwap5s", input_type="bar", window=5,
                           window_unit="seconds", params={"type": "vwap"})
        f = VWAPFeature(spec)
        ts_ns = _s(10)
        u = f.update(Bar(close=100.0, volume=1.0, event_time_ns=ts_ns))
        assert u.value.window_start_ns == ts_ns - _s(5)
        assert u.value.window_end_ns == ts_ns

    def test_vwap_count_window_no_bounds(self):
        """Count-based VWAP does not emit window bounds."""
        spec = FeatureSpec(name="vwap3", input_type="bar", window=3,
                           window_unit="bars", params={"type": "vwap"})
        f = VWAPFeature(spec)
        u = f.update(Bar(close=100.0, volume=1.0, event_time_ns=_s(1)))
        assert u.value.window_start_ns is None
        assert u.value.window_end_ns is None

    def test_session_vwap_no_bounds(self):
        """Unbounded session VWAP has no window bounds."""
        spec = FeatureSpec(name="vwap", input_type="bar", params={"type": "vwap"})
        f = VWAPFeature(spec)
        u = f.update(Bar(close=100.0, volume=1.0, event_time_ns=_s(1)))
        assert u.value.window_start_ns is None
        assert u.value.window_end_ns is None

    def test_rolling_mean_no_window_bounds(self):
        """Count-based rolling mean does not emit window bounds."""
        spec = FeatureSpec(name="m3", input_type="bar", input_field="close",
                           window=3, params={"type": "rolling_mean"})
        f = RollingMeanFeature(spec)
        for i in range(3):
            u = f.update(Bar(close=float(i + 1), event_time_ns=_s(i)))
        assert u.value.window_start_ns is None
        assert u.value.window_end_ns is None

    def test_source_event_time_ns_rolling_mean(self):
        """Rolling mean sets source_event_time_ns on every update."""
        spec = FeatureSpec(name="m3", input_type="bar", input_field="close",
                           window=3, params={"type": "rolling_mean"})
        f = RollingMeanFeature(spec)
        ts_ns = _s(7)
        for i in range(3):
            u = f.update(Bar(close=float(i + 1), event_time_ns=_s(i)))
        u = f.update(Bar(close=4.0, event_time_ns=ts_ns))
        assert u.value.source_event_time_ns == ts_ns

    def test_source_event_time_ns_spread(self):
        """Quote spread sets source_event_time_ns."""
        spec = FeatureSpec(name="spread", input_type="quote", params={"type": "spread"})
        f = SpreadFeature(spec)
        ts_ns = _s(3)
        u = f.update(Quote(bid_price=99.0, ask_price=101.0, event_time_ns=ts_ns))
        assert u.value.source_event_time_ns == ts_ns

    def test_source_event_time_ns_vwap_time_window(self):
        """Time-based VWAP sets source_event_time_ns = window_end_ns."""
        spec = FeatureSpec(name="vwap5s", input_type="bar", window=5,
                           window_unit="seconds", params={"type": "vwap"})
        f = VWAPFeature(spec)
        ts_ns = _s(10)
        u = f.update(Bar(close=100.0, volume=1.0, event_time_ns=ts_ns))
        assert u.value.source_event_time_ns == ts_ns
        assert u.value.window_end_ns == u.value.source_event_time_ns

    def test_engine_snapshot_feature_value_has_source_time(self):
        """FeatureSnapshot values include source_event_time_ns via engine."""
        engine = SpecFeatureEngine(
            specs=[FeatureSpec(name="m3", input_type="bar", input_field="close",
                               window=3, params={"type": "rolling_mean"})],
            stamp_process_time=False,
        )
        ts_ns = _s(7)
        for i in range(3):
            engine.on_event(Bar(close=float(i + 1), event_time_ns=_s(i)))
        snap = engine.on_event(Bar(close=4.0, event_time_ns=ts_ns))
        fv = snap.get("m3")
        assert fv is not None
        assert fv.source_event_time_ns == ts_ns
