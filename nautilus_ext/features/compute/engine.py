"""
SpecFeatureEngine and SpecDrivenFeatureEngine.

SpecFeatureEngine
    Standalone spec-driven engine. Accepts a list of FeatureSpec objects,
    builds feature instances via BackendRegistry, routes market events to
    the relevant features, and returns FeatureSnapshot objects. Suitable
    for standalone use in backtests or live runners.

    Interface:
        engine = SpecFeatureEngine(specs=[...])
        engine.warmup(history_events)           # pre-heat all features
        snapshot = engine.on_event(event)       # hot path, returns FeatureSnapshot
        value    = engine.get("my_feature")     # latest FeatureValue by name
        ready    = engine.is_ready()            # True when all features ready

SpecDrivenFeatureEngine
    Adapter that wraps SpecFeatureEngine and implements FeatureEngineBase
    from nautilus_ext.features.feature_engine. Allows the spec-driven engine
    to be plugged into FeaturePipeline without changing the existing pipeline.

    Interface (same as any other FeatureEngineBase subclass):
        engine = SpecDrivenFeatureEngine(specs=[...], feature_set_id="my_v1")
        pipeline = FeaturePipeline([engine], online_store=..., offline_store=...)
        pipeline.warmup(history_events)
        feature_events = pipeline.update(live_event)   # returns list[FeatureEvent]

Event type routing
------------------
Each feature declares ``spec.input_type`` (e.g. ``"bar"``). The engine checks
``event.event_type`` (if present) and only calls ``feature.update()`` for
features whose input_type matches. Features that do not match return their
cached value unchanged.

Supported event_type values and how they map to input_type:
    event.event_type == "bar"         → input_type "bar"
    event.event_type == "trade_tick"  → input_type "trade"
    event.event_type == "quote_tick"  → input_type "quote"
    event.event_type == "orderbook"   → input_type "book_delta"
    (no event_type attr)              → matched by all features (duck-typed)
"""
from __future__ import annotations

import logging
from typing import Any, Iterable

from nautilus_ext.features.compute.backend import BackendRegistry, build_default_registry
from nautilus_ext.features.compute.feature_base import FeatureBase
from nautilus_ext.features.compute.spec import FeatureSnapshot, FeatureSpec, FeatureValue

log = logging.getLogger(__name__)

# Mapping from event_type attribute values to FeatureSpec.input_type strings
_EVENT_TYPE_MAP: dict[str, str] = {
    "bar": "bar",
    "trade_tick": "trade",
    "quote_tick": "quote",
    "orderbook": "book_delta",
    "funding_rate": "timer",
}


def _extract_ts(event: Any) -> int:
    ts = getattr(event, "ts_event", None)
    return int(ts) if ts is not None else 0


def _extract_instrument_id(event: Any) -> str | None:
    return getattr(event, "instrument_id", None)


def _input_type_for(event: Any) -> str | None:
    """Map event.event_type to a FeatureSpec input_type string, or None."""
    et = getattr(event, "event_type", None)
    if et is None:
        return None  # duck-typed: all features will be tried
    return _EVENT_TYPE_MAP.get(et)


class SpecFeatureEngine:
    """Spec-driven incremental feature engine.

    Parameters
    ----------
    specs : list[FeatureSpec]
        Feature specifications to register and build.
    backend_registry : BackendRegistry | None
        Registry for feature creation. Defaults to the pure-Python backend.
    """

    def __init__(
        self,
        specs: list[FeatureSpec],
        backend_registry: BackendRegistry | None = None,
    ) -> None:
        self._specs: list[FeatureSpec] = list(specs)
        self._registry: BackendRegistry = backend_registry or build_default_registry()
        self._features: dict[str, FeatureBase] = {}
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

        update() during warmup behaves identically to live updates — internal
        state accumulates. After warmup, is_ready() reflects actual readiness.
        """
        count = 0
        for event in history_events:
            self._route(event)
            count += 1
        log.info("SpecFeatureEngine.warmup: processed %d events", count)

    def on_event(self, event: Any) -> FeatureSnapshot:
        """Process one market event and return a FeatureSnapshot (hot path).

        Only features whose input_type matches the event are updated; others
        return their cached values unchanged. No DataFrame is created.
        """
        ts = _extract_ts(event)
        iid = _extract_instrument_id(event)
        input_type = _input_type_for(event)

        values: dict[str, FeatureValue] = {}
        for name, feature in self._features.items():
            if input_type is None or feature.spec.input_type == input_type:
                update = feature.update(event)
                values[name] = update.value
            else:
                values[name] = feature.value  # cached, not updated

        return FeatureSnapshot(ts_event=ts, instrument_id=iid, values=values)

    def get(self, name: str) -> FeatureValue | None:
        """Return the latest FeatureValue for a named feature, or None."""
        f = self._features.get(name)
        return f.value if f is not None else None

    def is_ready(self, name: str | None = None) -> bool:
        """Return True when all (or the named) feature(s) are ready."""
        if name is not None:
            f = self._features.get(name)
            return f.is_ready if f is not None else False
        return all(f.is_ready for f in self._features.values())

    def reset(self) -> None:
        """Clear all feature states to initial condition."""
        for f in self._features.values():
            f.reset()

    def state_dict(self) -> dict:
        """Return a JSON-serialisable checkpoint of all feature states."""
        return {name: f.state_dict() for name, f in self._features.items()}

    def load_state_dict(self, state: dict) -> None:
        """Restore all feature states from a checkpoint dict."""
        for name, f in self._features.items():
            if name in state:
                f.load_state_dict(state[name])

    def specs(self) -> list[FeatureSpec]:
        return list(self._specs)

    def feature_names(self) -> list[str]:
        return list(self._features)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _route(self, event: Any) -> None:
        """Update only features whose input_type matches the event."""
        input_type = _input_type_for(event)
        for feature in self._features.values():
            if input_type is None or feature.spec.input_type == input_type:
                feature.update(event)


# ---------------------------------------------------------------------------
# Adapter: plugs SpecFeatureEngine into FeaturePipeline
# ---------------------------------------------------------------------------

# Late import to avoid circular dependency on FeatureEngineBase
def _make_spec_driven_engine_class():  # type: ignore[return]
    from nautilus_ext.features.feature_engine import FeatureEngineBase
    from nautilus_ext.features.feature_event import FeatureEvent
    from nautilus_ext.features.feature_schema import FeatureFieldSpec, FeatureSetSpec

    class SpecDrivenFeatureEngine(FeatureEngineBase):
        """Adapter: SpecFeatureEngine → FeatureEngineBase.

        Allows a spec-driven feature set to be registered in FeaturePipeline
        alongside existing VwmBarFeatureEngine and other engines.

        Parameters
        ----------
        specs : list[FeatureSpec]
            Feature specifications.
        feature_set_id : str
            Stable identifier for this feature set (used as FeatureEvent.feature_set_id).
        version : str
            Schema version string.
        backend_registry : BackendRegistry | None
            Defaults to the pure-Python backend.
        """

        def __init__(
            self,
            specs: list[FeatureSpec],
            feature_set_id: str,
            version: str = "1",
            backend_registry: BackendRegistry | None = None,
        ) -> None:
            self._engine = SpecFeatureEngine(specs, backend_registry)
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
            input_types = list({s.input_type for s in self._specs})
            return FeatureSetSpec(
                feature_set_id=self._feature_set_id,
                version=self._version,
                input_types=input_types,
                output_features=[
                    FeatureFieldSpec(name=s.name, dtype="float", nullable=True)
                    for s in self._specs
                ],
                required_history=max_warmup,
            )

        def reset(self) -> None:
            self._engine.reset()

        def update(self, event: Any) -> FeatureEvent | None:
            """Process one event; return FeatureEvent when any feature is ready."""
            snapshot = self._engine.on_event(event)
            ready = snapshot.ready_values()
            if not ready:
                return None
            source_et = getattr(event, "event_type", None)
            return FeatureEvent(
                ts_event=snapshot.ts_event,
                instrument_id=snapshot.instrument_id or "unknown",
                feature_set_id=self._feature_set_id,
                feature_version=self._version,
                values=ready,
                source_event_type=source_et,
                source_event_ts=snapshot.ts_event,
            )

        def state_dict(self) -> dict:
            return self._engine.state_dict()

        def load_state_dict(self, state: dict) -> None:
            self._engine.load_state_dict(state)

    return SpecDrivenFeatureEngine


# Lazily constructed so the import of FeatureEngineBase only happens when
# the class is first used, keeping compute/ importable without the full
# nautilus_ext.features graph loaded.
_SpecDrivenFeatureEngineClass: type | None = None


def SpecDrivenFeatureEngine(
    specs: list[FeatureSpec],
    feature_set_id: str,
    version: str = "1",
    backend_registry: BackendRegistry | None = None,
):
    """Factory that returns an instance of the adapter class.

    Defers the FeatureEngineBase import until first call.
    """
    global _SpecDrivenFeatureEngineClass
    if _SpecDrivenFeatureEngineClass is None:
        _SpecDrivenFeatureEngineClass = _make_spec_driven_engine_class()
    return _SpecDrivenFeatureEngineClass(
        specs=specs,
        feature_set_id=feature_set_id,
        version=version,
        backend_registry=backend_registry,
    )
