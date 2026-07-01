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

        from feature_engine.compute.clock import ManualClock
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

from feature_engine.compute.backend import BackendRegistry, build_default_registry
from feature_engine.compute.clock import Clock, SystemClock
from feature_engine.compute.feature_base import FeatureBase
from feature_engine.compute.features import DependencyContext
from feature_engine.compute.spec import FeatureSnapshot, FeatureSpec, FeatureValue
from feature_engine.compute.timestamps import (
    EventTimestamps,
    TimestampConfig,
    extract_timestamps,
    select_timestamp,
)
from feature_engine.compute.watermark import StreamKey, WatermarkTracker

log = logging.getLogger(__name__)

_EVENT_TYPE_MAP: dict[str, str] = {
    # Canonical values — equal to FeatureSpec.input_type
    "bar": "bar",
    "trade": "trade",
    "quote": "quote",
    "book_delta": "book_delta",
    "timer": "timer",
    "derived": "derived",      # reserved for derived (feature-to-feature) specs
    # Vendor / legacy aliases
    "trade_tick": "trade",
    "quote_tick": "quote",
    "orderbook": "book_delta",
    "order_book": "book_delta",
    "book_update": "book_delta",
    "funding_rate": "timer",
}

# All recognized FeatureSpec.input_type values (canonical + normalizable aliases).
_VALID_INPUT_TYPES: frozenset[str] = frozenset(_EVENT_TYPE_MAP.keys())


def input_type_for_event(event: Any) -> str | None:
    """Return the canonical input_type string for a market event.

    Canonical values match FeatureSpec.input_type:
        ``"bar"``, ``"trade"``, ``"quote"``, ``"book_delta"``, ``"timer"``.

    Accepts both canonical names and vendor/legacy aliases
    (``"trade_tick"``, ``"quote_tick"``, ``"orderbook"``, etc.) so that
    ``FeatureSpec.input_type`` and watermark ``StreamKey.input_type`` always
    agree regardless of the raw ``event_type`` string carried by the event.

    Returns ``None`` when the event carries no ``event_type`` attribute or
    when the value is not a recognised type.
    """
    et = getattr(event, "event_type", None)
    if et is None:
        return None
    return _EVENT_TYPE_MAP.get(et)


# Internal alias kept for call-sites inside this module
_input_type_for = input_type_for_event


def _extract_instrument_id(event: Any) -> str | None:
    return getattr(event, "instrument_id", None)


# ---------------------------------------------------------------------------
# Pre-build spec validation
# ---------------------------------------------------------------------------

def _validate_spec_list(specs: list[FeatureSpec], registry: BackendRegistry) -> None:
    """Validate a list of FeatureSpecs before any feature instances are created.

    Raises ``ValueError`` on the first violation found.  Checks:
    - ``name`` is non-empty.
    - No duplicate names within the list.
    - ``input_type`` is a known canonical or alias value.
    - ``window`` is a positive integer when present.
    - ``backend`` is registered in the registry.

    Unknown feature *type* (name prefix / params["type"]) is not checked here
    because it is caught by the backend's ``create_feature()`` with a clear
    ``ValueError`` during ``_build()``.

    Whether ``input_field`` is required is validated inside the concrete feature
    class constructor (e.g. ``RollingSumFeature.__init__``), which also fires
    during ``_build()``.
    """
    registered = set(registry.available_backends())
    seen: set[str] = set()
    for spec in specs:
        if not spec.name:
            raise ValueError(
                "FeatureSpec.name must be non-empty. "
                "Each feature needs a stable, unique name."
            )
        if spec.name in seen:
            raise ValueError(
                f"Duplicate feature name {spec.name!r}. "
                "Every FeatureSpec in the engine must have a unique name."
            )
        seen.add(spec.name)

        if spec.input_type not in _VALID_INPUT_TYPES:
            raise ValueError(
                f"FeatureSpec {spec.name!r}: unknown input_type {spec.input_type!r}. "
                f"Valid values: {sorted(_VALID_INPUT_TYPES)}"
            )

        if spec.window is not None and spec.window <= 0:
            raise ValueError(
                f"FeatureSpec {spec.name!r}: window must be a positive integer, "
                f"got {spec.window!r}."
            )

        if spec.backend not in registered:
            raise ValueError(
                f"FeatureSpec {spec.name!r}: backend {spec.backend!r} is not "
                f"registered. Available backends: {sorted(registered)}"
            )

    # Second pass: validate depends_on (requires all names to be known)
    for spec in specs:
        for dep in spec.depends_on:
            if dep == spec.name:
                raise ValueError(
                    f"FeatureSpec {spec.name!r}: self-dependency not allowed "
                    f"(depends_on contains {dep!r})."
                )
            if dep not in seen:
                raise ValueError(
                    f"FeatureSpec {spec.name!r}: unknown dependency {dep!r} in "
                    f"depends_on. Known feature names: {sorted(seen)}"
                )


# ---------------------------------------------------------------------------
# LateEventError
# ---------------------------------------------------------------------------

class LateEventError(RuntimeError):
    """Raised by late_event_policy='raise' when a late event is received.

    Attributes
    ----------
    feature_name : str
    stream_key : StreamKey
        The (instrument_id, input_type, source) stream that detected lateness.
    trigger_ts_ns : int
        The event's trigger timestamp (honouring time_semantics).
    watermark_ns : int
        The stream watermark at the time of the check.
    allowed_lateness_ns : int
        The feature's allowed_lateness_ns setting.
    late_by_ns : int
        How many nanoseconds late: watermark_ns − trigger_ts_ns (always > 0).
    event_time_ns : int
        Exchange/source timestamp of the triggering event.
    receive_time_ns : int | None
        Local reception timestamp, when available.
    process_time_ns : int | None
        Engine processing timestamp, when stamp_process_time=True.
    """

    def __init__(
        self,
        feature_name: str,
        stream_key: StreamKey,
        trigger_ts_ns: int,
        watermark_ns: int,
        allowed_lateness_ns: int,
        event_time_ns: int,
        receive_time_ns: int | None = None,
        process_time_ns: int | None = None,
    ) -> None:
        late_by_ns = watermark_ns - trigger_ts_ns
        src_suffix = f"/{stream_key.source}" if stream_key.source else ""
        super().__init__(
            f"Late event for feature {feature_name!r} "
            f"stream={stream_key.instrument_id}/{stream_key.input_type}{src_suffix}: "
            f"trigger_ts_ns={trigger_ts_ns} watermark_ns={watermark_ns} "
            f"late_by_ns={late_by_ns} (allowed_lateness_ns={allowed_lateness_ns})"
        )
        self.feature_name = feature_name
        self.stream_key = stream_key
        self.trigger_ts_ns = trigger_ts_ns
        self.watermark_ns = watermark_ns
        self.allowed_lateness_ns = allowed_lateness_ns
        self.late_by_ns = late_by_ns
        self.event_time_ns = event_time_ns
        self.receive_time_ns = receive_time_ns
        self.process_time_ns = process_time_ns


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
    profile : bool
        When True, collect per-feature event counters (update_count,
        skip_count, late_drop_count).  Access via ``profile_summary()``.
        Adds one dict-lookup + increment per feature per event when enabled.
        Default False — zero overhead on the hot path.
    """

    def __init__(
        self,
        specs: list[FeatureSpec],
        backend_registry: BackendRegistry | None = None,
        stamp_process_time: bool = True,
        clock: Clock | None = None,
        ts_config: TimestampConfig | None = None,
        is_live: bool = True,
        profile: bool = False,
    ) -> None:
        self._specs: list[FeatureSpec] = list(specs)
        self._registry: BackendRegistry = backend_registry or build_default_registry()
        self._features: dict[str, FeatureBase] = {}
        self._watermarks: dict[StreamKey, WatermarkTracker] = {}
        self._stamp_process_time = stamp_process_time
        self._clock: Clock = clock if clock is not None else SystemClock()
        self._ts_config: TimestampConfig = ts_config or TimestampConfig()
        self._is_live_mode: bool = is_live
        self._is_warmup: bool = False
        self._profile: bool = profile
        self._profile_update_count: dict[str, int] = {}
        self._profile_skip_count: dict[str, int] = {}
        self._profile_late_drop_count: dict[str, int] = {}
        self._profile_last_status: dict[str, str | None] = {}
        # Finer-grained health counters (populated only when profile=True)
        self._profile_missing_field_count: dict[str, int] = {}
        self._profile_dep_not_ready_count: dict[str, int] = {}
        self._profile_last_update_ns: dict[str, int] = {}
        # Dependency graph state — populated by _build_dependency_graph()
        self._raw_features: dict[str, FeatureBase] = {}
        self._derived_names: list[str] = []          # topo-sorted derived feature names
        self._dep_graph: dict[str, list[str]] = {}   # name -> direct dep names (derived only)
        self._build()
        if profile:
            self._profile_update_count = {n: 0 for n in self._features}
            self._profile_skip_count = {n: 0 for n in self._features}
            self._profile_late_drop_count = {n: 0 for n in self._features}
            self._profile_last_status = {n: None for n in self._features}
            self._profile_missing_field_count = {n: 0 for n in self._features}
            self._profile_dep_not_ready_count = {n: 0 for n in self._features}
            self._profile_last_update_ns = {n: 0 for n in self._features}

    def _build(self) -> None:
        _validate_spec_list(self._specs, self._registry)
        for spec in self._specs:
            self._features[spec.name] = self._registry.create_feature(spec)
        self._build_dependency_graph()
        log.debug(
            "SpecFeatureEngine: built %d features (%d raw, %d derived)",
            len(self._features), len(self._raw_features), len(self._derived_names),
        )

    def _build_dependency_graph(self) -> None:
        """Separate raw from derived features; validate and topologically sort derived ones.

        Populates ``_raw_features``, ``_dep_graph``, and ``_derived_names`` (topo order).

        Raises ``ValueError`` on:
        - Self-dependency (caught earlier in _validate_spec_list, re-checked here).
        - Unknown dependency name (same).
        - Circular dependency (detected here via DFS colouring).
        """
        self._raw_features = {}
        self._dep_graph = {}

        for name, feature in self._features.items():
            deps = list(feature.spec.depends_on)
            if deps:
                self._dep_graph[name] = deps
            else:
                self._raw_features[name] = feature

        self._derived_names = self._topo_sort()

    def _topo_sort(self) -> list[str]:
        """Return derived feature names in topological order (deps before dependents).

        Uses iterative DFS with three-colour marking to detect cycles.

        White (0) = not visited, Gray (1) = on the current DFS stack, Black (2) = done.
        """
        WHITE, GRAY, BLACK = 0, 1, 2
        color: dict[str, int] = {n: WHITE for n in self._dep_graph}
        result: list[str] = []

        def visit(node: str, stack: list[str]) -> None:
            if color[node] == BLACK:
                return
            if color[node] == GRAY:
                # Reconstruct cycle for the error message
                cycle_start = stack.index(node)
                cycle = stack[cycle_start:] + [node]
                raise ValueError(
                    f"Circular dependency detected in feature graph: "
                    f"{' -> '.join(cycle)}"
                )
            color[node] = GRAY
            stack.append(node)
            for dep in self._dep_graph.get(node, []):
                if dep in color:          # only recurse into other derived features
                    visit(dep, stack)
            stack.pop()
            color[node] = BLACK
            result.append(node)

        for node in self._dep_graph:
            if color[node] == WHITE:
                visit(node, [])

        return result  # deps before dependents

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

        Two-phase update
        ----------------
        Phase 1 — raw features:
            Each feature whose ``input_type`` matches the event's type is
            updated.  Features that do not match return their cached value.
            Late-event policy is applied per feature.  Updated features are
            added to the ``dirty`` set.

        Phase 2 — derived features (topological order):
            For each derived feature (``depends_on`` non-empty), the engine
            checks whether any direct dependency is dirty.  If so, the feature
            is updated via ``update_from_dependencies(ctx, event)``; otherwise
            the cached value is returned.  The ``DependencyContext`` holds a
            live reference to the values dict and is updated in-place, so each
            derived feature sees the latest values of all previously computed
            features (current-value semantics).  Dirty is propagated upward so
            multi-level chains (A → B → C) work correctly.
        """
        ts = extract_timestamps(event, self._ts_config, is_live=self._is_live_mode)
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
        dirty: set[str] = set()   # names updated (processed) this event turn

        # ----------------------------------------------------------------
        # Phase 1: raw features
        # ----------------------------------------------------------------
        for name, feature in self._raw_features.items():
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
                values[name] = self._handle_late(
                    feature, event, trigger_ts_ns, watermark,
                    stream_key, ts, process_time_ns,
                )
                if self._profile:
                    policy = feature.spec.trigger.late_event_policy
                    if policy in ("drop", "log_only", "recompute_for_backtest_only"):
                        self._profile_late_drop_count[name] += 1
                    self._profile_last_status[name] = "late_dropped"
            else:
                update = feature.update(event)
                values[name] = update.value
                dirty.add(name)
                if self._profile:
                    status = update.value.update_status
                    if status == "updated":
                        self._profile_update_count[name] += 1
                        self._profile_last_update_ns[name] = ts.event_time_ns
                    elif status == "skipped_missing_field":
                        self._profile_skip_count[name] += 1
                        self._profile_missing_field_count[name] += 1
                    elif status == "not_ready":
                        self._profile_skip_count[name] += 1
                    self._profile_last_status[name] = status

        # ----------------------------------------------------------------
        # Phase 2: derived features in topological order
        # ----------------------------------------------------------------
        if self._derived_names:
            # DependencyContext holds a live reference; in-place updates to
            # `values` are immediately visible to downstream derived features.
            ctx = DependencyContext(values)
            for name in self._derived_names:
                feature = self._features[name]
                deps = self._dep_graph.get(name, [])

                # Only trigger if at least one dep was updated this event turn
                if not any(d in dirty for d in deps):
                    values[name] = feature.value   # return cached, no change
                    continue

                update = feature.update_from_dependencies(ctx, event)  # type: ignore[attr-defined]
                values[name] = update.value
                dirty.add(name)   # propagate dirty upward for multi-level chains

                if self._profile:
                    status = update.value.update_status
                    if status == "updated":
                        self._profile_update_count[name] += 1
                        self._profile_last_update_ns[name] = ts.event_time_ns
                    elif status == "dependency_not_ready":
                        self._profile_skip_count[name] += 1
                        self._profile_dep_not_ready_count[name] += 1
                    elif status in ("not_ready", "skipped_missing_field"):
                        self._profile_skip_count[name] += 1
                    self._profile_last_status[name] = status

        return FeatureSnapshot(
            ts_event=ts.event_time_ns,
            instrument_id=instrument_id,
            values=values,
            receive_time_ns=ts.receive_time_ns,
            process_time_ns=process_time_ns,
        )

    def get(self, name: str, default: FeatureValue | None = None) -> FeatureValue | None:
        """Return the cached FeatureValue for a feature, or default if not found."""
        f = self._features.get(name)
        return f.value if f is not None else default

    def value(self, name: str, default: Any = None) -> Any:
        """Return the cached scalar for a feature, or default if absent or not ready."""
        f = self._features.get(name)
        if f is None or not f.is_ready:
            return default
        return f.value.value

    def latest(self) -> dict[str, FeatureValue]:
        """Return all features' current FeatureValues as a dict keyed by name."""
        return {name: f.value for name, f in self._features.items()}

    def latest_values(self, include_not_ready: bool = False) -> dict[str, Any]:
        """Return all feature scalars.

        When ``include_not_ready=False`` (default) only ready features are
        included.  When ``True``, all features are included with ``None``
        for unready ones.
        """
        if include_not_ready:
            return {name: f.value.value for name, f in self._features.items()}
        return {
            name: f.value.value
            for name, f in self._features.items()
            if f.is_ready
        }

    def ready(self, name: str) -> bool:
        """True if the named feature exists and is currently ready."""
        f = self._features.get(name)
        return f is not None and f.is_ready

    def statuses(self) -> dict[str, str | None]:
        """Return ``update_status`` for every feature keyed by name.

        Reflects the status from the most recent ``on_event()`` call (or
        ``None`` for features that have never been updated, or legacy features
        that do not populate the field).
        """
        return {name: f.value.update_status for name, f in self._features.items()}

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
        """Return all registered feature names in insertion order."""
        return list(self._features)

    def feature_specs(self) -> dict[str, FeatureSpec]:
        """Return all FeatureSpecs keyed by name.

        ``FeatureSpec`` is frozen, so the returned objects are safe to read
        without copying.  The dict preserves insertion order (same as
        ``feature_names()``).
        """
        return {spec.name: spec for spec in self._specs}

    def profile_summary(self) -> dict:
        """Return per-feature event counters collected since engine construction.

        Returns ``{"profile": False}`` when profiling is disabled (the default).

        When ``profile=True``, returns::

            {
                "profile": True,
                "features": {
                    "feat_name": {
                        "update_count":    int,   # on_event() calls that produced "updated"
                        "skip_count":      int,   # "not_ready" + "skipped_missing_field"
                        "late_drop_count": int,   # dropped late events (policy=drop/log_only)
                    },
                    ...
                }
            }

        Counters cover only ``on_event()`` calls — warmup events are excluded.
        """
        if not self._profile:
            return {"profile": False}
        return {
            "profile": True,
            "features": {
                name: {
                    "update_count": self._profile_update_count.get(name, 0),
                    "skip_count": self._profile_skip_count.get(name, 0),
                    "late_drop_count": self._profile_late_drop_count.get(name, 0),
                    "last_status": self._profile_last_status.get(name),
                }
                for name in self._features
            },
        }

    def health_summary(self, stale_threshold_ns: int | None = None) -> dict:
        """Return a feature health diagnostic report.

        When ``profile=True`` (set at engine construction), returns per-feature
        counters broken down by status type.  When ``profile=False``, returns
        only readiness info (no counters — zero overhead on the hot path).

        Parameters
        ----------
        stale_threshold_ns : int | None
            If provided and ``profile=True``, features whose ``last_update_ns``
            is older than ``stale_threshold_ns`` nanoseconds behind the engine's
            max watermark are listed in ``stale_features``.  None disables
            stale detection.

        Returns
        -------
        dict with keys:
            ``profiling_enabled`` (bool),
            ``n_features`` (int),
            ``n_ready`` (int),
            ``n_derived`` (int),
            ``ready_features`` (list[str]),
            ``not_ready_features`` (list[str]),
            ``features`` (dict, only when ``profile=True``):
                per-feature ``update_count``, ``skipped_missing_field_count``,
                ``dependency_not_ready_count``, ``late_dropped_count``,
                ``last_status``, ``last_update_ns``,
            ``stale_features`` (list[str], only when profile+threshold set).
        """
        ready     = [n for n, f in self._features.items() if f.is_ready]
        not_ready = [n for n, f in self._features.items() if not f.is_ready]
        summary: dict = {
            "profiling_enabled": self._profile,
            "n_features": len(self._features),
            "n_ready": len(ready),
            "n_derived": len(self._derived_names),
            "ready_features": ready,
            "not_ready_features": not_ready,
        }
        if not self._profile:
            return summary

        feature_health = {}
        for name in self._features:
            feature_health[name] = {
                "update_count":              self._profile_update_count.get(name, 0),
                "skipped_missing_field_count": self._profile_missing_field_count.get(name, 0),
                "dependency_not_ready_count":  self._profile_dep_not_ready_count.get(name, 0),
                "late_dropped_count":          self._profile_late_drop_count.get(name, 0),
                "last_status":                 self._profile_last_status.get(name),
                "last_update_ns":              self._profile_last_update_ns.get(name, 0),
            }
        summary["features"] = feature_health

        if stale_threshold_ns is not None:
            max_wm = self.watermark_ns
            stale = [
                name
                for name, h in feature_health.items()
                if h["last_update_ns"] > 0 and (max_wm - h["last_update_ns"]) > stale_threshold_ns
            ]
            summary["stale_features"] = stale

        return summary

    # ------------------------------------------------------------------
    # Watermark access
    # ------------------------------------------------------------------

    def watermark_for(
        self,
        instrument_id: str | None,
        input_type: str,
        source: str | None = None,
    ) -> int:
        """Get watermark_ns for a specific or aggregate stream.

        When ``source`` is provided, returns the watermark for the exact
        ``(instrument_id, input_type, source)`` stream, or 0 if no events
        have been seen for that key.

        When ``source`` is ``None`` (default), returns the **maximum**
        watermark across all streams sharing ``(instrument_id, input_type)``
        regardless of source.  This is an **aggregate query for
        monitoring/debugging only** — the engine itself always uses the exact
        ``(instrument_id, input_type, source)`` stream watermark for per-feature
        late-event decisions and is never influenced by this aggregate value.

        Returns 0 when no matching stream has seen any events.
        """
        if source is not None:
            key = StreamKey(instrument_id=instrument_id, input_type=input_type, source=source)
            wm = self._watermarks.get(key)
            return wm.watermark_ns if wm is not None else 0

        # Aggregate: max watermark across all streams matching instrument_id + input_type
        matches = [
            wm.watermark_ns
            for k, wm in self._watermarks.items()
            if k.instrument_id == instrument_id and k.input_type == input_type
        ]
        return max(matches) if matches else 0

    def all_watermarks(self) -> dict[StreamKey, int]:
        """All stream watermarks as {StreamKey: watermark_ns}."""
        return {k: v.watermark_ns for k, v in self._watermarks.items()}

    @property
    def watermark_ns(self) -> int:
        """Maximum watermark_ns across all streams (backward-compat aggregate).

        **For monitoring and debugging only.** This property returns the max
        watermark across every stream the engine has ever seen. Do NOT use
        this value for feature late-event decisions — the engine internally
        uses per-stream watermarks keyed by StreamKey, and this aggregate can
        be far ahead of a slow stream, causing incorrect late classification.

        For per-stream access use ``watermark_for(instrument_id, input_type)``
        or ``all_watermarks()``.
        """
        if not self._watermarks:
            return 0
        return max(wm.watermark_ns for wm in self._watermarks.values())

    @property
    def max_event_time_ns(self) -> int:
        """Maximum event_time_ns seen across all streams (backward-compat aggregate).

        **For monitoring and debugging only.** Same caveat as ``watermark_ns``:
        this is a cross-stream maximum and must not be used for feature
        late-event decisions.
        """
        if not self._watermarks:
            return 0
        return max(wm.max_event_time_ns for wm in self._watermarks.values())

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _route_warmup(self, event: Any) -> None:
        """Update all matching features during warmup (no late event check).

        Same two-phase approach as on_event():
        1. Update raw features that match the event's input_type.
        2. Update derived features in topological order if any dep is dirty.
        """
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

        dirty: set[str] = set()

        # Phase 1: raw features
        for name, feature in self._raw_features.items():
            if input_type is None or feature.spec.input_type == input_type:
                feature.update(event)
                dirty.add(name)

        # Phase 2: derived features in topological order
        if dirty and self._derived_names:
            # Build a values dict from post-update raw feature state; pre-populate
            # derived feature cached values so multi-level lookups work correctly.
            values: dict[str, FeatureValue] = {n: f.value for n, f in self._raw_features.items()}
            for n in self._derived_names:
                values[n] = self._features[n].value   # start from cached
            ctx = DependencyContext(values)
            for name in self._derived_names:
                feature = self._features[name]
                deps = self._dep_graph.get(name, [])
                if any(d in dirty for d in deps):
                    update = feature.update_from_dependencies(ctx, event)  # type: ignore[attr-defined]
                    values[name] = update.value
                    dirty.add(name)

    def _handle_late(
        self,
        feature: FeatureBase,
        event: Any,
        trigger_ts_ns: int,
        watermark: WatermarkTracker,
        stream_key: StreamKey,
        ts: EventTimestamps,
        process_time_ns: int | None = None,
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
                stream_key=stream_key,
                trigger_ts_ns=trigger_ts_ns,
                watermark_ns=watermark.watermark_ns,
                allowed_lateness_ns=feature.spec.trigger.allowed_lateness_ns,
                event_time_ns=ts.event_time_ns,
                receive_time_ns=ts.receive_time_ns,
                process_time_ns=process_time_ns,
            )

        # Unknown policy: safe default is drop
        log.warning(
            "Unknown late_event_policy %r for feature %s; dropping",
            policy,
            feature.spec.name,
        )
        return feature.value

