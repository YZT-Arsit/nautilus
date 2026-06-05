"""
SpecFeatureEngine and SpecDrivenFeatureEngine.

SpecFeatureEngine
    Standalone spec-driven engine. Processes market events, maintains a
    per-engine WatermarkTracker, and applies late event policy before
    calling feature.update(). Returns FeatureSnapshot with all three
    timestamps (event_time_ns, receive_time_ns, process_time_ns).

    Watermark semantics
    -------------------
    The watermark tracks the maximum event_time_ns seen across all events.
    Before calling feature.update(), the engine checks:

        is_late = event_time_ns < (max_event_time_ns - feature.trigger.allowed_lateness_ns)

    Different features can declare different allowed_lateness_ns; the engine
    uses each feature's own allowed_lateness value for the per-feature check.

    Late event policies (configured per feature via TriggerPolicy):
        "drop"                      — skip update(), return cached value
        "log_only"                  — log warning, skip update()
        "update_if_not_finalized"   — call update() anyway (state self-corrects)
        "recompute_for_backtest_only" — update during warmup; drop in live mode

    process_time_ns stamping
    ------------------------
    SpecFeatureEngine.on_event() stamps the current wall-clock time as
    process_time_ns in the returned FeatureSnapshot. This enables
    pipeline latency measurement: snapshot.processing_latency_ns().
    During warmup, process_time_ns is NOT stamped (backtest doesn't
    have meaningful wall-clock processing time).

SpecDrivenFeatureEngine
    Adapter that implements FeatureEngineBase, allowing SpecFeatureEngine
    to plug into the existing FeaturePipeline.
    Converts FeatureSnapshot (ns timestamps) to FeatureEvent (ms ts_event).
"""
from __future__ import annotations

import logging
import time
from typing import Any, Iterable

from nautilus_ext.features.compute.backend import BackendRegistry, build_default_registry
from nautilus_ext.features.compute.feature_base import FeatureBase
from nautilus_ext.features.compute.spec import FeatureSnapshot, FeatureSpec, FeatureValue
from nautilus_ext.features.compute.timestamps import EventTimestamps, extract_timestamps, select_timestamp
from nautilus_ext.features.compute.watermark import WatermarkTracker

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


class SpecFeatureEngine:
    """Spec-driven incremental feature engine with full timestamp semantics.

    Parameters
    ----------
    specs : list[FeatureSpec]
        Feature specifications to register and build.
    backend_registry : BackendRegistry | None
        Registry for feature creation. Defaults to the pure-Python backend.
    stamp_process_time : bool
        If True (default), stamp process_time_ns in FeatureSnapshot during
        on_event() using time.time_ns(). Set False for deterministic tests
        or when process_time is not needed.
    """

    def __init__(
        self,
        specs: list[FeatureSpec],
        backend_registry: BackendRegistry | None = None,
        stamp_process_time: bool = True,
    ) -> None:
        self._specs: list[FeatureSpec] = list(specs)
        self._registry: BackendRegistry = backend_registry or build_default_registry()
        self._features: dict[str, FeatureBase] = {}
        self._watermark = WatermarkTracker(allowed_lateness_ns=0)
        self._stamp_process_time = stamp_process_time
        self._is_warmup: bool = False
        self._build()

    def _build(self) -> None:
        for spec in self._specs:
            self._features[spec.name] = self._registry.create_feature(spec)
        log.debug("SpecFeatureEngine: built %d features", len(self._features))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def warmup(self, history_events: Iterable) -> None:
        """Pre-heat all features with historical events in order.

        During warmup, process_time_ns is NOT stamped and late event policy
        "recompute_for_backtest_only" treats all events as on-time.
        The watermark still advances so that post-warmup live events that are
        truly older than history are detected correctly.
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
        2. Optionally stamp process_time_ns.
        3. Advance the watermark.
        4. For each matching feature:
           a. Check per-feature late event condition.
           b. Dispatch to feature.update() or late-event handler.
        5. Return FeatureSnapshot with all three timestamps.
        """
        ts = extract_timestamps(event)
        process_time_ns: int | None = time.time_ns() if self._stamp_process_time else None

        # Advance the global watermark (used for is_late checks below)
        self._watermark.update(ts.event_time_ns)

        input_type = _input_type_for(event)
        values: dict[str, FeatureValue] = {}

        for name, feature in self._features.items():
            # Route by input_type
            if input_type is not None and feature.spec.input_type != input_type:
                values[name] = feature.value
                continue

            # Per-feature late event check (uses feature's own allowed_lateness_ns)
            trigger_ts_ns = select_timestamp(ts, feature.spec.trigger.time_semantics)
            is_late = self._watermark.is_late_for(
                trigger_ts_ns,
                feature.spec.trigger.allowed_lateness_ns,
            )

            if is_late:
                values[name] = self._handle_late(feature, event, trigger_ts_ns)
            else:
                update = feature.update(event)
                values[name] = update.value

        return FeatureSnapshot(
            ts_event=ts.event_time_ns,
            instrument_id=_extract_instrument_id(event),
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
        self._watermark.reset()

    def state_dict(self) -> dict:
        return {
            "features": {name: f.state_dict() for name, f in self._features.items()},
            "watermark": self._watermark.state_dict(),
        }

    def load_state_dict(self, state: dict) -> None:
        for name, f in self._features.items():
            if name in state.get("features", {}):
                f.load_state_dict(state["features"][name])
        if "watermark" in state:
            self._watermark.load_state_dict(state["watermark"])

    def specs(self) -> list[FeatureSpec]:
        return list(self._specs)

    def feature_names(self) -> list[str]:
        return list(self._features)

    @property
    def watermark_ns(self) -> int:
        return self._watermark.watermark_ns

    @property
    def max_event_time_ns(self) -> int:
        return self._watermark.max_event_time_ns

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _route_warmup(self, event: Any) -> None:
        """Update all matching features during warmup (no late event check)."""
        ts = extract_timestamps(event)
        self._watermark.update(ts.event_time_ns)
        input_type = _input_type_for(event)
        for feature in self._features.values():
            if input_type is None or feature.spec.input_type == input_type:
                feature.update(event)

    def _handle_late(
        self,
        feature: FeatureBase,
        event: Any,
        trigger_ts_ns: int,
    ) -> FeatureValue:
        """Dispatch a late event according to the feature's late_event_policy."""
        policy = feature.spec.trigger.late_event_policy

        if policy == "drop":
            return feature.value

        if policy == "log_only":
            log.warning(
                "Late event dropped: feature=%s event_time_ns=%d watermark_ns=%d "
                "allowed_lateness_ns=%d",
                feature.spec.name,
                trigger_ts_ns,
                self._watermark.watermark_ns,
                feature.spec.trigger.allowed_lateness_ns,
            )
            return feature.value

        if policy == "update_if_not_finalized":
            # For rolling time-based windows: the feature's state container
            # handles out-of-order entries naturally (eviction is self-correcting).
            # For count-based windows: finalization doesn't apply, so always update.
            return feature.update(event).value

        if policy == "recompute_for_backtest_only":
            # In live mode (not warmup) this behaves as "drop".
            # In warmup mode, _route_warmup() bypasses this method entirely,
            # so we should not reach here during warmup.
            return feature.value

        # Unknown policy: safe default is drop.
        log.warning("Unknown late_event_policy %r for feature %s; dropping", policy, feature.spec.name)
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
            Passed to SpecFeatureEngine. Default False to avoid wall-clock
            non-determinism when used with FeaturePipeline in backtests.
        """

        def __init__(
            self,
            specs: list[FeatureSpec],
            feature_set_id: str,
            version: str = "1",
            backend_registry: BackendRegistry | None = None,
            stamp_process_time: bool = False,
        ) -> None:
            self._engine = SpecFeatureEngine(
                specs, backend_registry, stamp_process_time=stamp_process_time
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
    )
