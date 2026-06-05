"""
Stable public interfaces: FeatureSpec, TriggerPolicy, WarmupRequirement,
FeatureValue, FeatureUpdate, FeatureSnapshot.

Strategy code must depend only on these types — never on backend-specific
computation objects. Swapping the backend (python → rust) must require no
strategy changes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class TriggerPolicy:
    """Policy controlling when a feature emits a new value.

    Parameters
    ----------
    kind : str
        One of: ``on_event``, ``on_bar_close``, ``on_timer``,
        ``on_n_events``, ``on_n_bars``, ``on_window_close``.
    n : int | None
        For ``on_n_events`` and ``on_n_bars``: emit every N events/bars.
    interval_ms : int | None
        For ``on_timer`` and ``on_window_close``: minimum ms between emissions.
    """

    kind: str = "on_event"
    n: int | None = None
    interval_ms: int | None = None


@dataclass(frozen=True)
class WarmupRequirement:
    """Minimum historical data a feature needs before it is reliable.

    Parameters
    ----------
    n_events : int
        Events to replay during warmup before ``is_ready`` becomes True.
    unit : str
        Unit for n_events: ``"bars"``, ``"events"``, ``"seconds"``, etc.
    mandatory : bool
        If True, ``is_ready`` stays False until n_events are processed.
        If False, the feature may emit values before warmup completes (e.g. EWMA).
    """

    n_events: int
    unit: str = "bars"
    mandatory: bool = True


@dataclass(frozen=True)
class FeatureSpec:
    """Stable specification for one incremental feature.

    Every field is part of the public interface seen by strategy code.
    The ``backend`` and ``params`` fields control the implementation without
    leaking implementation details to callers.

    Parameters
    ----------
    name : str
        Stable feature name used as the dict key in FeatureSnapshot.
        Example: ``"rolling_mean_close_20"``.
    input_type : str
        Market event type: ``"bar"``, ``"trade"``, ``"quote"``,
        ``"book_delta"``, or ``"timer"``.
    input_field : str | None
        Specific event field to extract (e.g. ``"close"``). None for
        features that consume multiple fields (VWAP, spread, imbalance).
    window : int | None
        Lookback window size in ``window_unit`` units.
    window_unit : str | None
        Unit of the window: ``"bars"``, ``"events"``, ``"seconds"``, etc.
    trigger : TriggerPolicy
        When to emit a new feature value.
    backend : str
        Backend identifier. ``"python"`` by default. Other backends
        (``"numpy"``, ``"polars"``, ``"rust"``) can be registered via
        BackendRegistry without changing the strategy API.
    params : dict
        Feature-specific parameters.  ``params["type"]`` is used by
        PythonBackend to select the concrete feature class when it cannot
        be inferred from the name.
        Example: ``{"type": "rolling_mean"}``.
    """

    name: str
    input_type: str = "bar"
    input_field: str | None = None
    window: int | None = None
    window_unit: str | None = None
    trigger: TriggerPolicy = field(default_factory=lambda: TriggerPolicy(kind="on_event"))
    backend: str = "python"
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FeatureValue:
    """One feature's value at a single point in time.

    Parameters
    ----------
    name : str
        Feature name (mirrors FeatureSpec.name).
    value : float | int | bool | None
        Computed value; None when the feature is not yet ready.
    is_ready : bool
        True when the feature has processed enough events for reliable output.
    """

    name: str
    value: float | int | bool | None
    is_ready: bool


@dataclass(frozen=True)
class FeatureUpdate:
    """Returned by FeatureBase.update() after processing one event.

    Parameters
    ----------
    value : FeatureValue
        Latest value (may be unchanged if the trigger did not fire).
    triggered : bool
        True when the trigger policy fired and a new value was computed.
    """

    value: FeatureValue
    triggered: bool


@dataclass
class FeatureSnapshot:
    """All feature values for one instrument at one point in time.

    Produced by SpecFeatureEngine.on_event(). Stable across backend changes —
    strategy code should depend on this type, not on FeatureEvent.

    Parameters
    ----------
    ts_event : int
        Millisecond POSIX timestamp of the triggering market event.
    instrument_id : str | None
        Instrument identifier from the source event.
    values : dict[str, FeatureValue]
        All features keyed by FeatureSpec.name.
    """

    ts_event: int
    instrument_id: str | None
    values: dict[str, FeatureValue]

    def get(self, name: str) -> FeatureValue | None:
        """Return the FeatureValue for a named feature, or None if absent."""
        return self.values.get(name)

    def scalar(self, name: str) -> float | int | bool | None:
        """Return the raw scalar for a feature, or None if absent or not ready."""
        fv = self.values.get(name)
        return fv.value if fv is not None else None

    def to_dict(self) -> dict[str, float | int | bool | None]:
        """All values as a plain dict (None for unready features)."""
        return {k: v.value for k, v in self.values.items()}

    def ready_values(self) -> dict[str, float | int | bool | None]:
        """Only ready features as a plain dict."""
        return {k: v.value for k, v in self.values.items() if v.is_ready}

    def all_ready(self) -> bool:
        """True when every feature in this snapshot is ready."""
        return all(v.is_ready for v in self.values.values())

    def __len__(self) -> int:
        return len(self.values)
