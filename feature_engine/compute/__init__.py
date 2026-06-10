"""
feature_engine.compute — modular, incremental feature computation.

Public surface
--------------
Specs / values (stable, strategy-facing):
    FeatureSpec         — stable feature specification
    TriggerPolicy       — when to emit a new feature value
    WarmupRequirement   — historical data needed before a feature is ready
    FeatureValue        — one feature's value at one point in time
    FeatureUpdate       — result of FeatureBase.update()
    FeatureSnapshot     — all feature values at one point in time

Protocol:
    FeatureBase         — structural protocol for incremental features

Timestamps:
    TimestampConfig     — configures legacy ts_event unit and live strictness
    EventTimestamps     — three-field timestamp bundle (event/receive/process)
    extract_timestamps  — duck-typed extraction with configurable fallback
    select_timestamp    — selects field based on time_semantics string
    convert_legacy_ts_event_to_ns — explicit unit conversion helper

Watermark / stream identity:
    StreamKey           — (instrument_id, input_type, source) stream identity
    WatermarkTracker    — per-stream event-time progress tracker

Clock:
    Clock               — structural protocol (now_ns() -> int)
    SystemClock         — live clock backed by time.time_ns()
    ManualClock         — deterministic clock for tests and replay

State containers (for building custom features):
    RollingWindowState  — fixed ring buffer, O(1) sum/mean/variance/std
    TimeWindowState     — time-based sliding window with eviction
    EWMAState           — exponentially weighted moving average
    VWAPState           — volume-weighted average price state

Backend / registry:
    FeatureBackend      — protocol for feature creation backends
    BackendRegistry     — maps backend name → FeatureBackend
    PythonBackend       — pure-Python default backend
    build_default_registry — pre-loaded registry (python backend only)

Engines:
    SpecFeatureEngine       — standalone spec-driven engine; returns FeatureSnapshot
    SpecDrivenFeatureEngine — adapter implementing FeatureEngineBase for FeaturePipeline
    LateEventError          — raised by late_event_policy='raise'
    input_type_for_event    — canonical input_type from a market event (stable routing)

Example
-------
    from feature_engine.compute import (
        FeatureSpec, TriggerPolicy, SpecFeatureEngine, TimestampConfig
    )
    from feature_engine.compute.clock import ManualClock

    specs = [
        FeatureSpec(
            name="rolling_mean_close_20",
            input_type="bar",
            input_field="close",
            window=20,
            window_unit="bars",
            trigger=TriggerPolicy(kind="on_bar_close"),
        ),
    ]

    # Deterministic test
    clock = ManualClock(initial_ns=0)
    engine = SpecFeatureEngine(
        specs=specs,
        clock=clock,
        ts_config=TimestampConfig(require_event_time_ns_for_live=True),
    )
    engine.warmup(historical_bars)
    clock.set(1_000_000_000)           # fix process_time for reproducibility
    snapshot = engine.on_event(live_bar)
    mean_value = snapshot.scalar("rolling_mean_close_20")
"""
from feature_engine.compute.backend import (
    BackendRegistry,
    FeatureBackend,
    PythonBackend,
    build_default_registry,
)
from feature_engine.compute.clock import Clock, ManualClock, SystemClock
from feature_engine.compute.engine import (
    LateEventError,
    SpecDrivenFeatureEngine,
    SpecFeatureEngine,
    input_type_for_event,
)
from feature_engine.compute.feature_base import FeatureBase
from feature_engine.compute.spec import (
    FeatureSnapshot,
    FeatureSpec,
    FeatureUpdate,
    FeatureValue,
    TriggerPolicy,
    WarmupRequirement,
)
from feature_engine.compute.state import (
    EWMAState,
    RollingWindowState,
    TimeWindowState,
    VWAPState,
)
from feature_engine.compute.timestamps import (
    EventTimestamps,
    TimestampConfig,
    convert_legacy_ts_event_to_ns,
    extract_timestamps,
    select_timestamp,
)
from feature_engine.compute.watermark import StreamKey, WatermarkTracker

__all__ = [
    # Specs / values (stable, strategy-facing)
    "FeatureSpec",
    "TriggerPolicy",
    "WarmupRequirement",
    "FeatureValue",
    "FeatureUpdate",
    "FeatureSnapshot",
    # Protocol
    "FeatureBase",
    # Timestamps
    "TimestampConfig",
    "EventTimestamps",
    "extract_timestamps",
    "select_timestamp",
    "convert_legacy_ts_event_to_ns",
    # Watermark / stream identity
    "StreamKey",
    "WatermarkTracker",
    # Clock
    "Clock",
    "SystemClock",
    "ManualClock",
    # State containers
    "RollingWindowState",
    "TimeWindowState",
    "EWMAState",
    "VWAPState",
    # Backend
    "FeatureBackend",
    "BackendRegistry",
    "PythonBackend",
    "build_default_registry",
    # Engines
    "SpecFeatureEngine",
    "SpecDrivenFeatureEngine",
    "LateEventError",
    "input_type_for_event",
]
