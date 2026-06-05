"""
SpecFeatureEngine and SpecDrivenFeatureEngine.

SpecFeatureEngine
    Standalone spec-driven engine.  Processes market events, maintains one
    WatermarkTracker per StreamKey (instrument × event-type × source), and
    applies per-feature late event policy before calling feature.update().
    Returns FeatureSnapshot with all three timestamps.

    Partitioned watermarks
    ----------------------
    A single global watermark is unsafe when the engine receives multiple
    instruments or multiple event types: a fast BTC/USDT bar stream would
    advance the watermark and incorrectly classify slower ETH/USDT quote
    events as late.

    The engine maintains:
        watermarks: dict[StreamKey, WatermarkTracker]

    Each event advances only the watermark for its own stream
    (instrument_id + input_type + optional source). The per-feature
    late-event check uses the watermark of the stream that matches the
    feature's input_type, so bar and quote features are checked
    independently even when they share the same engine instance.

    Late event policies (configured per feature via TriggerPolicy):
        "drop"                      — skip update(), return cached value
        "log_only"                  — log warning, skip update()
        "update_if_not_finalized"   — call update() anyway (state self-corrects
                                      for rolling windows with no fixed boundary)
        "recompute_for_backtest_only" — update during warmup; drop in live mode
        "raise"                     — raise LateEventError immediately

    Clock abstraction
    -----------------
    process_time_ns is stamped via an injected Clock rather than calling
    time.time_ns() directly.  Inject ManualClock for deterministic tests:

        from nautilus_ext.features.compute.clock import ManualClock
        clock = ManualClock(initial_ns=1_000_000_000)
        engine = SpecFeatureEngine(specs=specs, clock=clock)

    TimestampConfig
    ---------------
    Governs how legacy ts_event fields are converted to nanoseconds and
    whether missing event_time_ns raises in live mode.  Defaults are
    backward-compatible (ms legacy, no strict check).

SpecDrivenFeatureEngine
    Adapter that implements FeatureEngineBase, allowing SpecFeatureEngine
    to plug into the existing FeaturePipeline.
    Converts FeatureSnapshot (ns timestamps) to FeatureEvent (ms ts_event).
"""
from __future__ import annotations

import logging
from typing import Any, Iterable

from nautilus_ext.features.compute.backend import BackendRegistry, build_default_registry
from nautilus_ext.features.compute.clock import Clock, SystemClock
from nautilus_ext.features.compute.feature_base import FeatureBase
from nautilus_ext.features.compute.spec import FeatureSnapshot, FeatureSpec, FeatureValue
from nautilus_ext.features.compute.timestamps import (
    TimestampConfig,
    extract_timestamps,
    select_timestamp,
)
from nautilus_ext.features.compute.watermark import StreamKey, WatermarkTracker

log = logging.getLogger(__name__)

_EVENT_TYPE_MAP: dict[str, str] = {
    "bar": "bar",
    "trade_tick": "trade",
    "quote_tick": "quote",
    "orderbook": "book_delta",
    "funding_rate": "timer",
}


def _input_type_for(event: Any) -> str | None:
    et = getattr(event, "event_type", None)
    if et is None:
        return None
    return _EVENT_TYPE_MAP.get(et)


def _extract_instrument_id(event: Any) -> str | None:
    return getattr(event, "instrument_id", None)


# ---------------------------------------------------------------------------
# LateEventError
# ---------------------------------------------------------------------------

class LateEventError(RuntimeError):
    """Raised by late_event_policy='raise' when a late event is received.

    Attributes
    ----------
    feature_name : str
    trigger_ts_ns : int
        The event's trigger timestamp (based on time_semantics).
    watermark_ns : int
        The stream watermark at the time of the check.
    allowed_lateness_ns : int
        The feature's allowed_lateness_ns setting.
    """

    def __init__(
        self,
        feature_name: str,
        trigger_ts_ns: int,
        watermark_ns: int,
        allowed_lateness_ns: int,
    ) -> None:
        super().__init__(
            f"Late event for feature {feature_name!r}: "
            f"trigger_ts_ns={trigger_ts_ns} < watermark_ns={watermark_ns} "
            f"(allowed_lateness_ns={allowed_lateness_ns})"
        )
        self.feature_name = feature_name
        self.trigger_ts_ns = trigger_ts_ns
        self.watermark_ns = watermark_ns
        self.allowed_lateness_ns = allowed_lateness_ns


# ---------------------------------------------------------------------------
# SpecFeatureEngine
# ---------------------------------------------------------------------------

class SpecFeatureEngine:
    """Spec-driven incremental feature engine with full timestamp semantics.

    Parameters
    ----------
    specs : list[FeatureSpec]
        Feature specifications to register and build.
    backend_registry : BackendRegistry | None
        Registry for feature creation.  Defaults to the pure-Python backend.
    stamp_process_time : bool
        If True (default), stamp process_time_ns in FeatureSnapshot during
        on_event() using ``clock.now_ns()``.  Set False for deterministic
        tests or when process latency is not needed.
    clock : Clock | None
        Clock used to stamp process_time_ns.  Defaults to SystemClock
        (time.time_ns()).  Inject ManualClock for deterministic tests.
    ts_config : TimestampConfig | None
        Controls legacy ts_event unit conversion and live-mode strictness.
        Defaults to TimestampConfig() (ms legacy, no strict live check).
    """

    def __init__(
        self,
        specs: list[FeatureSpec],
        backend_registry: BackendRegistry | None = None,
        stamp_process_time: bool = True,
        clock: Clock | None = None,
        ts_config: TimestampConfig | None = None,
    ) -> None:
        self._specs: list[FeatureSpec] = list(specs)
        self._registry: BackendRegistry = backend_registry or build_default_registry()
        self._features: dict[str, FeatureBase] = {}
        self._watermarks: dict[StreamKey, WatermarkTracker] = {}
        self._stamp_process_time = stamp_process_time
        self._clock: Clock = clock if clock is not None else SystemClock()
        self._ts_config: TimestampConfig = ts_config or TimestampConfig()
        self._is_warmup: bool = False
        self._build()

    def _build(self) -> None:
        for spec in self._specs:
            self._features[spec.name] = self._registry.create_feature(spec)
        log.debug("SpecFeatureEngine: built %d features", len(self._features))

    # ------------------------------------------------------------------
    # Watermark helpers
    # ------------------------------------------------------------------

    def _get_watermark(self, key: StreamKey) -> WatermarkTracker:
        """Return (or create) the WatermarkTracker for a stream."""
        wm = self._watermarks.get(key)
        if wm is None:
            wm = WatermarkTracker(allowed_lateness_ns=0)
            self._watermarks[key] = wm
        return wm

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def warmup(self, history_events: Iterable) -> None:
        """Pre-heat all features with historical events in order.

        During warmup, process_time_ns is NOT stamped and late event policy
        "recompute_for_backtest_only" treats all events as on-time.
        The per-stream watermarks still advance so post-warmup live events
        that are truly older than history are detected correctly.
        """
        self._is_warmup = True
        count = 0
        try:
            for event in history_events:
                self._route_warmup(event)
                count += 1
        finally:
            self._is_warmup = False
        log.info("SpecFeatureEngine.warmup: processed %d events", count)

    def on_event(self, event: Any) -> FeatureSnapshot:
        """Process one live market event and return a FeatureSnapshot.

        Steps:
        1. Extract EventTimestamps (event_time_ns, receive_time_ns).
        2. Optionally stamp process_time_ns via clock.now_ns().
        3. Advance the per-stream watermark (instrument + type + source).
        4. For each matching feature:
           a. Select the trigger timestamp per time_semantics.
           b. Check per-feature lateness against the stream watermark.
           c. Dispatch to feature.update() or _handle_late().
        5. Return FeatureSnapshot with all three timestamps.
        """
        ts = extract_timestamps(event, self._ts_config, is_live=True)
        process_time_ns: int | None = (
            self._clock.now_ns() if self._stamp_process_time else None
        )

        input_type = _input_type_for(event)
        instrument_id = _extract_instrument_id(event)
        source = getattr(event, "source", None)

        # Advance only the watermark for this event's stream
        stream_key = StreamKey(
            instrument_id=instrument_id,
            input_type=input_type or "unknown",
            source=source,
        )
        watermark = self._get_watermark(stream_key)
        watermark.update(ts.event_time_ns)

        values: dict[str, FeatureValue] = {}
        for name, feature in self._features.items():
            # Route by input_type: skip features not matching this event
            if input_type is not None and feature.spec.input_type != input_type:
                values[name] = feature.value
                continue

            # Per-feature late event check using this stream's watermark
            trigger_ts_ns = select_timestamp(ts, feature.spec.trigger.time_semantics)
            is_late = watermark.is_late_for(
                trigger_ts_ns,
                feature.spec.trigger.allowed_lateness_ns,
            )

            if is_late:
                values[name] = self._handle_late(feature, event, trigger_ts_ns, watermark)
            else:
                update = feature.update(event)
                values[name] = update.value

        return FeatureSnapshot(
            ts_event=ts.event_time_ns,
            instrument_id=instrument_id,
            values=values,
            receive_time_ns=ts.receive_time_ns,
            process_time_ns=process_time_ns,
        )

    def get(self, name: str) -> FeatureValue | None:
        f = self._features.get(name)
        return f.value if f is not None else None

    def is_ready(self, name: str | None = None) -> bool:
        if name is not None:
            f = self._features.get(name)
            return f.is_ready if f is not None else False
        return all(f.is_ready for f in self._features.values())

    def reset(self) -> None:
        for f in self._features.values():
            f.reset()
        self._watermarks.clear()

    def state_dict(self) -> dict:
        return {
            "features": {name: f.state_dict() for name, f in self._features.items()},
            "watermarks": [
                {
                    "key": {
                        "instrument_id": k.instrument_id,
                        "input_type": k.input_type,
                        "source": k.source,
                    },
                    "state": v.state_dict(),
                }
                for k, v in self._watermarks.items()
            ],
        }

    def load_state_dict(self, state: dict) -> None:
        for name, f in self._features.items():
            if name in state.get("features", {}):
                f.load_state_dict(state["features"][name])
        self._watermarks = {}
        for entry in state.get("watermarks", []):
            k = StreamKey(**entry["key"])
            wm = WatermarkTracker(allowed_lateness_ns=0)
            wm.load_state_dict(entry["state"])
            self._watermarks[k] = wm

    def specs(self) -> list[FeatureSpec]:
        return list(self._specs)

    def feature_names(self) -> list[str]:
        return list(self._features)

    # ------------------------------------------------------------------
    # Watermark access
    # ------------------------------------------------------------------

    def watermark_for(
        self,
        instrument_id: str | None,
        input_type: str,
        source: str | None = None,
    ) -> int:
        """Get watermark_ns for a specific stream.  Returns 0 if no events seen."""
        key = StreamKey(instrument_id=instrument_id, input_type=input_type, source=source)
        wm = self._watermarks.get(key)
        return wm.watermark_ns if wm is not None else 0

    def all_watermarks(self) -> dict[StreamKey, int]:
        """All stream watermarks as {StreamKey: watermark_ns}."""
        return {k: v.watermark_ns for k, v in self._watermarks.items()}

    @property
    def watermark_ns(self) -> int:
        """Maximum watermark_ns across all streams.

        For multi-stream engines use watermark_for() or all_watermarks()
        to get per-stream values.
        """
        if not self._watermarks:
            return 0
        return max(wm.watermark_ns for wm in self._watermarks.values())

    @property
    def max_event_time_ns(self) -> int:
        """Maximum event_time_ns seen across all streams."""
        if not self._watermarks:
            return 0
        return max(wm.max_event_time_ns for wm in self._watermarks.values())

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _route_warmup(self, event: Any) -> None:
        """Update all matching features during warmup (no late event check)."""
        ts = extract_timestamps(event, self._ts_config, is_live=False)
        input_type = _input_type_for(event)
        instrument_id = _extract_instrument_id(event)
        source = getattr(event, "source", None)
        stream_key = StreamKey(
            instrument_id=instrument_id,
            input_type=input_type or "unknown",
            source=source,
        )
        self._get_watermark(stream_key).update(ts.event_time_ns)
        for feature in self._features.values():
            if input_type is None or feature.spec.input_type == input_type:
                feature.update(event)

    def _handle_late(
        self,
        feature: FeatureBase,
        event: Any,
        trigger_ts_ns: int,
        watermark: WatermarkTracker,
    ) -> FeatureValue:
        """Dispatch a late event according to the feature's late_event_policy."""
        policy = feature.spec.trigger.late_event_policy

        if policy == "drop":
            return feature.value

        if policy == "log_only":
            log.warning(
                "Late event dropped: feature=%s trigger_ts_ns=%d watermark_ns=%d "
                "allowed_lateness_ns=%d",
                feature.spec.name,
                trigger_ts_ns,
                watermark.watermark_ns,
                feature.spec.trigger.allowed_lateness_ns,
            )
            return feature.value

        if policy == "update_if_not_finalized":
            # For rolling time-based windows: state self-corrects on out-of-order entries.
            # For count-based windows: no fixed boundary, so always update.
            return feature.update(event).value

        if policy == "recompute_for_backtest_only":
            # In live mode (not warmup): behaves as "drop".
            # Warmup bypasses _handle_late entirely via _route_warmup().
            return feature.value

        if policy == "raise":
            raise LateEventError(
                feature_name=feature.spec.name,
                trigger_ts_ns=trigger_ts_ns,
                watermark_ns=watermark.watermark_ns,
                allowed_lateness_ns=feature.spec.trigger.allowed_lateness_ns,
            )

        # Unknown policy: safe default is drop
        log.warning(
            "Unknown late_event_policy %r for feature %s; dropping",
            policy,
            feature.spec.name,
        )
        return feature.value


# ---------------------------------------------------------------------------
# Adapter: plugs SpecFeatureEngine into existing FeaturePipeline
# ---------------------------------------------------------------------------

def _make_spec_driven_engine_class():  # type: ignore[return]
    from nautilus_ext.features.feature_engine import FeatureEngineBase
    from nautilus_ext.features.feature_event import FeatureEvent
    from nautilus_ext.features.feature_schema import FeatureFieldSpec, FeatureSetSpec

    class SpecDrivenFeatureEngine(FeatureEngineBase):
        """Adapter: SpecFeatureEngine → FeatureEngineBase.

        Bridges the compute module's ns-based FeatureSnapshot to the
        existing FeatureEvent (ms ts_event). Allows spec-driven feature sets
        to be registered in FeaturePipeline alongside VwmBarFeatureEngine.

        ts_event conversion: FeatureSnapshot.ts_event (ns) ÷ 1_000_000 → ms.

        Parameters
        ----------
        specs : list[FeatureSpec]
        feature_set_id : str
            Stable identifier (used as FeatureEvent.feature_set_id).
        version : str
        backend_registry : BackendRegistry | None
        stamp_process_time : bool
            Passed to SpecFeatureEngine.  Default False to avoid wall-clock
            non-determinism when used with FeaturePipeline in backtests.
        ts_config : TimestampConfig | None
            Legacy timestamp conversion config for SpecFeatureEngine.
        """

        def __init__(
            self,
            specs: list[FeatureSpec],
            feature_set_id: str,
            version: str = "1",
            backend_registry: BackendRegistry | None = None,
            stamp_process_time: bool = False,
            ts_config: TimestampConfig | None = None,
        ) -> None:
            self._engine = SpecFeatureEngine(
                specs,
                backend_registry,
                stamp_process_time=stamp_process_time,
                ts_config=ts_config,
            )
            self._feature_set_id = feature_set_id
            self._version = version
            self._specs = specs

        @property
        def name(self) -> str:
            return self._feature_set_id

        @property
        def schema(self) -> FeatureSetSpec:
            max_warmup = max(
                (f.warmup_required().n_events for f in self._engine._features.values()),
                default=0,
            )
            return FeatureSetSpec(
                feature_set_id=self._feature_set_id,
                version=self._version,
                input_types=list({s.input_type for s in self._specs}),
                output_features=[
                    FeatureFieldSpec(name=s.name, dtype="float", nullable=True)
                    for s in self._specs
                ],
                required_history=max_warmup,
            )

        def reset(self) -> None:
            self._engine.reset()

        def update(self, event: Any) -> FeatureEvent | None:
            snapshot = self._engine.on_event(event)
            ready = snapshot.ready_values()
            if not ready:
                return None
            # FeatureEvent.ts_event is milliseconds; snapshot.ts_event is ns.
            ts_ms = snapshot.ts_event // 1_000_000
            source_et = getattr(event, "event_type", None)
            return FeatureEvent(
                ts_event=ts_ms,
                instrument_id=snapshot.instrument_id or "unknown",
                feature_set_id=self._feature_set_id,
                feature_version=self._version,
                values=ready,
                source_event_type=source_et,
                source_event_ts=ts_ms,
            )

        def state_dict(self) -> dict:
            return self._engine.state_dict()

        def load_state_dict(self, state: dict) -> None:
            self._engine.load_state_dict(state)

    return SpecDrivenFeatureEngine


_SpecDrivenFeatureEngineClass: type | None = None


def SpecDrivenFeatureEngine(
    specs: list[FeatureSpec],
    feature_set_id: str,
    version: str = "1",
    backend_registry: BackendRegistry | None = None,
    stamp_process_time: bool = False,
    ts_config: TimestampConfig | None = None,
):
    """Factory that returns an instance of the adapter class (lazy import)."""
    global _SpecDrivenFeatureEngineClass
    if _SpecDrivenFeatureEngineClass is None:
        _SpecDrivenFeatureEngineClass = _make_spec_driven_engine_class()
    return _SpecDrivenFeatureEngineClass(
        specs=specs,
        feature_set_id=feature_set_id,
        version=version,
        backend_registry=backend_registry,
        stamp_process_time=stamp_process_time,
        ts_config=ts_config,
    )
