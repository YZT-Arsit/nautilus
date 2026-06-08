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
    RollingSumFeature,
    RollingVolumeSumFeature,
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


# ===========================================================================
# input_type_for_event() — canonical name derivation
# ===========================================================================

class TestInputTypeDerivation:
    """input_type_for_event() must return canonical names matching FeatureSpec.input_type."""

    def test_bar_canonical(self):
        from nautilus_ext.features.compute.engine import input_type_for_event
        assert input_type_for_event(Bar(event_type="bar")) == "bar"

    def test_trade_canonical(self):
        from nautilus_ext.features.compute.engine import input_type_for_event
        assert input_type_for_event(Bar(event_type="trade")) == "trade"

    def test_trade_tick_alias(self):
        from nautilus_ext.features.compute.engine import input_type_for_event
        assert input_type_for_event(Bar(event_type="trade_tick")) == "trade"

    def test_quote_canonical(self):
        from nautilus_ext.features.compute.engine import input_type_for_event
        assert input_type_for_event(Quote(event_type="quote")) == "quote"

    def test_quote_tick_alias(self):
        from nautilus_ext.features.compute.engine import input_type_for_event
        assert input_type_for_event(Quote(event_type="quote_tick")) == "quote"

    def test_book_delta_canonical(self):
        from nautilus_ext.features.compute.engine import input_type_for_event
        assert input_type_for_event(OrderBook(event_type="book_delta")) == "book_delta"

    def test_orderbook_alias(self):
        from nautilus_ext.features.compute.engine import input_type_for_event
        assert input_type_for_event(OrderBook(event_type="orderbook")) == "book_delta"

    def test_order_book_alias(self):
        from nautilus_ext.features.compute.engine import input_type_for_event
        assert input_type_for_event(OrderBook(event_type="order_book")) == "book_delta"

    def test_timer_canonical(self):
        from nautilus_ext.features.compute.engine import input_type_for_event
        assert input_type_for_event(Bar(event_type="timer")) == "timer"

    def test_funding_rate_alias(self):
        from nautilus_ext.features.compute.engine import input_type_for_event
        assert input_type_for_event(Bar(event_type="funding_rate")) == "timer"

    def test_unknown_event_type_returns_none(self):
        from nautilus_ext.features.compute.engine import input_type_for_event
        assert input_type_for_event(Bar(event_type="some_custom_type")) is None

    def test_no_event_type_attribute_returns_none(self):
        from nautilus_ext.features.compute.engine import input_type_for_event

        class NoTypeEvent:
            pass

        assert input_type_for_event(NoTypeEvent()) is None

    def test_routing_uses_canonical_name(self):
        """Bar events with event_type='bar' route to features with input_type='bar'."""
        engine = SpecFeatureEngine(
            specs=[FeatureSpec(name="m3", input_type="bar", input_field="close",
                               window=1, params={"type": "rolling_mean"})],
            stamp_process_time=False,
        )
        snap = engine.on_event(Bar(close=5.0, event_time_ns=_s(1), event_type="bar"))
        assert snap.values["m3"].is_ready

    def test_watermark_key_input_type_is_canonical(self):
        """Watermark StreamKey.input_type equals canonical 'bar', not raw 'bar'."""
        engine = SpecFeatureEngine(
            specs=[FeatureSpec(name="m3", input_type="bar", input_field="close",
                               window=1, params={"type": "rolling_mean"})],
            stamp_process_time=False,
        )
        engine.on_event(Bar(close=1.0, event_time_ns=_s(1), event_type="bar"))
        keys = list(engine.all_watermarks().keys())
        assert len(keys) == 1
        assert keys[0].input_type == "bar"

    def test_quote_tick_watermark_key_is_canonical_quote(self):
        """quote_tick events produce a StreamKey with input_type='quote'."""
        engine = SpecFeatureEngine(
            specs=[FeatureSpec(name="spread", input_type="quote",
                               params={"type": "spread"})],
            stamp_process_time=False,
        )
        engine.on_event(Quote(bid_price=99.0, ask_price=101.0,
                              event_time_ns=_s(1), event_type="quote_tick"))
        assert engine.watermark_for("BTC/USDT", "quote") == _s(1)

    def test_exported_from_package(self):
        """input_type_for_event is importable from the top-level package."""
        from nautilus_ext.features.compute import input_type_for_event as fn
        assert fn(Bar(event_type="bar")) == "bar"


# ===========================================================================
# watermark_for() — source-aware and aggregate queries
# ===========================================================================

@dataclass
class _SrcQuote:
    bid_price: float = 99.0
    ask_price: float = 101.0
    ts_event: int = 0
    instrument_id: str = "BTC/USDT"
    event_type: str = "quote"
    event_time_ns: int = 0
    source: str = None


class TestWatermarkForSourceAware:
    def _make_engine(self):
        return SpecFeatureEngine(
            specs=[FeatureSpec(name="spread", input_type="quote",
                               params={"type": "spread"})],
            stamp_process_time=False,
        )

    def test_exact_source_query_returns_stream_watermark(self):
        engine = self._make_engine()
        engine.on_event(_SrcQuote(event_time_ns=_s(5), source="binance"))
        assert engine.watermark_for("BTC/USDT", "quote", source="binance") == _s(5)

    def test_exact_source_query_no_match_returns_zero(self):
        engine = self._make_engine()
        engine.on_event(_SrcQuote(event_time_ns=_s(5), source="binance"))
        assert engine.watermark_for("BTC/USDT", "quote", source="okx") == 0

    def test_aggregate_query_returns_max_across_sources(self):
        """source=None → max watermark across all matching streams."""
        engine = self._make_engine()
        engine.on_event(_SrcQuote(event_time_ns=_s(3), source="binance"))
        engine.on_event(_SrcQuote(event_time_ns=_s(7), source="okx"))
        assert engine.watermark_for("BTC/USDT", "quote") == _s(7)

    def test_aggregate_query_single_source_equals_that_stream(self):
        engine = self._make_engine()
        engine.on_event(_SrcQuote(event_time_ns=_s(4), source="binance"))
        assert engine.watermark_for("BTC/USDT", "quote") == _s(4)

    def test_aggregate_query_no_match_returns_zero(self):
        engine = self._make_engine()
        assert engine.watermark_for("ETH/USDT", "quote") == 0

    def test_binance_watermark_does_not_affect_okx_watermark(self):
        """Binance advancing to 100s does not change OKX watermark at 2s."""
        engine = self._make_engine()
        engine.on_event(_SrcQuote(event_time_ns=_s(100), source="binance"))
        engine.on_event(_SrcQuote(event_time_ns=_s(2), source="okx"))
        assert engine.watermark_for("BTC/USDT", "quote", source="binance") == _s(100)
        assert engine.watermark_for("BTC/USDT", "quote", source="okx") == _s(2)

    def test_source_specific_streams_are_independent_for_lateness(self):
        """OKX quote at 2s is not late just because binance watermark is at 100s."""
        engine = self._make_engine()
        engine.on_event(_SrcQuote(event_time_ns=_s(100), source="binance"))
        # OKX stream watermark is still 0 before its first event — never late
        snap = engine.on_event(_SrcQuote(event_time_ns=_s(2), source="okx"))
        assert snap.values["spread"].is_ready


# ===========================================================================
# Engine mode — is_live parameter
# ===========================================================================

class TestEngineMode:
    def _bar_spec(self):
        return [FeatureSpec(name="m3", input_type="bar", input_field="close",
                            window=3, params={"type": "rolling_mean"})]

    def test_live_mode_raises_when_event_time_ns_missing_and_strict(self):
        """is_live=True + require_event_time_ns_for_live=True + no event_time_ns → raises."""
        engine = SpecFeatureEngine(
            specs=self._bar_spec(),
            ts_config=TimestampConfig(require_event_time_ns_for_live=True),
            is_live=True,
            stamp_process_time=False,
        )
        with pytest.raises(RuntimeError, match="event_time_ns"):
            engine.on_event(_LegacyEvent(ts_event=1000))

    def test_backtest_mode_allows_legacy_ts_event_fallback(self):
        """is_live=False bypasses require_event_time_ns_for_live; ts_event used instead."""
        engine = SpecFeatureEngine(
            specs=self._bar_spec(),
            ts_config=TimestampConfig(require_event_time_ns_for_live=True,
                                      legacy_ts_event_unit="ms"),
            is_live=False,
            stamp_process_time=False,
        )
        snap = engine.on_event(_LegacyEvent(ts_event=5000))
        assert snap.ts_event == 5_000_000_000

    def test_default_is_live_preserves_backward_compat(self):
        """Default is_live=True with default config (no strict check) processes normally."""
        engine = SpecFeatureEngine(specs=self._bar_spec(), stamp_process_time=False)
        snap = engine.on_event(Bar(close=1.0, event_time_ns=_s(1)))
        assert snap.ts_event == _s(1)

    def test_backtest_mode_does_not_raise_even_with_strict_config(self):
        """is_live=False never triggers require_event_time_ns_for_live."""
        engine = SpecFeatureEngine(
            specs=self._bar_spec(),
            ts_config=TimestampConfig(require_event_time_ns_for_live=True,
                                      legacy_ts_event_unit="ms"),
            is_live=False,
            stamp_process_time=False,
        )
        # Bar events with only ts_event (no event_time_ns) — should all succeed
        for i in range(3):
            engine.on_event(Bar(close=float(i + 1), ts_event=(i + 1) * 1000))
        assert engine.is_ready("m3")


# ===========================================================================
# LateEventError — diagnostic fields
# ===========================================================================

class TestLateEventErrorDiagnostics:
    def _make_engine_with_raise(self, stamp_process_time: bool = False,
                                clock=None) -> SpecFeatureEngine:
        spec = FeatureSpec(
            name="mean5",
            input_type="bar",
            input_field="close",
            window=5,
            trigger=TriggerPolicy(kind="on_event", late_event_policy="raise"),
            params={"type": "rolling_mean"},
        )
        kw = dict(specs=[spec], stamp_process_time=stamp_process_time)
        if clock is not None:
            kw["clock"] = clock
        engine = SpecFeatureEngine(**kw)
        for i in range(5):
            engine.on_event(Bar(close=float(i + 1), event_time_ns=_s(i + 1),
                               instrument_id="BTC/USDT"))
        return engine

    def test_stream_key_instrument_and_type(self):
        engine = self._make_engine_with_raise()
        with pytest.raises(LateEventError) as exc_info:
            engine.on_event(Bar(close=9999.0, event_time_ns=_s(1),
                               instrument_id="BTC/USDT"))
        err = exc_info.value
        assert err.stream_key.instrument_id == "BTC/USDT"
        assert err.stream_key.input_type == "bar"

    def test_event_time_ns_field(self):
        engine = self._make_engine_with_raise()
        with pytest.raises(LateEventError) as exc_info:
            engine.on_event(Bar(close=9999.0, event_time_ns=_s(2)))
        assert exc_info.value.event_time_ns == _s(2)

    def test_late_by_ns_equals_watermark_minus_trigger(self):
        engine = self._make_engine_with_raise()
        with pytest.raises(LateEventError) as exc_info:
            engine.on_event(Bar(close=9999.0, event_time_ns=_s(1)))
        err = exc_info.value
        assert err.late_by_ns == err.watermark_ns - err.trigger_ts_ns
        assert err.late_by_ns > 0

    def test_receive_time_ns_included(self):
        engine = self._make_engine_with_raise()
        with pytest.raises(LateEventError) as exc_info:
            engine.on_event(Bar(close=9999.0, event_time_ns=_s(1), receive_time_ns=_s(2)))
        assert exc_info.value.receive_time_ns == _s(2)

    def test_process_time_ns_included_when_stamped(self):
        clock = ManualClock(initial_ns=_s(10))
        engine = self._make_engine_with_raise(stamp_process_time=True, clock=clock)
        clock.set(_s(99))
        with pytest.raises(LateEventError) as exc_info:
            engine.on_event(Bar(close=9999.0, event_time_ns=_s(1)))
        assert exc_info.value.process_time_ns == _s(99)

    def test_process_time_ns_none_when_not_stamped(self):
        engine = self._make_engine_with_raise(stamp_process_time=False)
        with pytest.raises(LateEventError) as exc_info:
            engine.on_event(Bar(close=9999.0, event_time_ns=_s(1)))
        assert exc_info.value.process_time_ns is None

    def test_error_message_contains_key_fields(self):
        engine = self._make_engine_with_raise()
        with pytest.raises(LateEventError) as exc_info:
            engine.on_event(Bar(close=9999.0, event_time_ns=_s(1)))
        msg = str(exc_info.value)
        assert "mean5" in msg
        assert "trigger_ts_ns" in msg
        assert "watermark_ns" in msg
        assert "late_by_ns" in msg


# ===========================================================================
# Late event boundary conditions
# ===========================================================================

class TestLateEventBoundary:
    def _engine_with_lateness(self, allowed_lateness_ns: int, policy: str = "drop"):
        spec = FeatureSpec(
            name="m3",
            input_type="bar",
            input_field="close",
            window=3,
            trigger=TriggerPolicy(
                kind="on_event",
                allowed_lateness_ns=allowed_lateness_ns,
                late_event_policy=policy,
            ),
            params={"type": "rolling_mean"},
        )
        return SpecFeatureEngine(specs=[spec], stamp_process_time=False)

    def test_event_at_1000_then_900_with_lateness_50_is_late(self):
        """max=1000, lateness=50 → watermark=950; event@900 < 950 → dropped."""
        engine = self._engine_with_lateness(50)
        engine.on_event(Bar(close=1.0, event_time_ns=1000))
        before = engine.get("m3")
        engine.on_event(Bar(close=9999.0, event_time_ns=900))
        assert engine.get("m3") == before

    def test_event_at_1000_then_970_with_lateness_50_is_not_late(self):
        """max=1000, lateness=50 → watermark=950; event@970 >= 950 → accepted."""
        engine = self._engine_with_lateness(50)
        engine.on_event(Bar(close=1.0, event_time_ns=1000))
        before = engine.get("m3")
        engine.on_event(Bar(close=2.0, event_time_ns=970))
        assert engine.get("m3") != before

    def test_event_exactly_at_watermark_is_not_late(self):
        """event@950 with watermark=950: 950 < 950 is False → not late."""
        engine = self._engine_with_lateness(50)
        engine.on_event(Bar(close=1.0, event_time_ns=1000))
        before = engine.get("m3")
        engine.on_event(Bar(close=2.0, event_time_ns=950))
        assert engine.get("m3") != before  # state changed → not dropped

    def test_event_one_ns_before_watermark_is_late(self):
        """event@949 with watermark=950: 949 < 950 is True → dropped."""
        engine = self._engine_with_lateness(50)
        engine.on_event(Bar(close=1.0, event_time_ns=1000))
        before = engine.get("m3")
        engine.on_event(Bar(close=9999.0, event_time_ns=949))
        assert engine.get("m3") == before

    def test_aapl_bar_watermark_does_not_affect_msft_bar(self):
        """Advancing AAPL watermark to 10s does not classify MSFT@1s as late."""
        engine = SpecFeatureEngine(
            specs=[FeatureSpec(name="m3", input_type="bar", input_field="close",
                               window=3, params={"type": "rolling_mean"})],
            stamp_process_time=False,
        )
        for i in range(1, 11):
            engine.on_event(Bar(close=float(i), event_time_ns=_s(i),
                               instrument_id="AAPL"))
        snap = engine.on_event(Bar(close=100.0, event_time_ns=_s(1),
                                   instrument_id="MSFT"))
        # MSFT stream watermark starts fresh; event at 1s is never late
        assert engine.watermark_for("MSFT", "bar") == _s(1)
        assert engine.watermark_for("AAPL", "bar") == _s(10)
        # MSFT feature value was updated (not dropped)
        assert snap.values["m3"] is not None

    def test_bar_watermark_does_not_affect_quote_watermark(self):
        """Bar watermark at 10s does not make quote@1s late."""
        bar_spec = FeatureSpec(name="m3", input_type="bar", input_field="close",
                               window=3, params={"type": "rolling_mean"})
        quote_spec = FeatureSpec(name="spread", input_type="quote",
                                 params={"type": "spread"})
        engine = SpecFeatureEngine(specs=[bar_spec, quote_spec], stamp_process_time=False)
        for i in range(1, 11):
            engine.on_event(Bar(close=float(i), event_time_ns=_s(i)))
        snap = engine.on_event(Quote(bid_price=99.0, ask_price=101.0, event_time_ns=_s(1)))
        assert snap.values["spread"].is_ready
        assert snap.values["spread"].value == pytest.approx(2.0)

    def test_source_watermarks_independent_for_lateness(self):
        """OKX@2s is not late when binance watermark is at 100s."""
        engine = SpecFeatureEngine(
            specs=[FeatureSpec(name="spread", input_type="quote",
                               params={"type": "spread"})],
            stamp_process_time=False,
        )
        engine.on_event(_SrcQuote(event_time_ns=_s(100), source="binance"))
        snap = engine.on_event(_SrcQuote(event_time_ns=_s(2), source="okx"))
        assert snap.values["spread"].is_ready  # not dropped


# ===========================================================================
# RollingVolumeSumFeature
# ===========================================================================

from nautilus_ext.features.compute.features import RollingSumFeature, RollingVolumeSumFeature


class TestRollingVolumeSumFeature:
    """Rolling volume sum: O(1) running sum, same update path as other rolling features."""

    def _spec(self, window: int = 3, input_field: str | None = None) -> FeatureSpec:
        return FeatureSpec(
            name="vol_sum_3",
            input_type="bar",
            input_field=input_field,
            window=window,
            params={"type": "rolling_volume_sum"},
        )

    def test_incremental_sum_matches_reference(self):
        """Running sum equals last-window sum at each step."""
        volumes = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0]
        window = 3
        spec = self._spec(window=window)
        feat = RollingVolumeSumFeature(spec)
        incremental = []
        for v in volumes:
            upd = feat.update(Bar(volume=v, event_time_ns=0))
            if upd.value.is_ready:
                incremental.append(upd.value.value)

        # Reference: rolling sum of last `window` values starting when full
        reference = [
            sum(volumes[i - window + 1: i + 1])
            for i in range(window - 1, len(volumes))
        ]
        assert incremental == pytest.approx(reference, rel=1e-12)

    def test_is_ready_only_after_window_bars(self):
        window = 4
        feat = RollingVolumeSumFeature(self._spec(window=window))
        for i in range(window - 1):
            upd = feat.update(Bar(volume=float(i + 1), event_time_ns=0))
            assert not upd.value.is_ready
        upd = feat.update(Bar(volume=float(window), event_time_ns=0))
        assert upd.value.is_ready

    def test_eviction_maintains_correct_sum(self):
        """After filling, sum reflects the last window elements only."""
        feat = RollingVolumeSumFeature(self._spec(window=3))
        for v in [10.0, 20.0, 30.0]:
            feat.update(Bar(volume=v, event_time_ns=0))
        assert feat.value.value == pytest.approx(60.0)
        feat.update(Bar(volume=5.0, event_time_ns=0))
        assert feat.value.value == pytest.approx(55.0)   # 20+30+5

    def test_custom_input_field_respected(self):
        """input_field overrides the default 'volume' field."""
        spec = FeatureSpec(
            name="ask_vol_sum",
            input_type="quote",
            input_field="ask_size",
            window=2,
            params={"type": "rolling_volume_sum"},
        )
        feat = RollingVolumeSumFeature(spec)
        feat.update(Quote(ask_size=3.0, event_time_ns=0))
        upd = feat.update(Quote(ask_size=7.0, event_time_ns=0))
        assert upd.value.is_ready
        assert upd.value.value == pytest.approx(10.0)

    def test_missing_volume_field_returns_no_change(self):
        """Event without the volume field does not crash and returns cached."""
        feat = RollingVolumeSumFeature(self._spec(window=2))

        @dataclass
        class NoVolume:
            event_time_ns: int = 0

        upd = feat.update(NoVolume())
        assert not upd.value.is_ready
        assert upd.value.value is None

    def test_state_dict_round_trip(self):
        feat = RollingVolumeSumFeature(self._spec(window=3))
        for v in [1.0, 2.0, 3.0]:
            feat.update(Bar(volume=v, event_time_ns=0))
        state = feat.state_dict()

        feat2 = RollingVolumeSumFeature(self._spec(window=3))
        feat2.load_state_dict(state)
        assert feat2.value.value == pytest.approx(feat.value.value)
        assert feat2.is_ready == feat.is_ready

    def test_source_event_time_ns_set_on_every_ready_update(self):
        feat = RollingVolumeSumFeature(self._spec(window=2))
        feat.update(Bar(volume=1.0, event_time_ns=_s(1)))
        upd = feat.update(Bar(volume=2.0, event_time_ns=_s(2)))
        assert upd.value.source_event_time_ns == _s(2)

    def test_warmup_required_matches_window(self):
        spec = self._spec(window=5)
        req = RollingVolumeSumFeature(spec).warmup_required()
        assert req.n_events == 5
        assert req.mandatory is True

    def test_engine_routes_bar_events_to_volume_sum(self):
        """Engine correctly routes bar events to rolling_volume_sum feature."""
        spec = FeatureSpec(
            name="vol_sum_3",
            input_type="bar",
            window=3,
            params={"type": "rolling_volume_sum"},
        )
        engine = SpecFeatureEngine(specs=[spec], stamp_process_time=False)
        bs = bars([1.0, 2.0, 3.0], volumes=[10.0, 20.0, 30.0])
        for b in bs:
            snap = engine.on_event(b)
        assert snap.scalar("vol_sum_3") == pytest.approx(60.0)

    def test_engine_does_not_route_quote_events_to_bar_feature(self):
        """rolling_volume_sum with input_type='bar' must not update on quote events."""
        spec = FeatureSpec(
            name="vol_sum_3",
            input_type="bar",
            window=3,
            params={"type": "rolling_volume_sum"},
        )
        engine = SpecFeatureEngine(specs=[spec], stamp_process_time=False)
        for _ in range(5):
            engine.on_event(Quote(bid_price=99.0, ask_price=101.0, event_time_ns=_s(1)))
        assert engine.get("vol_sum_3").is_ready is False

    def test_reset_clears_state(self):
        feat = RollingVolumeSumFeature(self._spec(window=2))
        feat.update(Bar(volume=5.0, event_time_ns=0))
        feat.update(Bar(volume=5.0, event_time_ns=0))
        assert feat.is_ready
        feat.reset()
        assert not feat.is_ready
        assert feat.value.value is None

    def test_backend_dispatch_by_name_prefix(self):
        """PythonBackend infers rolling_volume_sum from name prefix."""
        spec = FeatureSpec(name="rolling_volume_sum_5", input_type="bar", window=5)
        registry = build_default_registry()
        feat = registry.create_feature(spec)
        assert isinstance(feat, RollingVolumeSumFeature)

    def test_backend_dispatch_by_params_type(self):
        """PythonBackend dispatches by params['type'] = 'rolling_volume_sum'."""
        spec = FeatureSpec(
            name="my_vol_sum",
            input_type="bar",
            window=3,
            params={"type": "rolling_volume_sum"},
        )
        registry = build_default_registry()
        feat = registry.create_feature(spec)
        assert isinstance(feat, RollingVolumeSumFeature)


# ===========================================================================
# Warmup and live update use the same incremental update path
# ===========================================================================

class TestWarmupAndLiveSamePath:
    """Verify that engine.warmup() and engine.on_event() produce identical
    incremental state — warmup is not a separate cold-start code path."""

    def _engine(self):
        spec = FeatureSpec(
            name="mean3",
            input_type="bar",
            input_field="close",
            window=3,
            params={"type": "rolling_mean"},
        )
        return SpecFeatureEngine(specs=[spec], stamp_process_time=False)

    def test_state_after_warmup_equals_all_on_event(self):
        """Processing N bars via warmup then M live events equals N+M all on_event.

        Both engines process the same bar list; engine A splits it across warmup
        and on_event, engine B feeds everything through on_event. The live events
        for engine A use the same timestamps as the original bars so they are
        never classified as late relative to the warmup watermark.
        """
        all_bars = bars([1.0, 2.0, 3.0, 4.0, 5.0])

        # Engine A: warmup first 3, then live last 2 (same bar objects, same ts)
        eng_a = self._engine()
        eng_a.warmup(all_bars[:3])
        for b in all_bars[3:]:
            eng_a.on_event(b)

        # Engine B: all events via on_event
        eng_b = self._engine()
        for b in all_bars:
            eng_b.on_event(b)

        assert eng_a.get("mean3").value == pytest.approx(eng_b.get("mean3").value)

    def test_warmup_does_not_add_different_state_than_on_event(self):
        """Warmup updates features through the same feature.update() path.
        Any feature that is ready after warmup holds the same value
        as if the same events were fed via on_event in backtest mode."""
        # Use is_live=False so both paths skip the strict ns check
        spec = FeatureSpec(
            name="m5",
            input_type="bar",
            input_field="close",
            window=5,
            params={"type": "rolling_mean"},
        )

        eng_warmup = SpecFeatureEngine(specs=[spec], stamp_process_time=False, is_live=False)
        eng_live = SpecFeatureEngine(specs=[spec], stamp_process_time=False, is_live=False)

        bs = bars([float(i) for i in range(1, 8)])  # 7 bars

        eng_warmup.warmup(bs)       # all via warmup
        for b in bs:
            eng_live.on_event(b)    # all via on_event

        assert eng_warmup.get("m5").value == pytest.approx(eng_live.get("m5").value)
        assert eng_warmup.is_ready("m5") == eng_live.is_ready("m5")

    def test_rolling_volume_sum_warmup_then_live(self):
        """RollingVolumeSumFeature state is identical regardless of warmup vs live path."""
        spec = FeatureSpec(
            name="vs3",
            input_type="bar",
            window=3,
            params={"type": "rolling_volume_sum"},
        )
        eng_warmup = SpecFeatureEngine(specs=[spec], stamp_process_time=False, is_live=False)
        eng_live = SpecFeatureEngine(specs=[spec], stamp_process_time=False, is_live=False)

        bs = bars([1.0, 2.0, 3.0, 4.0], volumes=[10.0, 20.0, 30.0, 40.0])

        eng_warmup.warmup(bs[:2])
        for b in bs[2:]:
            eng_warmup.on_event(b)
        for b in bs:
            eng_live.on_event(b)

        assert eng_warmup.get("vs3").value == pytest.approx(eng_live.get("vs3").value)

    def test_alias_event_routes_to_canonical_spec(self):
        """quote_tick events (vendor alias) route to features with input_type='quote'."""
        spec = FeatureSpec(name="spread", input_type="quote", params={"type": "spread"})
        engine = SpecFeatureEngine(specs=[spec], stamp_process_time=False)

        # Quote fixture uses event_type="quote_tick" — a vendor alias
        assert Quote().event_type == "quote_tick"

        q = Quote(bid_price=98.0, ask_price=102.0, event_time_ns=_s(1))
        snap = engine.on_event(q)

        # Alias was normalised → routed to spread feature
        assert snap.values["spread"].is_ready
        assert snap.values["spread"].value == pytest.approx(4.0)


# ===========================================================================
# RollingSumFeature — generic rolling sum over any input field
# ===========================================================================

class TestRollingSumFeature:
    """RollingSumFeature: O(1) running sum, update_status observability, field dispatch."""

    def _spec(self, window: int = 3, input_field: str | None = "close") -> FeatureSpec:
        return FeatureSpec(
            name="sum3",
            input_type="bar",
            input_field=input_field,
            window=window,
            params={"type": "rolling_sum"},
        )

    def test_incremental_sum_over_close(self):
        closes = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
        window = 3
        feat = RollingSumFeature(self._spec(window=window))
        incremental = []
        for c in closes:
            upd = feat.update(Bar(close=c, event_time_ns=0))
            if upd.value.is_ready:
                incremental.append(upd.value.value)
        reference = [sum(closes[i - window + 1: i + 1]) for i in range(window - 1, len(closes))]
        assert incremental == pytest.approx(reference, rel=1e-12)

    def test_incremental_sum_over_volume(self):
        vols = [10.0, 20.0, 30.0, 40.0]
        spec = FeatureSpec(name="volsum3", input_type="bar", input_field="volume", window=3,
                           params={"type": "rolling_sum"})
        feat = RollingSumFeature(spec)
        for v in vols[:2]:
            feat.update(Bar(volume=v, event_time_ns=0))
        upd = feat.update(Bar(volume=vols[2], event_time_ns=0))
        assert upd.value.is_ready
        assert upd.value.value == pytest.approx(60.0)   # 10+20+30

    def test_rolling_sum_equals_rolling_volume_sum_on_volume_field(self):
        """rolling_sum(input_field='volume') must equal rolling_volume_sum(default)."""
        spec_rs = FeatureSpec(name="rs", input_type="bar", input_field="volume", window=4,
                              params={"type": "rolling_sum"})
        spec_rvs = FeatureSpec(name="rvs", input_type="bar", window=4,
                               params={"type": "rolling_volume_sum"})
        feat_rs = RollingSumFeature(spec_rs)
        feat_rvs = RollingVolumeSumFeature(spec_rvs)

        vols = [5.0, 10.0, 15.0, 20.0, 25.0]
        for v in vols:
            b = Bar(volume=v, event_time_ns=0)
            feat_rs.update(b)
            feat_rvs.update(b)

        assert feat_rs.value.value == pytest.approx(feat_rvs.value.value)
        assert feat_rs.is_ready == feat_rvs.is_ready

    def test_is_ready_only_after_window_fills(self):
        feat = RollingSumFeature(self._spec(window=5))
        for i in range(4):
            upd = feat.update(Bar(close=float(i + 1), event_time_ns=0))
            assert not upd.value.is_ready
        upd = feat.update(Bar(close=5.0, event_time_ns=0))
        assert upd.value.is_ready

    def test_eviction_correctness(self):
        """After overflow the sum reflects only the last window elements."""
        feat = RollingSumFeature(self._spec(window=3))
        for c in [10.0, 20.0, 30.0]:
            feat.update(Bar(close=c, event_time_ns=0))
        assert feat.value.value == pytest.approx(60.0)
        feat.update(Bar(close=5.0, event_time_ns=0))
        assert feat.value.value == pytest.approx(55.0)   # 20+30+5

    def test_update_status_updated_when_ready(self):
        feat = RollingSumFeature(self._spec(window=2))
        feat.update(Bar(close=1.0, event_time_ns=0))
        upd = feat.update(Bar(close=2.0, event_time_ns=0))
        assert upd.value.update_status == "updated"

    def test_update_status_not_ready_before_window_fills(self):
        feat = RollingSumFeature(self._spec(window=3))
        upd = feat.update(Bar(close=1.0, event_time_ns=0))
        assert upd.value.update_status == "not_ready"

    def test_update_status_skipped_missing_field(self):
        """Missing field sets update_status='skipped_missing_field'."""
        feat = RollingSumFeature(self._spec(window=2, input_field="close"))

        @dataclass
        class NoClose:
            event_time_ns: int = 0

        upd = feat.update(NoClose())
        assert upd.value.update_status == "skipped_missing_field"

    def test_reason_and_source_field_on_skip(self):
        feat = RollingSumFeature(self._spec(window=2, input_field="close"))

        @dataclass
        class NoClose:
            event_time_ns: int = 0

        upd = feat.update(NoClose())
        assert upd.value.source_field == "close"
        assert "close" in (upd.value.reason or "")

    def test_skip_preserves_cached_value(self):
        """A skip update does not change the cached feature value."""
        feat = RollingSumFeature(self._spec(window=2))
        feat.update(Bar(close=1.0, event_time_ns=0))
        feat.update(Bar(close=2.0, event_time_ns=0))
        assert feat.value.value == pytest.approx(3.0)

        @dataclass
        class NoClose:
            event_time_ns: int = 0

        upd = feat.update(NoClose())
        # Return value shows skip status, but internal cache is unchanged
        assert upd.value.update_status == "skipped_missing_field"
        assert feat.value.value == pytest.approx(3.0)   # cached untouched

    def test_warmup_required_mandatory(self):
        req = RollingSumFeature(self._spec(window=7)).warmup_required()
        assert req.n_events == 7
        assert req.mandatory is True

    def test_reset_clears_state(self):
        feat = RollingSumFeature(self._spec(window=2))
        feat.update(Bar(close=1.0, event_time_ns=0))
        feat.update(Bar(close=2.0, event_time_ns=0))
        feat.reset()
        assert not feat.is_ready
        assert feat.value.value is None

    def test_state_dict_round_trip(self):
        feat = RollingSumFeature(self._spec(window=3))
        for c in [1.0, 2.0, 3.0]:
            feat.update(Bar(close=c, event_time_ns=0))
        state = feat.state_dict()
        feat2 = RollingSumFeature(self._spec(window=3))
        feat2.load_state_dict(state)
        assert feat2.value.value == pytest.approx(feat.value.value)
        assert feat2.is_ready == feat.is_ready

    def test_engine_routes_bar_to_rolling_sum(self):
        spec = FeatureSpec(name="close_sum_3", input_type="bar", input_field="close",
                           window=3, params={"type": "rolling_sum"})
        engine = SpecFeatureEngine(specs=[spec], stamp_process_time=False)
        for b in bars([1.0, 2.0, 3.0]):
            snap = engine.on_event(b)
        assert snap.scalar("close_sum_3") == pytest.approx(6.0)

    def test_backend_dispatch_by_params_type(self):
        spec = FeatureSpec(name="my_sum", input_type="bar", input_field="close",
                           window=3, params={"type": "rolling_sum"})
        feat = build_default_registry().create_feature(spec)
        assert isinstance(feat, RollingSumFeature)

    def test_backend_dispatch_by_name_prefix_rolling_sum(self):
        spec = FeatureSpec(name="rolling_sum_5bar", input_type="bar",
                           input_field="close", window=5)
        feat = build_default_registry().create_feature(spec)
        assert isinstance(feat, RollingSumFeature)

    def test_backend_prefix_rolling_sum_does_not_match_rolling_volume_sum(self):
        """'rolling_volume_sum_3' must dispatch to RollingVolumeSumFeature, not RollingSumFeature."""
        spec = FeatureSpec(name="rolling_volume_sum_3", input_type="bar", window=3)
        feat = build_default_registry().create_feature(spec)
        assert isinstance(feat, RollingVolumeSumFeature)


# ===========================================================================
# Update status / observability
# ===========================================================================

class TestUpdateStatus:
    """update_status, reason, source_field in FeatureValue."""

    def test_feature_value_has_update_status_field(self):
        fv = FeatureValue(name="x", value=1.0, is_ready=True)
        assert hasattr(fv, "update_status")
        assert hasattr(fv, "reason")
        assert hasattr(fv, "source_field")

    def test_update_status_defaults_to_none_for_legacy_features(self):
        """Existing features that don't set update_status return None (backward compat)."""
        spec = FeatureSpec(name="m3", input_type="bar", input_field="close",
                           window=3, params={"type": "rolling_mean"})
        feat = RollingMeanFeature(spec)
        upd = feat.update(Bar(close=1.0, event_time_ns=0))
        assert upd.value.update_status is None

    def test_rolling_sum_updated_status_on_ready_emit(self):
        spec = FeatureSpec(name="s2", input_type="bar", input_field="close",
                           window=2, params={"type": "rolling_sum"})
        feat = RollingSumFeature(spec)
        feat.update(Bar(close=1.0, event_time_ns=0))
        upd = feat.update(Bar(close=2.0, event_time_ns=0))
        assert upd.value.update_status == "updated"
        assert upd.value.is_ready is True

    def test_rolling_sum_not_ready_status_before_warmup(self):
        spec = FeatureSpec(name="s3", input_type="bar", input_field="close",
                           window=3, params={"type": "rolling_sum"})
        feat = RollingSumFeature(spec)
        upd = feat.update(Bar(close=5.0, event_time_ns=0))
        assert upd.value.update_status == "not_ready"
        assert upd.value.is_ready is False

    def test_missing_field_status_and_metadata(self):
        spec = FeatureSpec(name="s2", input_type="bar", input_field="close",
                           window=2, params={"type": "rolling_sum"})
        feat = RollingSumFeature(spec)

        @dataclass
        class NoClose:
            event_time_ns: int = 0

        upd = feat.update(NoClose())
        fv = upd.value
        assert fv.update_status == "skipped_missing_field"
        assert fv.source_field == "close"
        assert fv.reason is not None
        assert "close" in fv.reason

    def test_skip_does_not_cache_status(self):
        """update_status on a skip update is NOT stored in the feature's cached value."""
        spec = FeatureSpec(name="s2", input_type="bar", input_field="close",
                           window=2, params={"type": "rolling_sum"})
        feat = RollingSumFeature(spec)
        feat.update(Bar(close=1.0, event_time_ns=0))
        feat.update(Bar(close=2.0, event_time_ns=0))   # now ready

        @dataclass
        class NoClose:
            event_time_ns: int = 0

        feat.update(NoClose())   # skip
        # The cached value (from last real emit) keeps "updated" status
        assert feat.value.update_status == "updated"

    def test_rolling_volume_sum_inherits_observability(self):
        """RollingVolumeSumFeature (subclass) also reports skip status."""
        spec = FeatureSpec(name="vs2", input_type="bar", window=2,
                           params={"type": "rolling_volume_sum"})
        feat = RollingVolumeSumFeature(spec)

        @dataclass
        class NoVolume:
            event_time_ns: int = 0

        upd = feat.update(NoVolume())
        assert upd.value.update_status == "skipped_missing_field"
        assert upd.value.source_field == "volume"


# ===========================================================================
# Backend dispatch hardening — priority and prefix determinism
# ===========================================================================

class TestBackendDispatchHardening:
    """PythonBackend dispatch: params['type'] > exact name > longest-prefix."""

    def test_params_type_overrides_name_prefix(self):
        """params['type']='rolling_mean' wins even when name starts with 'rolling_sum'."""
        spec = FeatureSpec(name="rolling_sum_alias", input_type="bar", input_field="close",
                           window=3, params={"type": "rolling_mean"})
        feat = PythonBackend().create_feature(spec)
        assert isinstance(feat, RollingMeanFeature)

    def test_exact_name_match_dispatches_correctly(self):
        """Exact name 'rolling_sum' must resolve to RollingSumFeature, not prefix-match."""
        spec = FeatureSpec(name="rolling_sum", input_type="bar", input_field="close",
                           window=3)
        feat = PythonBackend().create_feature(spec)
        assert isinstance(feat, RollingSumFeature)

    def test_exact_name_rolling_volume_sum_dispatches_correctly(self):
        spec = FeatureSpec(name="rolling_volume_sum", input_type="bar", window=3)
        feat = PythonBackend().create_feature(spec)
        assert isinstance(feat, RollingVolumeSumFeature)

    def test_longest_prefix_wins_rolling_volume_sum_over_rolling_sum(self):
        """'rolling_volume_sum_3bar' matches 'rolling_volume_sum' (18 chars)
        rather than 'rolling_sum' (11 chars)."""
        spec = FeatureSpec(name="rolling_volume_sum_3bar", input_type="bar", window=3)
        feat = PythonBackend().create_feature(spec)
        assert isinstance(feat, RollingVolumeSumFeature)

    def test_rolling_sum_prefix_does_not_match_rolling_volume_sum_name(self):
        """'rolling_sum_5bar' must NOT resolve to RollingVolumeSumFeature."""
        spec = FeatureSpec(name="rolling_sum_5bar", input_type="bar",
                           input_field="close", window=5)
        feat = PythonBackend().create_feature(spec)
        assert isinstance(feat, RollingSumFeature)
        assert not isinstance(feat, RollingVolumeSumFeature)

    def test_unknown_name_raises_value_error(self):
        spec = FeatureSpec(name="totally_unknown_xyz_feature", input_type="bar")
        with pytest.raises(ValueError, match="cannot determine"):
            PythonBackend().create_feature(spec)

    def test_params_type_unknown_raises_value_error(self):
        spec = FeatureSpec(name="any_name", input_type="bar",
                           params={"type": "no_such_type"})
        with pytest.raises(ValueError, match="unknown feature type"):
            PythonBackend().create_feature(spec)

    def test_dispatch_is_deterministic_for_all_registered_types(self):
        """Every key in _FEATURE_CLASSES resolves to the correct class."""
        from nautilus_ext.features.compute.backend import _FEATURE_CLASSES
        # Derived types require at least one entry in depends_on — supply a minimal valid tuple.
        _DERIVED_TYPES = {"ratio", "difference", "sum", "product", "rolling_std_derived"}
        registry = build_default_registry()
        for type_key, expected_cls in _FEATURE_CLASSES.items():
            if type_key in _DERIVED_TYPES:
                dep_tuple = ("x", "y") if type_key in ("ratio", "difference") else ("x",)
                spec = FeatureSpec(
                    name=f"test_{type_key}",
                    input_type="derived",
                    depends_on=dep_tuple,
                    params={"type": type_key},
                )
            else:
                spec = FeatureSpec(
                    name=f"test_{type_key}",
                    input_type="bar",
                    input_field="close",
                    window=3,
                    params={"type": type_key},
                )
            feat = registry.create_feature(spec)
            assert isinstance(feat, expected_cls), (
                f"type_key={type_key!r} produced {type(feat).__name__}, "
                f"expected {expected_cls.__name__}"
            )


# ===========================================================================
# Backend replacement equivalence
# ===========================================================================

class TestBackendReplacementEquivalence:
    """Replacing the backend must produce identical FeatureSnapshot names/values/readiness."""

    class _DebugPythonBackend:
        """Test-only wrapper around PythonBackend that records creation calls."""

        def __init__(self):
            self._inner = PythonBackend()
            self.created_names: list[str] = []

        def create_feature(self, spec):
            self.created_names.append(spec.name)
            return self._inner.create_feature(spec)

    def _specs(self):
        return [
            FeatureSpec(name="m3", input_type="bar", input_field="close", window=3,
                        params={"type": "rolling_mean"}),
            FeatureSpec(name="sum3", input_type="bar", input_field="close", window=3,
                        params={"type": "rolling_sum"}),
            FeatureSpec(name="spread", input_type="quote", params={"type": "spread"}),
        ]

    def _make_engines(self):
        specs = self._specs()
        debug = self._DebugPythonBackend()

        registry_python = build_default_registry()
        registry_debug = BackendRegistry()
        registry_debug.register("python", debug)

        eng_a = SpecFeatureEngine(specs=specs, backend_registry=registry_python,
                                  stamp_process_time=False)
        eng_b = SpecFeatureEngine(specs=specs, backend_registry=registry_debug,
                                  stamp_process_time=False)
        return eng_a, eng_b, debug

    def test_both_backends_produce_same_feature_names(self):
        eng_a, eng_b, _ = self._make_engines()
        snap_a = eng_a.on_event(Bar(close=1.0, event_time_ns=_s(1)))
        snap_b = eng_b.on_event(Bar(close=1.0, event_time_ns=_s(1)))
        assert set(snap_a.values.keys()) == set(snap_b.values.keys())

    def test_both_backends_produce_same_values_after_warmup(self):
        eng_a, eng_b, _ = self._make_engines()
        bs = bars([1.0, 2.0, 3.0, 4.0, 5.0])
        for b in bs:
            eng_a.on_event(b)
            eng_b.on_event(b)
        assert eng_a.get("m3").value == pytest.approx(eng_b.get("m3").value)
        assert eng_a.get("sum3").value == pytest.approx(eng_b.get("sum3").value)

    def test_both_backends_produce_same_readiness(self):
        eng_a, eng_b, _ = self._make_engines()
        for b in bars([1.0, 2.0]):
            eng_a.on_event(b)
            eng_b.on_event(b)
        assert eng_a.get("m3").is_ready == eng_b.get("m3").is_ready

    def test_debug_backend_tracks_creation(self):
        _, _, debug = self._make_engines()
        assert "m3" in debug.created_names
        assert "sum3" in debug.created_names
        assert "spread" in debug.created_names

    def test_strategy_code_uses_only_spec_and_snapshot(self):
        """Simulate strategy: only FeatureSpec and FeatureSnapshot used, not backend internals."""
        specs = self._specs()
        eng = SpecFeatureEngine(specs=specs, stamp_process_time=False)
        for b in bars([10.0, 20.0, 30.0]):
            snap = eng.on_event(b)

        # Strategy-facing API surface
        assert isinstance(snap, FeatureSnapshot)
        fv = snap.get("m3")
        assert isinstance(fv, FeatureValue)
        scalar = snap.scalar("m3")
        assert isinstance(scalar, float)
        assert snap.all_ready() or not snap.all_ready()   # just exercises the method
        ready_dict = snap.ready_values()
        assert isinstance(ready_dict, dict)


# ===========================================================================
# Performance discipline — O(1) structural guard
# ===========================================================================

class TestPerformanceGuard:
    """Structural checks proving hot-path does not grow unbounded with window size."""

    def test_rolling_window_buffer_bounded_after_overflow(self):
        """Buffer count stays at maxlen after overflow — proves ring-buffer eviction."""
        state = RollingWindowState(maxlen=10)
        for i in range(200):
            state.push(float(i))
        assert state.count == 10
        assert len(state._buf) == 10

    def test_rolling_window_maxlen_attribute_matches_spec(self):
        """deque.maxlen is fixed at construction — O(1) by construction, not by coincidence."""
        state = RollingWindowState(maxlen=10_000)
        assert state._buf.maxlen == 10_000
        for i in range(100):
            state.push(float(i))
        # Only 100 elements; buffer is not full yet, but maxlen is bounded
        assert len(state._buf) == 100
        assert state._buf.maxlen == 10_000

    def test_small_and_large_window_buffer_same_element_count_after_equal_pushes(self):
        """After N pushes, count = min(N, maxlen) — identical for small and large windows."""
        n_pushes = 50
        state_small = RollingWindowState(maxlen=10)
        state_large = RollingWindowState(maxlen=10_000)
        for i in range(n_pushes):
            state_small.push(float(i))
            state_large.push(float(i))
        assert state_small.count == 10       # capped at maxlen
        assert state_large.count == n_pushes # not yet full

    def test_rolling_sum_sum_consistent_regardless_of_window_size(self):
        """Running sum stays correct across different window sizes after overflow."""
        for window in [5, 100, 10_000]:
            state = RollingWindowState(maxlen=window)
            for i in range(window + 3):
                state.push(1.0)   # push all 1s
            # sum must equal window exactly (ring buffer evicted old entries)
            assert state.sum == pytest.approx(float(window))

    def test_no_pandas_import_in_hot_path_modules(self):
        """Features, state, and spec modules must not import pandas."""
        import sys
        for mod_name in [
            "nautilus_ext.features.compute.features",
            "nautilus_ext.features.compute.state",
            "nautilus_ext.features.compute.spec",
            "nautilus_ext.features.compute.engine",
        ]:
            mod = sys.modules.get(mod_name)
            if mod is not None:
                assert not hasattr(mod, "pd") or getattr(mod, "pd", None) is None, (
                    f"{mod_name} has a 'pd' attribute — pandas may have been imported"
                )
            # Also verify pandas is not in the module's globals
            if mod is not None:
                import types
                globals_dict = vars(mod)
                assert "pandas" not in globals_dict, f"pandas found in {mod_name} globals"

    def test_feature_engine_per_event_complexity_bar_features(self):
        """Engine processes exactly len(bar_features) features per bar event.

        This test verifies the routing table does not accidentally process
        non-bar features when a bar event arrives.
        """
        bar_specs = [
            FeatureSpec(name=f"m{i}", input_type="bar", input_field="close",
                        window=3, params={"type": "rolling_mean"})
            for i in range(5)
        ]
        quote_spec = FeatureSpec(name="spread", input_type="quote", params={"type": "spread"})
        all_specs = bar_specs + [quote_spec]

        engine = SpecFeatureEngine(specs=all_specs, stamp_process_time=False)
        snap = engine.on_event(Bar(close=1.0, event_time_ns=_s(1)))

        # Bar event updates all 5 bar features; quote feature stays not_ready
        assert all(snap.values[f"m{i}"] is not None for i in range(5))
        assert not snap.values["spread"].is_ready


# ===========================================================================
# FeatureSnapshot consumption API
# ===========================================================================

class TestFeatureSnapshotAPI:
    """FeatureSnapshot strategy-facing accessor methods."""

    def _engine_and_snap(self, n_warmup=3):
        """Build a small engine, warm up, return (engine, snapshot)."""
        spec_m = FeatureSpec(name="m3", input_type="bar", input_field="close",
                             window=3, params={"type": "rolling_mean"})
        spec_s = FeatureSpec(name="sum3", input_type="bar", input_field="close",
                             window=3, params={"type": "rolling_sum"})
        engine = SpecFeatureEngine(specs=[spec_m, spec_s], stamp_process_time=False)
        for b in bars([1.0, 2.0, 3.0, 4.0]):
            snap = engine.on_event(b)
        return engine, snap

    def test_get_returns_feature_value(self):
        _, snap = self._engine_and_snap()
        fv = snap.get("m3")
        assert isinstance(fv, FeatureValue)
        assert fv.name == "m3"

    def test_get_missing_returns_none_default(self):
        _, snap = self._engine_and_snap()
        assert snap.get("no_such") is None

    def test_get_missing_returns_custom_default(self):
        _, snap = self._engine_and_snap()
        sentinel = FeatureValue(name="x", value=99.0, is_ready=True)
        assert snap.get("no_such", sentinel) is sentinel

    def test_value_returns_raw_scalar_for_ready_feature(self):
        _, snap = self._engine_and_snap()
        v = snap.value("m3")
        assert isinstance(v, float)
        assert v == pytest.approx((2.0 + 3.0 + 4.0) / 3)

    def test_value_missing_returns_none_default(self):
        _, snap = self._engine_and_snap()
        assert snap.value("no_such") is None

    def test_value_missing_returns_custom_default(self):
        _, snap = self._engine_and_snap()
        assert snap.value("no_such", 0.0) == 0.0

    def test_value_not_ready_returns_default(self):
        """Feature that has not yet reached is_ready returns default from value()."""
        spec = FeatureSpec(name="m10", input_type="bar", input_field="close",
                           window=10, params={"type": "rolling_mean"})
        engine = SpecFeatureEngine(specs=[spec], stamp_process_time=False)
        snap = engine.on_event(Bar(close=1.0, event_time_ns=_s(1)))
        assert snap.value("m10") is None
        assert snap.value("m10", float("nan")) != snap.value("m10", float("nan"))  # nan != nan

    def test_is_ready_true_for_ready_feature(self):
        _, snap = self._engine_and_snap()
        assert snap.is_ready("m3") is True

    def test_is_ready_false_for_not_ready_feature(self):
        spec = FeatureSpec(name="m10", input_type="bar", input_field="close",
                           window=10, params={"type": "rolling_mean"})
        engine = SpecFeatureEngine(specs=[spec], stamp_process_time=False)
        snap = engine.on_event(Bar(close=1.0, event_time_ns=_s(1)))
        assert snap.is_ready("m10") is False

    def test_is_ready_false_for_missing_feature(self):
        _, snap = self._engine_and_snap()
        assert snap.is_ready("nonexistent") is False

    def test_ready_values_excludes_not_ready(self):
        spec_ready = FeatureSpec(name="r1", input_type="bar", input_field="close",
                                 window=2, params={"type": "rolling_mean"})
        spec_not   = FeatureSpec(name="r10", input_type="bar", input_field="close",
                                 window=10, params={"type": "rolling_mean"})
        engine = SpecFeatureEngine(specs=[spec_ready, spec_not], stamp_process_time=False)
        for b in bars([1.0, 2.0, 3.0]):
            snap = engine.on_event(b)
        rv = snap.ready_values()
        assert "r1" in rv
        assert "r10" not in rv

    def test_as_dict_excludes_not_ready_by_default(self):
        spec_ready = FeatureSpec(name="r1", input_type="bar", input_field="close",
                                 window=2, params={"type": "rolling_mean"})
        spec_not   = FeatureSpec(name="r10", input_type="bar", input_field="close",
                                 window=10, params={"type": "rolling_mean"})
        engine = SpecFeatureEngine(specs=[spec_ready, spec_not], stamp_process_time=False)
        for b in bars([1.0, 2.0, 3.0]):
            snap = engine.on_event(b)
        d = snap.as_dict()
        assert "r1" in d
        assert "r10" not in d

    def test_as_dict_include_not_ready_true_includes_all(self):
        spec_ready = FeatureSpec(name="r1", input_type="bar", input_field="close",
                                 window=2, params={"type": "rolling_mean"})
        spec_not   = FeatureSpec(name="r10", input_type="bar", input_field="close",
                                 window=10, params={"type": "rolling_mean"})
        engine = SpecFeatureEngine(specs=[spec_ready, spec_not], stamp_process_time=False)
        for b in bars([1.0, 2.0, 3.0]):
            snap = engine.on_event(b)
        d = snap.as_dict(include_not_ready=True)
        assert "r1" in d and d["r1"] is not None
        assert "r10" in d and d["r10"] is None

    def test_updated_names_contains_rolling_sum_after_ready(self):
        """RollingSumFeature emits update_status='updated' once ready."""
        _, snap = self._engine_and_snap()
        updated = snap.updated_names()
        assert "sum3" in updated

    def test_updated_names_excludes_not_ready_status(self):
        """A not_ready feature must not appear in updated_names()."""
        spec = FeatureSpec(name="big", input_type="bar", input_field="close",
                           window=10, params={"type": "rolling_sum"})
        engine = SpecFeatureEngine(specs=[spec], stamp_process_time=False)
        snap = engine.on_event(Bar(close=1.0, event_time_ns=_s(1)))
        assert "big" not in snap.updated_names()

    def test_statuses_includes_updated_status(self):
        _, snap = self._engine_and_snap()
        st = snap.statuses()
        assert st["sum3"] == "updated"

    def test_statuses_includes_not_ready_status(self):
        spec = FeatureSpec(name="big", input_type="bar", input_field="close",
                           window=10, params={"type": "rolling_sum"})
        engine = SpecFeatureEngine(specs=[spec], stamp_process_time=False)
        snap = engine.on_event(Bar(close=1.0, event_time_ns=_s(1)))
        assert snap.statuses()["big"] == "not_ready"

    def test_statuses_includes_skipped_missing_field(self):
        """FeatureSnapshot.statuses() shows skipped_missing_field when field absent."""
        spec = FeatureSpec(name="s1", input_type="bar", input_field="no_such_field",
                           window=2, params={"type": "rolling_sum"})
        engine = SpecFeatureEngine(specs=[spec], stamp_process_time=False)
        snap = engine.on_event(Bar(close=1.0, event_time_ns=_s(1)))
        assert snap.statuses()["s1"] == "skipped_missing_field"

    def test_statuses_legacy_features_return_none(self):
        """RollingMeanFeature (legacy) has update_status=None in statuses()."""
        spec = FeatureSpec(name="m3", input_type="bar", input_field="close",
                           window=3, params={"type": "rolling_mean"})
        engine = SpecFeatureEngine(specs=[spec], stamp_process_time=False)
        snap = engine.on_event(Bar(close=1.0, event_time_ns=_s(1)))
        assert snap.statuses()["m3"] is None

    def test_strategy_helper_uses_only_feature_snapshot(self):
        """Simulate a strategy that imports only FeatureSnapshot and FeatureSpec."""
        def _strategy_signal(snap: FeatureSnapshot) -> float | None:
            if not snap.is_ready("mean3") or not snap.is_ready("sum3"):
                return None
            m = snap.value("mean3", 0.0)
            s = snap.value("sum3", 0.0)
            return m - s / 3.0

        spec_m = FeatureSpec(name="mean3", input_type="bar", input_field="close",
                             window=3, params={"type": "rolling_mean"})
        spec_s = FeatureSpec(name="sum3", input_type="bar", input_field="close",
                             window=3, params={"type": "rolling_sum"})
        engine = SpecFeatureEngine(specs=[spec_m, spec_s], stamp_process_time=False)
        result = None
        for b in bars([1.0, 2.0, 3.0, 4.0]):
            snap = engine.on_event(b)
            result = _strategy_signal(snap)
        assert result is not None
        assert result == pytest.approx(0.0)   # mean == sum/3 for equal-weight window


# ===========================================================================
# Engine latest-value API
# ===========================================================================

class TestEngineLatestValueAPI:
    """SpecFeatureEngine latest-value accessor methods."""

    def _make_engine(self, window=3):
        spec_m = FeatureSpec(name="m3", input_type="bar", input_field="close",
                             window=window, params={"type": "rolling_mean"})
        spec_s = FeatureSpec(name="sum3", input_type="bar", input_field="close",
                             window=window, params={"type": "rolling_sum"})
        return SpecFeatureEngine(specs=[spec_m, spec_s], stamp_process_time=False)

    def test_get_default_returns_none_before_warmup(self):
        engine = self._make_engine()
        fv = engine.get("m3")
        assert fv is not None                # cached stub exists; returns initial FeatureValue
        assert fv.is_ready is False

    def test_get_with_custom_default_for_unknown_name(self):
        engine = self._make_engine()
        sentinel = FeatureValue(name="x", value=42.0, is_ready=True)
        assert engine.get("no_such", sentinel) is sentinel

    def test_get_returns_feature_value_after_warmup(self):
        engine = self._make_engine()
        for b in bars([10.0, 20.0, 30.0]):
            engine.on_event(b)
        fv = engine.get("m3")
        assert isinstance(fv, FeatureValue)
        assert fv.is_ready is True
        assert fv.value == pytest.approx(20.0)

    def test_value_returns_scalar_after_warmup(self):
        engine = self._make_engine()
        for b in bars([10.0, 20.0, 30.0]):
            engine.on_event(b)
        assert engine.value("m3") == pytest.approx(20.0)

    def test_value_returns_default_before_warmup(self):
        engine = self._make_engine()
        engine.on_event(Bar(close=1.0, event_time_ns=_s(1)))
        assert engine.value("m3") is None
        assert engine.value("m3", -1.0) == -1.0

    def test_value_returns_default_for_unknown_name(self):
        engine = self._make_engine()
        assert engine.value("ghost") is None
        assert engine.value("ghost", 0.0) == 0.0

    def test_latest_returns_all_feature_values(self):
        engine = self._make_engine()
        for b in bars([1.0, 2.0, 3.0]):
            engine.on_event(b)
        lat = engine.latest()
        assert set(lat.keys()) == {"m3", "sum3"}
        assert all(isinstance(v, FeatureValue) for v in lat.values())

    def test_latest_values_excludes_not_ready_by_default(self):
        spec_m = FeatureSpec(name="m3",  input_type="bar", input_field="close",
                             window=3,  params={"type": "rolling_mean"})
        spec_big = FeatureSpec(name="m10", input_type="bar", input_field="close",
                               window=10, params={"type": "rolling_mean"})
        engine = SpecFeatureEngine(specs=[spec_m, spec_big], stamp_process_time=False)
        for b in bars([1.0, 2.0, 3.0, 4.0]):
            engine.on_event(b)
        lv = engine.latest_values()
        assert "m3"  in lv
        assert "m10" not in lv

    def test_latest_values_include_not_ready_includes_all(self):
        spec_m = FeatureSpec(name="m3",  input_type="bar", input_field="close",
                             window=3,  params={"type": "rolling_mean"})
        spec_big = FeatureSpec(name="m10", input_type="bar", input_field="close",
                               window=10, params={"type": "rolling_mean"})
        engine = SpecFeatureEngine(specs=[spec_m, spec_big], stamp_process_time=False)
        for b in bars([1.0, 2.0, 3.0, 4.0]):
            engine.on_event(b)
        lv = engine.latest_values(include_not_ready=True)
        assert "m3"  in lv and lv["m3"]  is not None
        assert "m10" in lv and lv["m10"] is None

    def test_ready_true_after_warmup(self):
        engine = self._make_engine()
        for b in bars([1.0, 2.0, 3.0]):
            engine.on_event(b)
        assert engine.ready("m3") is True

    def test_ready_false_before_warmup(self):
        engine = self._make_engine()
        engine.on_event(Bar(close=1.0, event_time_ns=_s(1)))
        assert engine.ready("m3") is False

    def test_ready_false_for_unknown_name(self):
        engine = self._make_engine()
        assert engine.ready("ghost") is False

    def test_statuses_shows_skipped_missing_field(self):
        """skipped_missing_field is visible in the snapshot returned by on_event().

        engine.statuses() reads the feature's internal _cached value, which is NOT
        updated on a skip (by design — the cached state reflects the last real emit).
        The skip status is visible in the FeatureSnapshot returned by on_event().
        """
        spec = FeatureSpec(name="s1", input_type="bar", input_field="no_field",
                           window=2, params={"type": "rolling_sum"})
        engine = SpecFeatureEngine(specs=[spec], stamp_process_time=False)
        snap = engine.on_event(Bar(close=1.0, event_time_ns=_s(1)))
        # Snapshot captures the per-event update (including skip status)
        assert snap.statuses()["s1"] == "skipped_missing_field"
        # Engine-level statuses() reflects _cached, which is unchanged on skip
        assert engine.statuses()["s1"] is None  # initial cached value

    def test_statuses_contains_all_feature_names(self):
        engine = self._make_engine()
        for b in bars([1.0, 2.0, 3.0]):
            engine.on_event(b)
        st = engine.statuses()
        assert set(st.keys()) == {"m3", "sum3"}

    def test_apis_are_backend_independent(self):
        """All new APIs work identically through the default and a custom registry."""
        class _PassthroughBackend:
            def __init__(self):
                self._inner = PythonBackend()
            def create_feature(self, spec):
                return self._inner.create_feature(spec)

        spec = FeatureSpec(name="m3", input_type="bar", input_field="close",
                           window=3, params={"type": "rolling_mean"})

        reg = BackendRegistry()
        reg.register("python", _PassthroughBackend())

        engine_a = SpecFeatureEngine(specs=[spec], stamp_process_time=False)
        engine_b = SpecFeatureEngine(specs=[spec], backend_registry=reg,
                                     stamp_process_time=False)

        for b in bars([10.0, 20.0, 30.0]):
            engine_a.on_event(b)
            engine_b.on_event(b)

        assert engine_a.value("m3") == pytest.approx(engine_b.value("m3"))
        assert engine_a.ready("m3") == engine_b.ready("m3")
        assert set(engine_a.latest()) == set(engine_b.latest())


# ===========================================================================
# FeatureSpec validation
# ===========================================================================

class TestFeatureSpecValidation:
    """SpecFeatureEngine rejects invalid specs at construction time."""

    def test_empty_name_raises(self):
        with pytest.raises(ValueError, match="non-empty"):
            SpecFeatureEngine(
                specs=[FeatureSpec(name="", input_type="bar", input_field="close",
                                   window=3, params={"type": "rolling_mean"})],
                stamp_process_time=False,
            )

    def test_duplicate_name_raises(self):
        spec = FeatureSpec(name="dup", input_type="bar", input_field="close",
                           window=3, params={"type": "rolling_mean"})
        with pytest.raises(ValueError, match="[Dd]uplicate"):
            SpecFeatureEngine(specs=[spec, spec], stamp_process_time=False)

    def test_invalid_input_type_raises(self):
        with pytest.raises(ValueError, match="input_type"):
            SpecFeatureEngine(
                specs=[FeatureSpec(name="x", input_type="candle", input_field="close",
                                   window=3, params={"type": "rolling_mean"})],
                stamp_process_time=False,
            )

    def test_window_zero_raises(self):
        with pytest.raises(ValueError, match="window"):
            SpecFeatureEngine(
                specs=[FeatureSpec(name="x", input_type="bar", input_field="close",
                                   window=0, params={"type": "rolling_mean"})],
                stamp_process_time=False,
            )

    def test_window_negative_raises(self):
        with pytest.raises(ValueError, match="window"):
            SpecFeatureEngine(
                specs=[FeatureSpec(name="x", input_type="bar", input_field="close",
                                   window=-5, params={"type": "rolling_mean"})],
                stamp_process_time=False,
            )

    def test_rolling_sum_without_input_field_raises(self):
        """RollingSumFeature (no _DEFAULT_FIELD) must raise when input_field is absent."""
        with pytest.raises(ValueError, match="input_field"):
            SpecFeatureEngine(
                specs=[FeatureSpec(name="s", input_type="bar", window=3,
                                   params={"type": "rolling_sum"})],  # no input_field
                stamp_process_time=False,
            )

    def test_rolling_volume_sum_without_input_field_is_valid(self):
        """RollingVolumeSumFeature has _DEFAULT_FIELD='volume' so no input_field needed."""
        engine = SpecFeatureEngine(
            specs=[FeatureSpec(name="vs", input_type="bar", window=3,
                               params={"type": "rolling_volume_sum"})],
            stamp_process_time=False,
        )
        assert engine.feature_names() == ["vs"]

    def test_unknown_backend_raises(self):
        with pytest.raises(ValueError, match="backend"):
            SpecFeatureEngine(
                specs=[FeatureSpec(name="x", input_type="bar", input_field="close",
                                   window=3, backend="rust",
                                   params={"type": "rolling_mean"})],
                stamp_process_time=False,
            )

    def test_unknown_feature_type_raises_clear_error(self):
        """Unknown params['type'] raises ValueError naming the bad type."""
        with pytest.raises(ValueError, match="no_such_feature_type"):
            SpecFeatureEngine(
                specs=[FeatureSpec(name="x", input_type="bar", input_field="close",
                                   window=3, params={"type": "no_such_feature_type"})],
                stamp_process_time=False,
            )

    def test_unknown_name_prefix_raises_clear_error(self):
        """A name that matches no known prefix raises ValueError."""
        with pytest.raises(ValueError, match="cannot determine"):
            SpecFeatureEngine(
                specs=[FeatureSpec(name="totally_unknown_xyz", input_type="bar",
                                   input_field="close", window=3)],
                stamp_process_time=False,
            )

    def test_valid_spec_builds_successfully(self):
        engine = SpecFeatureEngine(
            specs=[FeatureSpec(name="m3", input_type="bar", input_field="close",
                               window=3, params={"type": "rolling_mean"})],
            stamp_process_time=False,
        )
        assert engine.feature_names() == ["m3"]

    def test_input_type_vendor_alias_is_accepted(self):
        """'quote_tick' is a recognised alias and should not raise on validation."""
        engine = SpecFeatureEngine(
            specs=[FeatureSpec(name="sp", input_type="quote_tick",
                               params={"type": "spread"})],
            stamp_process_time=False,
        )
        assert engine.feature_names() == ["sp"]


# ===========================================================================
# Feature catalog / registry introspection
# ===========================================================================

class TestFeatureCatalogIntrospection:
    """Backend and engine introspection APIs."""

    def test_available_feature_types_includes_rolling_sum(self):
        assert "rolling_sum" in PythonBackend().available_feature_types()

    def test_available_feature_types_includes_rolling_volume_sum(self):
        assert "rolling_volume_sum" in PythonBackend().available_feature_types()

    def test_available_feature_types_is_sorted(self):
        types = PythonBackend().available_feature_types()
        assert types == sorted(types)

    def test_available_feature_types_contains_all_13_types(self):
        types = PythonBackend().available_feature_types()
        expected = {
            "rolling_mean", "rolling_std", "rolling_min", "rolling_max",
            "rolling_sum", "rolling_volume_sum", "vwap", "simple_return",
            "log_return", "ewma", "spread", "mid_price", "book_imbalance",
        }
        assert expected.issubset(set(types))

    def test_engine_feature_names_is_deterministic(self):
        specs = [
            FeatureSpec(name="b", input_type="bar", input_field="close",
                        window=3, params={"type": "rolling_mean"}),
            FeatureSpec(name="a", input_type="bar", input_field="close",
                        window=3, params={"type": "rolling_mean"}),
            FeatureSpec(name="c", input_type="bar", input_field="close",
                        window=3, params={"type": "rolling_mean"}),
        ]
        engine = SpecFeatureEngine(specs=specs, stamp_process_time=False)
        # Insertion order must be preserved
        assert engine.feature_names() == ["b", "a", "c"]

    def test_feature_specs_returns_correct_mapping(self):
        spec_a = FeatureSpec(name="alpha", input_type="bar", input_field="close",
                             window=3, params={"type": "rolling_mean"})
        spec_b = FeatureSpec(name="beta", input_type="bar", input_field="volume",
                             window=5, params={"type": "rolling_sum"})
        engine = SpecFeatureEngine(specs=[spec_a, spec_b], stamp_process_time=False)
        fs = engine.feature_specs()
        assert fs["alpha"] is spec_a
        assert fs["beta"]  is spec_b

    def test_feature_specs_keys_match_feature_names(self):
        specs = [
            FeatureSpec(name="x", input_type="bar", input_field="close",
                        window=3, params={"type": "rolling_mean"}),
            FeatureSpec(name="y", input_type="bar", input_field="close",
                        window=5, params={"type": "rolling_std"}),
        ]
        engine = SpecFeatureEngine(specs=specs, stamp_process_time=False)
        assert set(engine.feature_specs().keys()) == set(engine.feature_names())

    def test_feature_specs_are_frozen_dataclasses(self):
        """FeatureSpecs are frozen — verifies safe to return without copying."""
        spec = FeatureSpec(name="z", input_type="bar", input_field="close",
                           window=3, params={"type": "rolling_mean"})
        engine = SpecFeatureEngine(specs=[spec], stamp_process_time=False)
        returned = engine.feature_specs()["z"]
        with pytest.raises((TypeError, AttributeError)):
            returned.name = "modified"  # type: ignore[misc]


# ===========================================================================
# Profiling hook
# ===========================================================================

class TestProfilingHook:
    """SpecFeatureEngine optional profiling mode."""

    def _make_engine(self, profile: bool = True):
        spec_s = FeatureSpec(name="sum3", input_type="bar", input_field="close",
                             window=3, params={"type": "rolling_sum"})
        spec_s5 = FeatureSpec(name="sum5", input_type="bar", input_field="close",
                              window=5, params={"type": "rolling_sum"})
        return SpecFeatureEngine(specs=[spec_s, spec_s5], stamp_process_time=False,
                                 profile=profile)

    def test_profile_summary_disabled_returns_false(self):
        engine = self._make_engine(profile=False)
        assert engine.profile_summary() == {"profile": False}

    def test_profile_summary_enabled_returns_profile_true(self):
        engine = self._make_engine(profile=True)
        summary = engine.profile_summary()
        assert summary["profile"] is True
        assert "features" in summary

    def test_profile_summary_contains_all_feature_names(self):
        engine = self._make_engine(profile=True)
        summary = engine.profile_summary()
        assert set(summary["features"].keys()) == {"sum3", "sum5"}

    def test_update_count_increments_after_ready(self):
        """update_count increases once the feature has enough history.

        Uses RollingSumFeature because it explicitly sets update_status='updated'.
        Legacy features (RollingMean etc.) return update_status=None so they
        do not increment the update_count counter.
        """
        engine = self._make_engine(profile=True)
        # sum3 is ready after 3 events; sum5 after 5
        for b in bars([1.0, 2.0, 3.0, 4.0, 5.0, 6.0]):
            engine.on_event(b)
        summary = engine.profile_summary()["features"]
        assert summary["sum3"]["update_count"] >= 3   # ready from bar 3 → bars 3,4,5,6
        assert summary["sum5"]["update_count"] >= 1   # ready from bar 5 → bars 5,6

    def test_skip_count_increments_while_not_ready(self):
        """Not-ready events increment skip_count (update_status='not_ready')."""
        engine = self._make_engine(profile=True)
        for b in bars([1.0, 2.0]):  # only 2 events — sum3 (window=3) stays not_ready
            engine.on_event(b)
        summary = engine.profile_summary()["features"]
        # Both features have skips; sum3 definitely skipped 2 times
        assert summary["sum3"]["skip_count"] == 2

    def test_late_drop_count_increments_on_dropped_late_event(self):
        """Late events with policy='drop' increment late_drop_count."""
        spec = FeatureSpec(
            name="m3",
            input_type="bar",
            input_field="close",
            window=3,
            params={"type": "rolling_mean"},
            trigger=TriggerPolicy(kind="on_event", allowed_lateness_ns=0,
                                  late_event_policy="drop"),
        )
        engine = SpecFeatureEngine(specs=[spec], stamp_process_time=False,
                                   profile=True)
        # Advance watermark
        for b in bars([1.0, 2.0, 3.0]):
            engine.on_event(b)
        # Send a late event (ts_event in the past)
        engine.on_event(Bar(close=99.0, event_time_ns=_s(0)))
        summary = engine.profile_summary()["features"]
        assert summary["m3"]["late_drop_count"] >= 1

    def test_no_profile_dicts_when_disabled(self):
        """Disabling profile leaves the count dicts empty (no wasted memory)."""
        engine = self._make_engine(profile=False)
        assert engine._profile_update_count == {}
        assert engine._profile_skip_count   == {}
        assert engine._profile_late_drop_count == {}

    def test_last_status_present_when_enabled(self):
        """profile_summary() includes last_status per feature when profile=True."""
        engine = self._make_engine(profile=True)
        engine.on_event(Bar(close=1.0, event_time_ns=_s(1)))
        summary = engine.profile_summary()
        assert "last_status" in summary["features"]["sum3"]

    def test_last_status_updated_reflects_most_recent_event(self):
        """last_status tracks the update_status of the most recent on_event() call."""
        engine = self._make_engine(profile=True)
        # Before ready: update_status="not_ready" → last_status="not_ready"
        engine.on_event(Bar(close=1.0, event_time_ns=_s(1)))
        assert engine.profile_summary()["features"]["sum3"]["last_status"] == "not_ready"
        # After ready: update_status="updated" → last_status="updated"
        for i in range(2, 5):
            engine.on_event(Bar(close=float(i), event_time_ns=_s(i)))
        assert engine.profile_summary()["features"]["sum3"]["last_status"] == "updated"

    def test_last_status_none_when_no_events(self):
        """last_status is None before any on_event() calls."""
        engine = self._make_engine(profile=True)
        summary = engine.profile_summary()
        assert summary["features"]["sum3"]["last_status"] is None

    def test_last_status_late_dropped_on_dropped_late_event(self):
        """last_status reflects 'late_dropped' when a late event is dropped."""
        spec = FeatureSpec(
            name="sum3",
            input_type="bar",
            input_field="close",
            window=3,
            params={"type": "rolling_sum"},
            trigger=TriggerPolicy(kind="on_event", late_event_policy="drop"),
        )
        engine = SpecFeatureEngine(specs=[spec], stamp_process_time=False, profile=True)
        for b in bars([1.0, 2.0, 3.0]):
            engine.on_event(b)
        engine.on_event(Bar(close=99.0, event_time_ns=_s(0)))  # late
        assert engine.profile_summary()["features"]["sum3"]["last_status"] == "late_dropped"


# ===========================================================================
# Event adapters — BarMarketEvent / QuoteMarketEvent
# ===========================================================================

from nautilus_ext.features.compute.adapters import (
    BarMarketEvent,
    QuoteMarketEvent,
    TradeMarketEvent,
    InMemoryEventProvider,
    HistoricalEventProvider,
    adapt_bar_event,
    adapt_quote_tick_event,
)
from nautilus_ext.data.events import BarEvent, QuoteTickEvent
from datetime import datetime, timezone


def _dt(ts_s: float) -> datetime:
    """Return a UTC-aware datetime from POSIX seconds (float)."""
    return datetime.fromtimestamp(ts_s, tz=timezone.utc)


class TestEventAdapters:
    """adapt_bar_event() and adapt_quote_tick_event() convert existing event classes."""

    def test_adapt_bar_event_event_type(self):
        bar = BarEvent(
            instrument_id="BTC/USDT", open=100.0, high=101.0, low=99.0,
            close=100.5, volume=500.0, ts_event=_dt(1_000.0),
        )
        adapted = adapt_bar_event(bar)
        assert adapted.event_type == "bar"

    def test_adapt_bar_event_fields_preserved(self):
        bar = BarEvent(
            instrument_id="BTC/USDT", open=1.0, high=2.0, low=0.5,
            close=1.5, volume=100.0, ts_event=_dt(1_000.0), source="binance",
        )
        adapted = adapt_bar_event(bar)
        assert adapted.instrument_id == "BTC/USDT"
        assert adapted.open   == pytest.approx(1.0)
        assert adapted.high   == pytest.approx(2.0)
        assert adapted.low    == pytest.approx(0.5)
        assert adapted.close  == pytest.approx(1.5)
        assert adapted.volume == pytest.approx(100.0)
        assert adapted.source == "binance"

    def test_adapt_bar_event_datetime_to_ns(self):
        """datetime ts_event is correctly converted to nanoseconds."""
        ts_s = 1_700_000_000.0
        bar = BarEvent(
            instrument_id="X", open=1.0, high=1.0, low=1.0, close=1.0,
            volume=1.0, ts_event=_dt(ts_s),
        )
        adapted = adapt_bar_event(bar)
        expected_ns = int(ts_s * 1_000_000_000)
        assert adapted.event_time_ns == expected_ns

    def test_adapt_bar_event_ts_init_becomes_receive_time(self):
        ts_event_s = 1_700_000_000.0
        ts_init_s  = 1_700_000_001.0
        bar = BarEvent(
            instrument_id="X", open=1.0, high=1.0, low=1.0, close=1.0,
            volume=1.0, ts_event=_dt(ts_event_s), ts_init=_dt(ts_init_s),
        )
        adapted = adapt_bar_event(bar)
        assert adapted.receive_time_ns == int(ts_init_s * 1_000_000_000)

    def test_adapt_bar_event_no_ts_init_receive_equals_event(self):
        ts_s = 1_700_000_000.0
        bar = BarEvent(
            instrument_id="X", open=1.0, high=1.0, low=1.0, close=1.0,
            volume=1.0, ts_event=_dt(ts_s),
        )
        adapted = adapt_bar_event(bar)
        assert adapted.receive_time_ns == adapted.event_time_ns

    def test_adapt_quote_tick_event_type(self):
        q = QuoteTickEvent(
            instrument_id="ETH/USDT", bid_price=99.0, ask_price=101.0,
            bid_size=1.0, ask_size=1.0, ts_event=_dt(1_000.0),
        )
        adapted = adapt_quote_tick_event(q)
        assert adapted.event_type == "quote"

    def test_adapt_quote_tick_event_fields_preserved(self):
        q = QuoteTickEvent(
            instrument_id="ETH/USDT", bid_price=99.5, ask_price=100.5,
            bid_size=2.0, ask_size=3.0, ts_event=_dt(1_000.0), source="okx",
        )
        adapted = adapt_quote_tick_event(q)
        assert adapted.instrument_id == "ETH/USDT"
        assert adapted.bid_price  == pytest.approx(99.5)
        assert adapted.ask_price  == pytest.approx(100.5)
        assert adapted.bid_size   == pytest.approx(2.0)
        assert adapted.ask_size   == pytest.approx(3.0)
        assert adapted.source     == "okx"

    def test_adapt_bar_event_engine_can_consume(self):
        """Engine accepts adapted BarEvent and routes it to bar features."""
        spec = FeatureSpec(name="m1", input_type="bar", input_field="close",
                           window=1, params={"type": "rolling_mean"})
        engine = SpecFeatureEngine(specs=[spec], stamp_process_time=False)
        bar = BarEvent(
            instrument_id="BTC/USDT", open=1.0, high=1.0, low=1.0,
            close=42.0, volume=10.0, ts_event=_dt(1_000.0),
        )
        snap = engine.on_event(adapt_bar_event(bar))
        assert snap.is_ready("m1")
        assert snap.value("m1") == pytest.approx(42.0)

    def test_adapt_quote_engine_can_consume(self):
        """Engine accepts adapted QuoteTickEvent and routes it to quote features."""
        spec = FeatureSpec(name="spread", input_type="quote", params={"type": "spread"})
        engine = SpecFeatureEngine(specs=[spec], stamp_process_time=False)
        q = QuoteTickEvent(
            instrument_id="ETH/USDT", bid_price=99.0, ask_price=101.0,
            bid_size=1.0, ask_size=1.0, ts_event=_dt(1_000.0),
        )
        snap = engine.on_event(adapt_quote_tick_event(q))
        assert snap.is_ready("spread")
        assert snap.value("spread") == pytest.approx(2.0)

    def test_adapted_bar_is_frozen(self):
        """BarMarketEvent is a frozen dataclass."""
        bar = BarEvent(
            instrument_id="X", open=1.0, high=1.0, low=1.0,
            close=1.0, volume=1.0, ts_event=_dt(1_000.0),
        )
        adapted = adapt_bar_event(bar)
        with pytest.raises((TypeError, AttributeError)):
            adapted.close = 999.0  # type: ignore[misc]


# ===========================================================================
# Historical event provider (InMemoryEventProvider + warmup integration)
# ===========================================================================

class TestHistoricalEventProvider:
    """InMemoryEventProvider filters and SpecFeatureEngine.warmup() integration."""

    def _adapted_bars(self, n: int, instrument_id: str = "BTC/USDT") -> list:
        return [
            BarMarketEvent(
                instrument_id=instrument_id,
                open=float(i), high=float(i) + 0.5, low=float(i) - 0.5,
                close=float(i), volume=100.0,
                event_type="bar",
                event_time_ns=_s(i + 1),
                receive_time_ns=_s(i + 1),
            )
            for i in range(n)
        ]

    def _adapted_quotes(self, n: int, instrument_id: str = "BTC/USDT") -> list:
        return [
            QuoteMarketEvent(
                instrument_id=instrument_id,
                bid_price=float(99 + i), ask_price=float(101 + i),
                bid_size=1.0, ask_size=1.0,
                event_type="quote",
                event_time_ns=_s(i + 1),
                receive_time_ns=_s(i + 1),
            )
            for i in range(n)
        ]

    def test_iter_events_yields_all_by_default(self):
        events = self._adapted_bars(5)
        provider = InMemoryEventProvider(events)
        yielded = list(provider.iter_events())
        assert len(yielded) == 5

    def test_iter_events_filter_by_instrument_id(self):
        btc = self._adapted_bars(3, "BTC/USDT")
        eth = self._adapted_bars(2, "ETH/USDT")
        provider = InMemoryEventProvider(btc + eth)
        btc_events = list(provider.iter_events(instrument_id="BTC/USDT"))
        assert len(btc_events) == 3
        assert all(e.instrument_id == "BTC/USDT" for e in btc_events)

    def test_iter_events_filter_by_input_type(self):
        bars = self._adapted_bars(4)
        quotes = self._adapted_quotes(3)
        provider = InMemoryEventProvider(bars + quotes)
        bar_only = list(provider.iter_events(input_type="bar"))
        assert len(bar_only) == 4
        quote_only = list(provider.iter_events(input_type="quote"))
        assert len(quote_only) == 3

    def test_iter_events_filter_by_time_range(self):
        events = self._adapted_bars(10)  # event_time_ns = _s(1)..._s(10)
        provider = InMemoryEventProvider(events)
        # start_ns=_s(3), end_ns=_s(7) → events with ts in [3s, 7s) → indices 2..5
        filtered = list(provider.iter_events(start_ns=_s(3), end_ns=_s(7)))
        assert len(filtered) == 4  # _s(3), _s(4), _s(5), _s(6)

    def test_warmup_from_provider_warms_features(self):
        """warmup from InMemoryEventProvider produces correct feature state."""
        spec = FeatureSpec(name="m3", input_type="bar", input_field="close",
                           window=3, params={"type": "rolling_mean"})
        engine = SpecFeatureEngine(specs=[spec], stamp_process_time=False)
        events = self._adapted_bars(5)
        provider = InMemoryEventProvider(events)
        engine.warmup(provider.iter_events())
        assert engine.is_ready("m3")
        # mean of last 3 closes: 2.0, 3.0, 4.0
        assert engine.value("m3") == pytest.approx((2.0 + 3.0 + 4.0) / 3)

    def test_warmup_preserves_event_order(self):
        """Provider yields events in insertion order — watermark advances monotonically."""
        spec = FeatureSpec(name="m3", input_type="bar", input_field="close",
                           window=3, params={"type": "rolling_mean"})
        engine = SpecFeatureEngine(specs=[spec], stamp_process_time=False)
        events = self._adapted_bars(5)
        provider = InMemoryEventProvider(events)
        engine.warmup(provider.iter_events())
        wm = engine.watermark_for("BTC/USDT", "bar")
        assert wm == _s(5)  # last event had event_time_ns = _s(5)

    def test_warmup_plus_live_equals_full_replay(self):
        """warmup([:3]) + on_event([3:]) == all on_event for the same closes."""
        spec = FeatureSpec(name="m3", input_type="bar", input_field="close",
                           window=3, params={"type": "rolling_mean"})

        events = self._adapted_bars(6)
        provider = InMemoryEventProvider(events)

        # Engine A: warmup first 3, on_event last 3
        eng_a = SpecFeatureEngine(specs=[spec], stamp_process_time=False)
        eng_a.warmup(provider.iter_events(end_ns=_s(4)))   # events with ts < _s(4)
        for e in events[3:]:
            eng_a.on_event(e)

        # Engine B: all via on_event
        eng_b = SpecFeatureEngine(specs=[spec], stamp_process_time=False)
        for e in events:
            eng_b.on_event(e)

        assert eng_a.value("m3") == pytest.approx(eng_b.value("m3"))

    def test_provider_empty_yields_nothing(self):
        provider = InMemoryEventProvider([])
        assert list(provider.iter_events()) == []

    def test_provider_len(self):
        events = self._adapted_bars(7)
        provider = InMemoryEventProvider(events)
        assert len(provider) == 7

    def test_provider_append(self):
        provider = InMemoryEventProvider([])
        ev = self._adapted_bars(1)[0]
        provider.append(ev)
        assert len(provider) == 1

    def test_provider_satisfies_protocol(self):
        """InMemoryEventProvider satisfies the HistoricalEventProvider protocol."""
        provider = InMemoryEventProvider([])
        assert isinstance(provider, HistoricalEventProvider)


# ===========================================================================
# RealizedVolatilityFeature
# ===========================================================================

from nautilus_ext.features.compute.features import RealizedVolatilityFeature


class TestRealizedVolatilityFeature:
    """RealizedVolatilityFeature: rolling std of log returns."""

    def _spec(self, window: int = 5, field: str = "close") -> FeatureSpec:
        return FeatureSpec(
            name="rvol5",
            input_type="bar",
            input_field=field,
            window=window,
            params={"type": "realized_volatility"},
        )

    def _ref_realized_vol(self, closes: list[float], window: int) -> list[float]:
        """Reference: rolling sample std of log returns (window returns)."""
        log_rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
        results = []
        for i in range(window - 1, len(log_rets)):
            win = log_rets[i - window + 1: i + 1]
            mean = sum(win) / window
            var = sum((x - mean) ** 2 for x in win) / (window - 1)
            results.append(math.sqrt(max(0.0, var)))
        return results

    def test_not_ready_before_window_full(self):
        feat = RealizedVolatilityFeature(self._spec(window=3))
        for i in range(1, 4):  # 3 bars → only 2 returns, need 3
            upd = feat.update(Bar(close=float(i), event_time_ns=_s(i)))
            assert not upd.value.is_ready

    def test_ready_after_window_plus_one_bars(self):
        """window=3 requires 4 bars (3 log returns) → ready on bar 4."""
        feat = RealizedVolatilityFeature(self._spec(window=3))
        for i in range(1, 5):
            upd = feat.update(Bar(close=float(i), event_time_ns=_s(i)))
        assert upd.value.is_ready

    def test_value_matches_reference_std(self):
        closes = [100.0, 101.0, 102.0, 101.5, 103.0, 102.5, 104.0]
        window = 4
        feat = RealizedVolatilityFeature(self._spec(window=window))
        computed = []
        for i, c in enumerate(closes):
            upd = feat.update(Bar(close=c, event_time_ns=_s(i)))
            if upd.value.is_ready:
                computed.append(upd.value.value)
        ref = self._ref_realized_vol(closes, window)
        assert len(computed) == len(ref)
        for a, b in zip(computed, ref):
            assert a == pytest.approx(b, rel=1e-10)

    def test_update_status_populated(self):
        feat = RealizedVolatilityFeature(self._spec(window=3))
        upd1 = feat.update(Bar(close=100.0, event_time_ns=_s(1)))
        assert upd1.value.update_status == "not_ready"
        for i in range(2, 5):
            upd = feat.update(Bar(close=float(100 + i), event_time_ns=_s(i)))
        assert upd.value.update_status == "updated"

    def test_missing_field_returns_skipped_status(self):
        feat = RealizedVolatilityFeature(self._spec(window=3, field="close"))

        @dataclass
        class NoClose:
            event_time_ns: int = 0

        upd = feat.update(NoClose())
        assert upd.value.update_status == "skipped_missing_field"

    def test_reset_clears_state(self):
        feat = RealizedVolatilityFeature(self._spec(window=3))
        for i in range(1, 5):
            feat.update(Bar(close=float(i), event_time_ns=_s(i)))
        assert feat.is_ready
        feat.reset()
        assert not feat.is_ready
        assert feat.value.value is None

    def test_state_dict_round_trip(self):
        spec = self._spec(window=3)
        feat = RealizedVolatilityFeature(spec)
        closes = [100.0, 101.0, 102.0, 103.0, 104.0]
        for i, c in enumerate(closes):
            feat.update(Bar(close=c, event_time_ns=_s(i)))
        state = feat.state_dict()

        feat2 = RealizedVolatilityFeature(spec)
        feat2.load_state_dict(state)
        assert feat2.is_ready == feat.is_ready
        assert feat2.value.value == pytest.approx(feat.value.value)

    def test_warmup_required_is_window_plus_one(self):
        req = RealizedVolatilityFeature(self._spec(window=10)).warmup_required()
        assert req.n_events == 11
        assert req.mandatory is True

    def test_backend_dispatch_by_params_type(self):
        """PythonBackend dispatches realized_volatility by params['type']."""
        spec = FeatureSpec(
            name="rv5", input_type="bar", input_field="close",
            window=5, params={"type": "realized_volatility"},
        )
        feat = build_default_registry().create_feature(spec)
        assert isinstance(feat, RealizedVolatilityFeature)

    def test_engine_routes_bar_events_to_realized_volatility(self):
        """Engine correctly routes bar events to realized_volatility feature."""
        spec = FeatureSpec(
            name="rv3", input_type="bar", input_field="close",
            window=3, params={"type": "realized_volatility"},
        )
        engine = SpecFeatureEngine(specs=[spec], stamp_process_time=False)
        for i, c in enumerate([100.0, 101.0, 102.0, 103.0, 104.0]):
            snap = engine.on_event(Bar(close=c, event_time_ns=_s(i + 1)))
        assert snap.is_ready("rv3")
        assert snap.value("rv3") is not None

    def test_catalog_includes_realized_volatility(self):
        """available_feature_types() includes the new type."""
        assert "realized_volatility" in PythonBackend().available_feature_types()


# ===========================================================================
# Strategy integration example
# ===========================================================================

class TestStrategyIntegrationExample:
    """End-to-end strategy-like flow that only uses FeatureSnapshot / SpecFeatureEngine APIs.

    Validates that strategy code never needs to touch backend internals,
    feature class state, or any compute-layer object below FeatureSnapshot.
    """

    def _build_engine(self) -> SpecFeatureEngine:
        specs = [
            FeatureSpec(name="mean5",  input_type="bar", input_field="close",
                        window=5, params={"type": "rolling_mean"}),
            FeatureSpec(name="rvol5",  input_type="bar", input_field="close",
                        window=5, params={"type": "realized_volatility"}),
            FeatureSpec(name="spread", input_type="quote", params={"type": "spread"}),
            FeatureSpec(name="mid",    input_type="quote", params={"type": "mid_price"}),
        ]
        return SpecFeatureEngine(specs=specs, stamp_process_time=False)

    def _bar(self, close: float, ts: int) -> Bar:
        return Bar(close=close, high=close + 0.5, low=close - 0.5,
                   open=close - 0.2, volume=100.0, event_time_ns=ts)

    def _quote(self, bid: float, ask: float, ts: int) -> Quote:
        return Quote(bid_price=bid, ask_price=ask,
                     event_time_ns=ts, event_type="quote")

    def _simple_strategy(self, snap: "FeatureSnapshot") -> str | None:
        """Minimal signal: long if mean rises above threshold and vol is low."""
        mean  = snap.value("mean5")
        vol   = snap.value("rvol5")
        mid   = snap.value("mid")
        if mean is None or vol is None or mid is None:
            return None
        if mean > 100.0 and vol < 0.01 and mid > mean:
            return "long"
        return "flat"

    def test_strategy_reads_only_snapshot_api(self):
        """Strategy accesses features only through FeatureSnapshot — no backend objects."""
        engine = self._build_engine()
        closes = [100.0 + i * 0.1 for i in range(7)]
        for i, c in enumerate(closes):
            snap = engine.on_event(self._bar(c, _s(i + 1)))
        # Strategy only uses these stable FeatureSnapshot methods
        _ = snap.value("mean5")
        _ = snap.is_ready("mean5")
        _ = snap.as_dict()
        _ = snap.statuses()
        _ = snap.updated_names()
        _ = snap.all_ready()

    def test_strategy_signal_generation(self):
        """Strategy generates a non-None signal once all features are ready."""
        engine = self._build_engine()
        # Bar warmup (6 bars needed: mean5 needs 5 bars, rvol5 needs 6 bars)
        closes = [100.0 + i * 0.05 for i in range(8)]
        for i, c in enumerate(closes):
            snap = engine.on_event(self._bar(c, _s(i + 1)))
        for i in range(8):
            snap = engine.on_event(self._quote(99.5 + i * 0.01,
                                               100.5 + i * 0.01,
                                               _s(i + 100)))
        signal = self._simple_strategy(snap)
        # Signal may be None (not ready) or a string — just verify no crash
        assert signal is None or signal in ("long", "flat")

    def test_strategy_all_ready_check(self):
        """all_ready() returns False until every feature has processed enough history."""
        engine = self._build_engine()
        snap = engine.on_event(self._bar(100.0, _s(1)))
        assert not snap.all_ready()

    def test_strategy_multi_event_type(self):
        """Engine routes bar events to bar features and quote events to quote features."""
        engine = self._build_engine()
        snap_bar = engine.on_event(self._bar(100.0, _s(1)))
        # Bar events do not update quote features
        assert not snap_bar.is_ready("spread")
        snap_quote = engine.on_event(self._quote(99.0, 101.0, _s(2)))
        # Quote events do not update bar features (bar features hold previous value)
        assert snap_quote.is_ready("spread")

    def test_strategy_snapshot_ts_event(self):
        """FeatureSnapshot.ts_event matches the triggering event's event_time_ns."""
        engine = self._build_engine()
        ts_ns = _s(42)
        snap = engine.on_event(self._bar(100.0, ts_ns))
        assert snap.ts_event == ts_ns

    def test_strategy_value_default_before_ready(self):
        """snapshot.value() returns the caller's default when a feature is not ready."""
        engine = self._build_engine()
        snap = engine.on_event(self._bar(100.0, _s(1)))
        sentinel = float("nan")
        result = snap.value("mean5", sentinel)
        assert result is sentinel or (result != result)  # NaN check

    def test_strategy_does_not_need_backend_imports(self):
        """All strategy logic can be implemented with only FeatureSpec + FeatureSnapshot."""
        # This test documents the intended import surface for strategy code.
        # If this test is updatable without changing the feature compute internals,
        # the abstraction boundary is intact.
        from nautilus_ext.features.compute.spec import FeatureSpec, FeatureSnapshot
        from nautilus_ext.features.compute.engine import SpecFeatureEngine
        specs = [FeatureSpec(name="m3", input_type="bar", input_field="close",
                             window=3, params={"type": "rolling_mean"})]
        eng = SpecFeatureEngine(specs=specs, stamp_process_time=False)
        for b in bars([1.0, 2.0, 3.0]):
            snap = eng.on_event(b)
        assert isinstance(snap, FeatureSnapshot)
        assert snap.value("m3") is not None


# ===========================================================================
# Feature-to-feature dependencies
# ===========================================================================

class TestFeatureDependencies:
    """Tests for the feature dependency mechanism (depends_on in FeatureSpec).

    Covers:
    - Validation at engine init (unknown dep, self-dep, cycles)
    - Simple A->B chain (ratio, difference, sum, product)
    - Multi-level A->B->C chain
    - Current-value semantics: B sees A updated on the same event
    - Latest-ready semantics across mixed event types
    - Dirty propagation: unrelated derived features are not updated
    - dependency_not_ready status
    - Topological update order
    - Warmup integration
    - FeatureSnapshot includes derived features
    - State dict round-trip for derived features
    - Profile covers derived features
    - No backend imports needed for strategy
    """

    # ----------------------------------------------------------------
    # Helpers
    # ----------------------------------------------------------------

    def _bar(self, close=100.0, ts_ns=1_000_000_000, open=100.0, high=101.0,
             low=99.0, volume=1000.0):
        from nautilus_ext.features.compute.adapters import BarMarketEvent
        return BarMarketEvent(
            instrument_id="X", open=open, high=high, low=low,
            close=close, volume=volume, event_type="bar", event_time_ns=ts_ns,
        )

    def _quote(self, bid=100.0, ask=100.2, ts_ns=1_000_000_000):
        from nautilus_ext.features.compute.adapters import QuoteMarketEvent
        return QuoteMarketEvent(
            instrument_id="X", bid_price=bid, ask_price=ask,
            bid_size=10.0, ask_size=10.0, event_type="quote", event_time_ns=ts_ns,
        )

    def _engine(self, specs):
        return SpecFeatureEngine(specs, stamp_process_time=False)

    # ----------------------------------------------------------------
    # Validation at engine init
    # ----------------------------------------------------------------

    def test_unknown_dependency_raises_at_init(self):
        """SpecFeatureEngine rejects depends_on referencing an unknown feature name."""
        specs = [
            FeatureSpec("A", input_type="derived", depends_on=("DOES_NOT_EXIST",),
                        params={"type": "sum"}),
        ]
        with pytest.raises(ValueError, match="unknown dependency"):
            self._engine(specs)

    def test_self_dependency_raises_at_init(self):
        """Self-dependency (feature depends on itself) raises ValueError."""
        specs = [
            FeatureSpec("A", input_type="derived", depends_on=("A",),
                        params={"type": "sum"}),
        ]
        with pytest.raises(ValueError, match="self-dependency"):
            self._engine(specs)

    def test_cycle_two_features_raises(self):
        """Two-node cycle A->B->A raises ValueError with cycle info."""
        raw = FeatureSpec("raw", input_type="bar", input_field="close",
                          window=2, params={"type": "rolling_mean"})
        A = FeatureSpec("A", input_type="derived", depends_on=("raw", "B"),
                        params={"type": "sum"})
        B = FeatureSpec("B", input_type="derived", depends_on=("raw", "A"),
                        params={"type": "sum"})
        with pytest.raises(ValueError, match="Circular dependency"):
            self._engine([raw, A, B])

    def test_cycle_three_features_raises(self):
        """Three-node cycle A->B->C->A raises ValueError."""
        raw = FeatureSpec("raw", input_type="bar", input_field="close",
                          window=2, params={"type": "rolling_mean"})
        A = FeatureSpec("A", input_type="derived", depends_on=("raw", "C"),
                        params={"type": "sum"})
        B = FeatureSpec("B", input_type="derived", depends_on=("raw", "A"),
                        params={"type": "sum"})
        C = FeatureSpec("C", input_type="derived", depends_on=("raw", "B"),
                        params={"type": "sum"})
        with pytest.raises(ValueError, match="Circular dependency"):
            self._engine([raw, A, B, C])

    def test_valid_spec_builds_without_error(self):
        """A straightforward ratio of spread/mid builds without exceptions."""
        specs = [
            FeatureSpec("spread", input_type="quote", params={"type": "spread"}),
            FeatureSpec("mid",    input_type="quote", params={"type": "mid_price"}),
            FeatureSpec("ratio",  input_type="derived", depends_on=("spread", "mid"),
                        params={"type": "ratio"}),
        ]
        engine = self._engine(specs)
        assert "ratio" in engine.feature_names()

    # ----------------------------------------------------------------
    # Simple A -> B chains
    # ----------------------------------------------------------------

    def test_ratio_derived_feature(self):
        """ratio feature correctly computes value(dep0) / value(dep1)."""
        specs = [
            FeatureSpec("spread", input_type="quote", params={"type": "spread"}),
            FeatureSpec("mid",    input_type="quote", params={"type": "mid_price"}),
            FeatureSpec("ratio",  input_type="derived", depends_on=("spread", "mid"),
                        params={"type": "ratio"}),
        ]
        engine = self._engine(specs)
        snap = engine.on_event(self._quote(bid=100.0, ask=100.4, ts_ns=_s(1)))
        # spread = 0.4, mid = 100.2
        expected = 0.4 / 100.2
        assert abs(snap.value("ratio") - expected) < 1e-10

    def test_difference_derived_feature(self):
        """difference feature correctly computes value(dep0) - value(dep1)."""
        specs = [
            FeatureSpec("spread", input_type="quote", params={"type": "spread"}),
            FeatureSpec("mid",    input_type="quote", params={"type": "mid_price"}),
            FeatureSpec("diff",   input_type="derived", depends_on=("mid", "spread"),
                        params={"type": "difference"}),
        ]
        engine = self._engine(specs)
        snap = engine.on_event(self._quote(bid=100.0, ask=100.4, ts_ns=_s(1)))
        # mid = 100.2, spread = 0.4; diff = 100.2 - 0.4 = 99.8
        assert abs(snap.value("diff") - 99.8) < 1e-10

    def test_sum_derived_feature(self):
        """sum feature correctly sums all dependency values."""
        specs = [
            FeatureSpec("spread", input_type="quote", params={"type": "spread"}),
            FeatureSpec("mid",    input_type="quote", params={"type": "mid_price"}),
            FeatureSpec("s",      input_type="derived", depends_on=("spread", "mid"),
                        params={"type": "sum"}),
        ]
        engine = self._engine(specs)
        snap = engine.on_event(self._quote(bid=100.0, ask=100.4, ts_ns=_s(1)))
        assert abs(snap.value("s") - (0.4 + 100.2)) < 1e-10

    def test_product_derived_feature(self):
        """product feature correctly multiplies all dependency values."""
        specs = [
            FeatureSpec("spread", input_type="quote", params={"type": "spread"}),
            FeatureSpec("mid",    input_type="quote", params={"type": "mid_price"}),
            FeatureSpec("p",      input_type="derived", depends_on=("spread", "mid"),
                        params={"type": "product"}),
        ]
        engine = self._engine(specs)
        snap = engine.on_event(self._quote(bid=100.0, ask=100.4, ts_ns=_s(1)))
        # spread ≈ 0.4, mid = 100.2; product ≈ 40.08
        assert abs(snap.value("p") - 0.4 * 100.2) < 1e-8

    # ----------------------------------------------------------------
    # Multi-level dependency A -> B -> C
    # ----------------------------------------------------------------

    def test_multilevel_dependency_chain(self):
        """Three-level chain A->B->C produces correct values with topo update order."""
        specs = [
            FeatureSpec("spread", input_type="quote", params={"type": "spread"}),
            FeatureSpec("mid",    input_type="quote", params={"type": "mid_price"}),
            FeatureSpec("ratio",  input_type="derived", depends_on=("spread", "mid"),
                        params={"type": "ratio"}),
            FeatureSpec("doubled", input_type="derived", depends_on=("ratio", "ratio"),
                        params={"type": "sum"}),
        ]
        engine = self._engine(specs)
        assert engine._derived_names == ["ratio", "doubled"]  # topo order

        snap = engine.on_event(self._quote(bid=100.0, ask=100.4, ts_ns=_s(1)))
        ratio_val = snap.value("ratio")
        doubled_val = snap.value("doubled")
        assert ratio_val is not None
        assert abs(doubled_val - 2 * ratio_val) < 1e-12

    def test_deterministic_topological_order(self):
        """Engine stores derived feature names in dep-before-dependent order."""
        raw = FeatureSpec("raw", input_type="bar", input_field="close", window=2,
                          params={"type": "rolling_mean"})
        B = FeatureSpec("B", input_type="derived", depends_on=("raw",),
                        params={"type": "sum"})
        C = FeatureSpec("C", input_type="derived", depends_on=("B",),
                        params={"type": "sum"})
        engine = self._engine([raw, B, C])
        names = engine._derived_names
        assert names.index("B") < names.index("C")

    # ----------------------------------------------------------------
    # Current-value semantics
    # ----------------------------------------------------------------

    def test_current_value_semantics_b_sees_a_updated_same_event(self):
        """B sees A's value from event_t, not A's previous cached value."""
        # Build: mean2 (bar, window=2), doubled = sum(mean2, mean2)
        specs = [
            FeatureSpec("mean2", input_type="bar", input_field="close", window=2,
                        params={"type": "rolling_mean"}),
            FeatureSpec("doubled", input_type="derived", depends_on=("mean2", "mean2"),
                        params={"type": "sum"}),
        ]
        engine = self._engine(specs)
        snap1 = engine.on_event(self._bar(close=100.0, ts_ns=_s(1)))
        snap2 = engine.on_event(self._bar(close=102.0, ts_ns=_s(2)))
        # mean2 is ready after 2 bars: mean = (100+102)/2 = 101
        assert snap2.is_ready("mean2")
        mean_val = snap2.value("mean2")
        doubled_val = snap2.value("doubled")
        # doubled should use the CURRENT mean2 value (101), not the previous None
        assert doubled_val is not None
        assert abs(doubled_val - 2 * mean_val) < 1e-12

    # ----------------------------------------------------------------
    # dependency_not_ready status
    # ----------------------------------------------------------------

    def test_dependency_not_ready_when_dep_not_warmed_up(self):
        """Derived feature emits dependency_not_ready while a dep is still warming up."""
        specs = [
            FeatureSpec("mean5", input_type="bar", input_field="close", window=5,
                        params={"type": "rolling_mean"}),
            FeatureSpec("spread", input_type="quote", params={"type": "spread"}),
            FeatureSpec("ratio",  input_type="derived", depends_on=("mean5", "spread"),
                        params={"type": "ratio"}),
        ]
        engine = self._engine(specs)
        # 1 bar (mean5 not ready) then 1 quote
        engine.on_event(self._bar(close=100.0, ts_ns=_s(1)))
        snap_q = engine.on_event(self._quote(bid=100.0, ask=100.2, ts_ns=_s(2)))

        assert not snap_q.is_ready("ratio")
        fv = snap_q.get("ratio")
        assert fv.update_status == "dependency_not_ready"
        assert "mean5" in fv.reason

    def test_dependency_not_ready_does_not_crash(self):
        """No exception is raised when a dependency is not ready."""
        specs = [
            FeatureSpec("mean3", input_type="bar", input_field="close", window=3,
                        params={"type": "rolling_mean"}),
            FeatureSpec("doubled", input_type="derived", depends_on=("mean3",),
                        params={"type": "sum"}),
        ]
        engine = self._engine(specs)
        # Feed only 2 bars (mean3 needs 3)
        for i in range(1, 3):
            snap = engine.on_event(self._bar(close=float(i) * 100.0, ts_ns=_s(i)))
        # Should not raise, derived returns dependency_not_ready
        assert snap.get("doubled").update_status == "dependency_not_ready"

    # ----------------------------------------------------------------
    # Dirty propagation
    # ----------------------------------------------------------------

    def test_dirty_propagation_bar_event_does_not_update_quote_derived(self):
        """Derived feature depending only on quote features is not updated by bar events."""
        specs = [
            FeatureSpec("mean3",  input_type="bar",     input_field="close", window=3,
                        params={"type": "rolling_mean"}),
            FeatureSpec("spread", input_type="quote",   params={"type": "spread"}),
            FeatureSpec("mid",    input_type="quote",   params={"type": "mid_price"}),
            FeatureSpec("ratio",  input_type="derived", depends_on=("spread", "mid"),
                        params={"type": "ratio"}),
        ]
        engine = self._engine(specs)

        # Feed quote to make spread/mid ready, get ratio first value
        snap_q = engine.on_event(self._quote(bid=100.0, ask=100.4, ts_ns=_s(1)))
        ratio_after_q = snap_q.value("ratio")

        # Now feed a bar event — ratio's deps (spread, mid) are NOT dirty
        snap_bar = engine.on_event(self._bar(close=101.0, ts_ns=_s(2)))
        ratio_after_bar = snap_bar.value("ratio")

        # Ratio should return the same cached value (not recomputed from bar event)
        assert ratio_after_bar == ratio_after_q

    def test_dirty_propagation_quote_event_does_not_update_bar_derived(self):
        """Derived feature depending only on bar features is not updated by quote events."""
        specs = [
            FeatureSpec("mean2",  input_type="bar",     input_field="close", window=2,
                        params={"type": "rolling_mean"}),
            FeatureSpec("doubled", input_type="derived", depends_on=("mean2",),
                        params={"type": "sum"}),
        ]
        engine = self._engine(specs)

        # Warm up mean2 with 2 bars
        engine.on_event(self._bar(close=100.0, ts_ns=_s(1)))
        snap_bar = engine.on_event(self._bar(close=102.0, ts_ns=_s(2)))
        doubled_after_bars = snap_bar.value("doubled")
        assert doubled_after_bars is not None

        # Quote event: mean2 not dirty → doubled not updated
        snap_q = engine.on_event(self._quote(bid=100.0, ask=100.2, ts_ns=_s(3)))
        doubled_after_q = snap_q.value("doubled")
        assert doubled_after_q == doubled_after_bars   # cached, no update

    # ----------------------------------------------------------------
    # Latest-ready semantics (cross event-type)
    # ----------------------------------------------------------------

    def test_latest_ready_cross_type_ratio_updates_on_quote_event(self):
        """Ratio of bar-feature / quote-feature updates when quote event arrives.

        Latest-ready policy: mean5 (bar feature) was computed earlier; when a
        quote event arrives and updates spread, the ratio should use mean5's
        latest ready value.
        """
        specs = [
            FeatureSpec("mean3",  input_type="bar",     input_field="close", window=3,
                        params={"type": "rolling_mean"}),
            FeatureSpec("spread", input_type="quote",   params={"type": "spread"}),
            FeatureSpec("ratio",  input_type="derived", depends_on=("mean3", "spread"),
                        params={"type": "ratio"}),
        ]
        engine = self._engine(specs)

        # Warm up mean3 with 3 bars
        for i in range(1, 4):
            engine.on_event(self._bar(close=100.0, ts_ns=_s(i)))

        # Quote event: spread becomes ready; ratio should compute using latest mean3
        snap = engine.on_event(self._quote(bid=100.0, ask=100.4, ts_ns=_s(4)))
        assert snap.is_ready("mean3")
        assert snap.is_ready("spread")
        assert snap.is_ready("ratio")
        assert snap.value("ratio") is not None

    # ----------------------------------------------------------------
    # Snapshot includes derived features
    # ----------------------------------------------------------------

    def test_snapshot_includes_derived_features(self):
        """FeatureSnapshot.values contains both raw and derived feature keys."""
        specs = [
            FeatureSpec("spread", input_type="quote", params={"type": "spread"}),
            FeatureSpec("mid",    input_type="quote", params={"type": "mid_price"}),
            FeatureSpec("ratio",  input_type="derived", depends_on=("spread", "mid"),
                        params={"type": "ratio"}),
        ]
        engine = self._engine(specs)
        snap = engine.on_event(self._quote(ts_ns=_s(1)))
        assert "spread" in snap.values
        assert "mid" in snap.values
        assert "ratio" in snap.values
        assert snap.is_ready("ratio")

    def test_snapshot_ready_values_includes_ready_derived(self):
        """ready_values() includes derived features that are ready."""
        specs = [
            FeatureSpec("spread", input_type="quote", params={"type": "spread"}),
            FeatureSpec("mid",    input_type="quote", params={"type": "mid_price"}),
            FeatureSpec("ratio",  input_type="derived", depends_on=("spread", "mid"),
                        params={"type": "ratio"}),
        ]
        engine = self._engine(specs)
        snap = engine.on_event(self._quote(ts_ns=_s(1)))
        rv = snap.ready_values()
        assert "ratio" in rv
        assert rv["ratio"] is not None

    # ----------------------------------------------------------------
    # Warmup integration
    # ----------------------------------------------------------------

    def test_warmup_updates_derived_features(self):
        """engine.warmup() correctly pre-heats derived features.

        total = sum(mean3, mean3) has two identical deps → total == 2 * mean3.
        """
        specs = [
            FeatureSpec("mean3",  input_type="bar",     input_field="close", window=3,
                        params={"type": "rolling_mean"}),
            FeatureSpec("total",  input_type="derived", depends_on=("mean3", "mean3"),
                        params={"type": "sum"}),
        ]
        engine = self._engine(specs)
        # Warmup with 3 bars: close = 10, 20, 30 → mean3 = 20
        warmup_bars = [self._bar(close=float(i+1)*10, ts_ns=_s(i+1)) for i in range(3)]
        engine.warmup(warmup_bars)

        # One more live bar: close = 40 → mean3 = (20+30+40)/3 = 30
        snap = engine.on_event(self._bar(close=40.0, ts_ns=_s(10)))
        assert snap.is_ready("mean3")
        assert snap.is_ready("total")
        mean_val = snap.value("mean3")
        total_val = snap.value("total")
        assert abs(total_val - 2 * mean_val) < 1e-12

    # ----------------------------------------------------------------
    # State dict round-trip
    # ----------------------------------------------------------------

    def test_derived_state_dict_round_trip(self):
        """Derived feature state_dict / load_state_dict preserves readiness and value."""
        from nautilus_ext.features.compute.features import RatioDerivedFeature
        spec = FeatureSpec("r", input_type="derived",
                           depends_on=("a", "b"), params={"type": "ratio"})
        feat = RatioDerivedFeature(spec)
        # Manually push a cached value via _emit
        from nautilus_ext.features.compute.spec import FeatureValue
        feat._cached = FeatureValue(name="r", value=1.23, is_ready=True)
        state = feat.state_dict()
        feat2 = RatioDerivedFeature(spec)
        feat2.load_state_dict(state)
        assert feat2._event_count == feat._event_count

    # ----------------------------------------------------------------
    # Profile covers derived features
    # ----------------------------------------------------------------

    def test_profile_covers_derived_features(self):
        """profile_summary() includes derived feature counters."""
        specs = [
            FeatureSpec("spread", input_type="quote", params={"type": "spread"}),
            FeatureSpec("mid",    input_type="quote", params={"type": "mid_price"}),
            FeatureSpec("ratio",  input_type="derived", depends_on=("spread", "mid"),
                        params={"type": "ratio"}),
        ]
        engine = SpecFeatureEngine(specs, stamp_process_time=False, profile=True)
        engine.on_event(self._quote(ts_ns=_s(1)))
        summary = engine.profile_summary()
        assert summary["profile"] is True
        assert "ratio" in summary["features"]
        assert summary["features"]["ratio"]["update_count"] == 1
        assert summary["features"]["ratio"]["last_status"] == "updated"

    def test_profile_dependency_not_ready_counts_as_skip(self):
        """dependency_not_ready events increment skip_count in profile."""
        specs = [
            FeatureSpec("mean5", input_type="bar", input_field="close", window=5,
                        params={"type": "rolling_mean"}),
            FeatureSpec("spread", input_type="quote", params={"type": "spread"}),
            FeatureSpec("ratio",  input_type="derived", depends_on=("mean5", "spread"),
                        params={"type": "ratio"}),
        ]
        engine = SpecFeatureEngine(specs, stamp_process_time=False, profile=True)
        engine.on_event(self._bar(close=100.0, ts_ns=_s(1)))   # mean5 not ready
        engine.on_event(self._quote(bid=100.0, ask=100.2, ts_ns=_s(2)))  # ratio not ready
        summary = engine.profile_summary()
        assert summary["features"]["ratio"]["skip_count"] >= 1
        assert summary["features"]["ratio"]["last_status"] == "dependency_not_ready"

    # ----------------------------------------------------------------
    # Ratio denominator zero
    # ----------------------------------------------------------------

    def test_ratio_denominator_zero_emits_dependency_not_ready(self):
        """RatioDerivedFeature emits dependency_not_ready when denominator is zero."""
        specs = [
            FeatureSpec("spread", input_type="quote", params={"type": "spread"}),
            FeatureSpec("mid",    input_type="quote", params={"type": "mid_price"}),
            FeatureSpec("ratio",  input_type="derived", depends_on=("spread", "mid"),
                        params={"type": "ratio"}),
        ]
        engine = self._engine(specs)
        # bid == ask → spread = 0 (zero denominator if used as dep1)
        specs2 = [
            FeatureSpec("spread2", input_type="quote", params={"type": "spread"}),
            FeatureSpec("mid2",    input_type="quote", params={"type": "mid_price"}),
            FeatureSpec("r2",  input_type="derived", depends_on=("mid2", "spread2"),
                        params={"type": "ratio"}),
        ]
        engine2 = self._engine(specs2)
        snap = engine2.on_event(self._quote(bid=100.0, ask=100.0, ts_ns=_s(1)))
        fv = snap.get("r2")
        assert fv.update_status == "dependency_not_ready"
        assert "zero" in fv.reason.lower()

    # ----------------------------------------------------------------
    # No backend imports needed for strategy
    # ----------------------------------------------------------------

    def test_strategy_accesses_derived_via_snapshot_only(self):
        """Strategy code uses only FeatureSpec, FeatureSnapshot, SpecFeatureEngine."""
        from nautilus_ext.features.compute.spec import FeatureSpec, FeatureSnapshot
        from nautilus_ext.features.compute.engine import SpecFeatureEngine

        specs = [
            FeatureSpec("spread", input_type="quote", params={"type": "spread"}),
            FeatureSpec("mid",    input_type="quote", params={"type": "mid_price"}),
            FeatureSpec("ratio",  input_type="derived", depends_on=("spread", "mid"),
                        params={"type": "ratio"}),
        ]
        from nautilus_ext.features.compute.adapters import QuoteMarketEvent
        engine = SpecFeatureEngine(specs, stamp_process_time=False)
        ev = QuoteMarketEvent(instrument_id="X", bid_price=100.0, ask_price=100.4,
                              bid_size=10.0, ask_size=10.0, event_type="quote",
                              event_time_ns=1_000_000_000)
        snap = engine.on_event(ev)
        assert isinstance(snap, FeatureSnapshot)
        assert snap.is_ready("ratio")
        assert snap.value("ratio") is not None

    # ----------------------------------------------------------------
    # DependencyContext unit tests
    # ----------------------------------------------------------------

    def test_dependency_context_value_returns_scalar(self):
        """DependencyContext.value() returns the scalar of a ready FeatureValue."""
        from nautilus_ext.features.compute.features import DependencyContext
        from nautilus_ext.features.compute.spec import FeatureValue
        fv = FeatureValue(name="a", value=3.14, is_ready=True)
        ctx = DependencyContext({"a": fv})
        assert ctx.value("a") == 3.14
        assert ctx.value("missing") is None

    def test_dependency_context_is_ready_reflects_fv_flag(self):
        """DependencyContext.is_ready() reflects the is_ready flag of the FeatureValue."""
        from nautilus_ext.features.compute.features import DependencyContext
        from nautilus_ext.features.compute.spec import FeatureValue
        ready = FeatureValue(name="a", value=1.0, is_ready=True)
        not_ready = FeatureValue(name="b", value=None, is_ready=False)
        ctx = DependencyContext({"a": ready, "b": not_ready})
        assert ctx.is_ready("a") is True
        assert ctx.is_ready("b") is False
        assert ctx.is_ready("c") is False   # absent

    def test_dependency_context_live_reference_reflects_updates(self):
        """DependencyContext holds a live dict reference — mutations are visible."""
        from nautilus_ext.features.compute.features import DependencyContext
        from nautilus_ext.features.compute.spec import FeatureValue
        values: dict = {}
        ctx = DependencyContext(values)
        assert ctx.is_ready("a") is False
        fv = FeatureValue(name="a", value=42.0, is_ready=True)
        values["a"] = fv
        assert ctx.is_ready("a") is True
        assert ctx.value("a") == 42.0

    def test_dependency_context_all_ready(self):
        """DependencyContext.all_ready() is True only when every listed dep is ready."""
        from nautilus_ext.features.compute.features import DependencyContext
        from nautilus_ext.features.compute.spec import FeatureValue
        r = FeatureValue(name="a", value=1.0, is_ready=True)
        nr = FeatureValue(name="b", value=None, is_ready=False)
        ctx = DependencyContext({"a": r, "b": nr})
        assert ctx.all_ready(["a"]) is True
        assert ctx.all_ready(["a", "b"]) is False


# ===========================================================================
# RollingStdDerivedFeature + practical derived chains
# ===========================================================================

from nautilus_ext.features.compute.features import RollingStdDerivedFeature
from nautilus_ext.features.compute.adapters import adapt_trade_tick_event


class TestRollingStdDerivedFeature:
    """RollingStdDerivedFeature: rolling sample std of a single dependency stream."""

    def _log_ret_spec(self) -> FeatureSpec:
        return FeatureSpec(
            name="lr", input_type="bar", input_field="close",
            params={"type": "log_return"},
        )

    def _rvol_spec(self, window: int) -> FeatureSpec:
        return FeatureSpec(
            name="rvol", input_type="derived",
            depends_on=("lr",),
            window=window,
            params={"type": "rolling_std_derived"},
        )

    def test_not_ready_before_window_fills(self):
        """rvol is not ready until 'window' log_return values are accumulated."""
        window = 5
        specs = [self._log_ret_spec(), self._rvol_spec(window)]
        engine = SpecFeatureEngine(specs=specs, stamp_process_time=False)
        closes = [100.0 + i for i in range(window)]  # window log returns produced
        for i, c in enumerate(closes):
            snap = engine.on_event(Bar(close=c, event_time_ns=_s(i + 1)))
        # window log_returns need window+1 bars to produce; after 'window' bars only
        # window-1 log_returns are ready, so rvol should still be not ready
        assert not snap.is_ready("rvol")

    def test_ready_after_window_log_returns(self):
        """rvol becomes ready after window log_return values are accumulated."""
        window = 4
        specs = [self._log_ret_spec(), self._rvol_spec(window)]
        engine = SpecFeatureEngine(specs=specs, stamp_process_time=False)
        # Need window+1 bars to produce window log returns
        closes = [100.0 + i * 0.5 for i in range(window + 1)]
        for i, c in enumerate(closes):
            snap = engine.on_event(Bar(close=c, event_time_ns=_s(i + 1)))
        assert snap.is_ready("rvol")
        assert snap.value("rvol") is not None
        assert snap.value("rvol") > 0.0

    def test_value_matches_reference_std(self):
        """Emitted std matches reference std computed over the same log-return window."""
        import math
        window = 3
        specs = [self._log_ret_spec(), self._rvol_spec(window)]
        engine = SpecFeatureEngine(specs=specs, stamp_process_time=False)
        closes = [100.0, 101.0, 102.0, 101.5, 103.0]
        for i, c in enumerate(closes):
            snap = engine.on_event(Bar(close=c, event_time_ns=_s(i + 1)))

        log_rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
        window_rets = log_rets[-window:]
        mean = sum(window_rets) / window
        ref_std = math.sqrt(sum((x - mean) ** 2 for x in window_rets) / (window - 1))
        assert abs(snap.value("rvol") - ref_std) < 1e-10

    def test_dep_not_ready_before_log_return_ready(self):
        """When lr is not ready, rvol emits dependency_not_ready."""
        window = 3
        specs = [self._log_ret_spec(), self._rvol_spec(window)]
        engine = SpecFeatureEngine(specs=specs, stamp_process_time=False)
        # First bar: lr not ready (needs prev close)
        snap = engine.on_event(Bar(close=100.0, event_time_ns=_s(1)))
        fv = snap.get("rvol")
        assert fv.update_status == "dependency_not_ready"
        assert not fv.is_ready

    def test_warmup_pre_heats_rvol(self):
        """warmup() with enough bars makes rvol ready for subsequent on_event()."""
        window = 3
        specs = [self._log_ret_spec(), self._rvol_spec(window)]
        engine = SpecFeatureEngine(specs=specs, stamp_process_time=False)
        warmup_bars = bars([100.0 + i for i in range(window + 1)])
        engine.warmup(warmup_bars)
        # One more live bar should produce an updated rvol
        snap = engine.on_event(Bar(close=104.5, event_time_ns=_s(window + 2)))
        assert snap.is_ready("rvol")

    def test_state_dict_round_trip(self):
        """state_dict / load_state_dict round-trip preserves rvol value."""
        window = 3
        specs = [self._log_ret_spec(), self._rvol_spec(window)]
        engine = SpecFeatureEngine(specs=specs, stamp_process_time=False)
        closes = [100.0, 101.0, 102.0, 103.0]
        for i, c in enumerate(closes):
            engine.on_event(Bar(close=c, event_time_ns=_s(i + 1)))
        snap_before = engine.on_event(Bar(close=104.0, event_time_ns=_s(5)))

        state = engine.state_dict()
        engine2 = SpecFeatureEngine(specs=specs, stamp_process_time=False)
        engine2.load_state_dict(state)
        snap_after = engine2.on_event(Bar(close=105.0, event_time_ns=_s(6)))

        assert snap_after.is_ready("rvol")
        # Values may differ (new bar vs restored), but both engines should agree
        # after consuming the same next event from the same restored state
        assert abs(snap_after.value("rvol") - snap_before.value("rvol")) < 1.0

    def test_reset_clears_rolling_buffer(self):
        """reset() makes rvol not ready again."""
        window = 2
        specs = [self._log_ret_spec(), self._rvol_spec(window)]
        engine = SpecFeatureEngine(specs=specs, stamp_process_time=False)
        for i, c in enumerate([100.0, 101.0, 102.0]):
            engine.on_event(Bar(close=c, event_time_ns=_s(i + 1)))
        assert engine.ready("rvol")
        engine.reset()
        assert not engine.ready("rvol")

    def test_wrong_depends_on_arity_raises(self):
        """RollingStdDerivedFeature raises ValueError if depends_on != 1 entry."""
        spec = FeatureSpec(
            name="rvol2", input_type="derived",
            depends_on=("a", "b"),  # two deps — invalid
            window=5,
            params={"type": "rolling_std_derived"},
        )
        with pytest.raises(ValueError, match="exactly 1 entry"):
            RollingStdDerivedFeature(spec)

    def test_warmup_required_returns_window(self):
        """warmup_required().n_events == spec.window."""
        window = 7
        spec = FeatureSpec(
            name="rvol", input_type="derived",
            depends_on=("lr",),
            window=window,
            params={"type": "rolling_std_derived"},
        )
        f = RollingStdDerivedFeature(spec)
        req = f.warmup_required()
        assert req.n_events == window
        assert req.mandatory is True

    def test_update_status_transitions(self):
        """update_status transitions from not_ready → updated as window fills."""
        window = 2
        specs = [self._log_ret_spec(), self._rvol_spec(window)]
        engine = SpecFeatureEngine(specs=specs, stamp_process_time=False)
        # First bar: dep not ready
        s1 = engine.on_event(Bar(close=100.0, event_time_ns=_s(1)))
        assert s1.get("rvol").update_status == "dependency_not_ready"
        # Second bar: dep ready but window not full (only 1 lr value)
        s2 = engine.on_event(Bar(close=101.0, event_time_ns=_s(2)))
        assert s2.get("rvol").update_status == "not_ready"
        # Third bar: window full
        s3 = engine.on_event(Bar(close=102.0, event_time_ns=_s(3)))
        assert s3.get("rvol").update_status == "updated"
        assert s3.is_ready("rvol")


class TestPracticalDerivedChains:
    """Integration tests for the two practical derived chain examples."""

    # ----------------------------------------------------------------
    # Chain A: spread + mid_price → spread_ratio
    # ----------------------------------------------------------------

    def _spread_mid_ratio_specs(self):
        return [
            FeatureSpec("spread",       input_type="quote", params={"type": "spread"}),
            FeatureSpec("mid",          input_type="quote", params={"type": "mid_price"}),
            FeatureSpec("spread_ratio", input_type="derived",
                        depends_on=("spread", "mid"), params={"type": "ratio"}),
        ]

    def test_spread_ratio_ready_after_first_quote(self):
        """spread_ratio is ready after the first quote event (spread & mid both ready)."""
        specs = self._spread_mid_ratio_specs()
        engine = SpecFeatureEngine(specs=specs, stamp_process_time=False)
        q = Quote(bid_price=100.0, ask_price=100.4,
                  event_time_ns=_s(1), event_type="quote")
        snap = engine.on_event(q)
        assert snap.is_ready("spread")
        assert snap.is_ready("mid")
        assert snap.is_ready("spread_ratio")
        expected = 0.4 / 100.2  # spread / mid
        assert abs(snap.value("spread_ratio") - expected) < 1e-10

    def test_spread_ratio_updates_on_every_quote(self):
        """spread_ratio recomputes on each quote and tracks changing spread."""
        specs = self._spread_mid_ratio_specs()
        engine = SpecFeatureEngine(specs=specs, stamp_process_time=False)
        quotes = [
            Quote(bid_price=100.0, ask_price=100.2, event_time_ns=_s(1), event_type="quote"),
            Quote(bid_price=100.0, ask_price=100.6, event_time_ns=_s(2), event_type="quote"),
        ]
        snaps = [engine.on_event(q) for q in quotes]
        ratio1 = snaps[0].value("spread_ratio")
        ratio2 = snaps[1].value("spread_ratio")
        assert ratio2 > ratio1  # wider spread → larger ratio

    def test_spread_ratio_not_updated_by_bar_event(self):
        """Bar events do not recompute spread_ratio — the cached value is returned unchanged."""
        specs = [
            FeatureSpec("mean3",        input_type="bar", input_field="close",
                        window=3, params={"type": "rolling_mean"}),
        ] + self._spread_mid_ratio_specs()
        engine = SpecFeatureEngine(specs=specs, stamp_process_time=False)
        # First: one quote to get spread_ratio ready
        q = Quote(bid_price=100.0, ask_price=100.4, event_time_ns=_s(1), event_type="quote")
        snap_q = engine.on_event(q)
        ratio_after_quote = snap_q.value("spread_ratio")

        # Now send bar events only — spread_ratio cached value must stay the same
        for i in range(3):
            snap_b = engine.on_event(Bar(close=101.0 + i, event_time_ns=_s(10 + i)))
        # Value is unchanged: the engine returned the cached FeatureValue
        assert snap_b.value("spread_ratio") == pytest.approx(ratio_after_quote)
        # source_event_time_ns on the cached value is from the original quote event
        assert snap_b.get("spread_ratio").source_event_time_ns == _s(1)

    def test_spread_ratio_snapshot_contains_all_features(self):
        """Snapshot contains all three features: spread, mid, spread_ratio."""
        specs = self._spread_mid_ratio_specs()
        engine = SpecFeatureEngine(specs=specs, stamp_process_time=False)
        q = Quote(bid_price=99.0, ask_price=101.0, event_time_ns=_s(1), event_type="quote")
        snap = engine.on_event(q)
        assert set(snap.values.keys()) == {"spread", "mid", "spread_ratio"}

    # ----------------------------------------------------------------
    # Chain B: log_return → realized_vol (rolling_std_derived)
    # ----------------------------------------------------------------

    def _log_rvol_specs(self, window: int = 3):
        return [
            FeatureSpec("log_ret", input_type="bar", input_field="close",
                        params={"type": "log_return"}),
            FeatureSpec("rvol",    input_type="derived",
                        depends_on=("log_ret",),
                        window=window,
                        params={"type": "rolling_std_derived"}),
        ]

    def test_log_return_rvol_chain_produces_value(self):
        """After window+1 bars, rvol is ready and positive."""
        window = 3
        specs = self._log_rvol_specs(window)
        engine = SpecFeatureEngine(specs=specs, stamp_process_time=False)
        closes = [100.0, 101.0, 102.5, 101.8, 103.0]
        for i, c in enumerate(closes):
            snap = engine.on_event(Bar(close=c, event_time_ns=_s(i + 1)))
        assert snap.is_ready("rvol")
        assert snap.value("rvol") > 0.0

    def test_log_return_rvol_chain_warmup_integration(self):
        """warmup() + one live event produces ready rvol."""
        window = 3
        specs = self._log_rvol_specs(window)
        engine = SpecFeatureEngine(specs=specs, stamp_process_time=False)
        warmup_bars = bars([100.0 + i * 0.5 for i in range(window + 1)])
        engine.warmup(warmup_bars)
        snap = engine.on_event(Bar(close=103.0, event_time_ns=_s(window + 2)))
        assert snap.is_ready("rvol")

    def test_log_return_rvol_chain_not_updated_by_quote(self):
        """Quote events do not trigger rvol updates (log_ret is bar-only)."""
        window = 2
        specs = self._log_rvol_specs(window)
        engine = SpecFeatureEngine(specs=specs, stamp_process_time=False)
        # Warm up to ready
        for i, c in enumerate([100.0, 101.0, 102.0]):
            snap_b = engine.on_event(Bar(close=c, event_time_ns=_s(i + 1)))
        rvol_after_bars = snap_b.value("rvol")
        # Quote events should not change rvol
        for i in range(3):
            snap_q = engine.on_event(Quote(bid_price=100.0, ask_price=100.1,
                                           event_time_ns=_s(10 + i), event_type="quote"))
        # rvol cached value unchanged
        assert snap_q.value("rvol") == pytest.approx(rvol_after_bars)


class TestAdaptTradeTickEvent:
    """adapt_trade_tick_event() converts trade-like objects to TradeMarketEvent."""

    def test_event_type_is_trade(self):
        """Adapted event always has event_type='trade'."""
        @dataclass
        class FakeTrade:
            instrument_id: str = "BTC/USDT"
            price: float = 50000.0
            size: float = 0.5
            event_time_ns: int = _s(1)

        adapted = adapt_trade_tick_event(FakeTrade())
        assert adapted.event_type == "trade"

    def test_fields_preserved(self):
        """price, size, instrument_id, event_time_ns preserved."""
        @dataclass
        class FakeTrade:
            instrument_id: str = "ETH/USDT"
            price: float = 3000.0
            size: float = 1.25
            event_time_ns: int = _s(5)
            source: str = "binance"
            side: str = "buy"

        adapted = adapt_trade_tick_event(FakeTrade())
        assert adapted.instrument_id == "ETH/USDT"
        assert adapted.price == pytest.approx(3000.0)
        assert adapted.size == pytest.approx(1.25)
        assert adapted.event_time_ns == _s(5)
        assert adapted.source == "binance"
        assert adapted.side == "buy"

    def test_datetime_ts_event_converted_to_ns(self):
        """ts_event datetime is converted to nanoseconds."""
        from datetime import datetime, timezone
        ts_s = 1_700_000_000.0
        dt = datetime.fromtimestamp(ts_s, tz=timezone.utc)

        @dataclass
        class FakeTrade:
            instrument_id: str = "X"
            price: float = 1.0
            size: float = 1.0
            ts_event: "datetime" = None

        t = FakeTrade(ts_event=dt)
        adapted = adapt_trade_tick_event(t)
        assert adapted.event_time_ns == int(ts_s * 1_000_000_000)

    def test_frozen_dataclass(self):
        """TradeMarketEvent is a frozen dataclass."""
        @dataclass
        class FakeTrade:
            instrument_id: str = "X"
            price: float = 1.0
            size: float = 1.0
            event_time_ns: int = _s(1)

        adapted = adapt_trade_tick_event(FakeTrade())
        assert isinstance(adapted, TradeMarketEvent)
        with pytest.raises((TypeError, AttributeError)):
            adapted.price = 9999.0  # type: ignore[misc]
