"""
nautilus_ext.features.compute — modular, incremental feature computation.

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

Example
-------
    from nautilus_ext.features.compute import (
        FeatureSpec, TriggerPolicy, SpecFeatureEngine
    )
    from nautilus_ext.strategies.interfaces.input_types import BarInput

    specs = [
        FeatureSpec(
            name="rolling_mean_close_20",
            input_type="bar",
            input_field="close",
            window=20,
            window_unit="bars",
            trigger=TriggerPolicy(kind="on_bar_close"),
        ),
        FeatureSpec(
            name="vwap_50",
            input_type="bar",
            window=50,
            window_unit="bars",
            trigger=TriggerPolicy(kind="on_bar_close"),
        ),
    ]

    engine = SpecFeatureEngine(specs=specs)
    engine.warmup(historical_bars)            # pre-heat with history
    snapshot = engine.on_event(live_bar)      # hot path
    mean_value = snapshot.scalar("rolling_mean_close_20")
"""
from nautilus_ext.features.compute.backend import (
    BackendRegistry,
    FeatureBackend,
    PythonBackend,
    build_default_registry,
)
from nautilus_ext.features.compute.engine import SpecDrivenFeatureEngine, SpecFeatureEngine
from nautilus_ext.features.compute.feature_base import FeatureBase
from nautilus_ext.features.compute.spec import (
    FeatureSnapshot,
    FeatureSpec,
    FeatureUpdate,
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

__all__ = [
    # Specs / values
    "FeatureSpec",
    "TriggerPolicy",
    "WarmupRequirement",
    "FeatureValue",
    "FeatureUpdate",
    "FeatureSnapshot",
    # Protocol
    "FeatureBase",
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
]
