"""
Stable public interfaces: FeatureSpec, TriggerPolicy, WarmupRequirement,
FeatureValue, FeatureUpdate, FeatureSnapshot.

Strategy code must depend only on these types — never on backend-specific
computation objects. Swapping the backend (python → rust) must require no
strategy changes.

Time unit convention
--------------------
All duration and timestamp fields in this module use **nanoseconds**. The
legacy ts_event / ts_init fields in MarketEvent use milliseconds but are
converted to nanoseconds by extract_timestamps() in timestamps.py.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# TriggerPolicy
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TriggerPolicy:
    """Policy controlling when a feature emits a new value and how it handles time.

    Parameters
    ----------
    kind : str
        One of:
        - ``on_event``        — emit on every matching event (default)
        - ``on_bar_close``    — emit when a bar closes (same as on_event for bar input)
        - ``on_timer``        — emit when interval_ns has elapsed (time-based)
        - ``on_n_events``     — emit every N events
        - ``on_n_bars``       — emit every N bars
        - ``on_window_close`` — emit when a fixed time window closes

    interval_ns : int | None
        For ``on_timer`` and ``on_window_close``: minimum nanoseconds between
        successive emissions (measured in time_semantics time).

    n : int | None
        For ``on_n_events`` and ``on_n_bars``: emit every N events/bars.

    time_semantics : str
        Which timestamp to use for time-based triggers and feature windows:
        - ``"event_time"``   — exchange/source timestamp. Default. Use for all
                               feature windows (rolling VWAP, volatility, etc.)
        - ``"receive_time"`` — local system reception time. Use for latency
                               measurement and receive-time replay.
        - ``"process_time"`` — engine processing time (wall clock). Use ONLY
                               for system latency monitoring; never for feature
                               windows unless explicitly required.

    allowed_lateness_ns : int
        Watermark safety margin (nanoseconds). Events arriving within this
        window behind the leading event_time are still considered on-time.
        Default 0 for low-latency live trading. For batch/backtest with
        moderate out-of-order data, use e.g. 5_000_000_000 (5 seconds).

    late_event_policy : str
        How to handle events whose event_time_ns < watermark_ns:
        - ``"drop"``                      — silently ignore (default, hot path safe)
        - ``"log_only"``                  — log a warning, then drop
        - ``"update_if_not_finalized"``   — update state if the late event still
                                            falls within the current rolling window
        - ``"recompute_for_backtest_only"`` — process normally during warmup/backtest;
                                              drop in live trading
    """

    kind: str = "on_event"
    interval_ns: int | None = None
    n: int | None = None
    time_semantics: str = "event_time"
    allowed_lateness_ns: int = 0
    late_event_policy: str = "drop"


# ---------------------------------------------------------------------------
# WarmupRequirement
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# FeatureSpec
# ---------------------------------------------------------------------------

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
        Unit of the window: ``"bars"``, ``"events"``, ``"nanoseconds"``,
        ``"milliseconds"``, ``"seconds"``, ``"minutes"``.
    trigger : TriggerPolicy
        When to emit a new feature value, with full time semantics.
    backend : str
        Backend identifier. ``"python"`` by default.
    params : dict
        Feature-specific parameters.  ``params["type"]`` is used by
        PythonBackend to select the concrete feature class when it cannot
        be inferred from the name.
    """

    name: str
    input_type: str = "bar"
    input_field: str | None = None
    window: int | None = None
    window_unit: str | None = None
    trigger: TriggerPolicy = field(default_factory=lambda: TriggerPolicy(kind="on_event"))
    backend: str = "python"
    params: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# FeatureValue / FeatureUpdate
# ---------------------------------------------------------------------------

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
    window_start_ns : int | None
        Start of the time window this value represents (nanoseconds POSIX).
        Populated by time-based features (VWAP with window_unit in seconds,
        etc.).  None for count-based rolling features.
    window_end_ns : int | None
        End of the time window (nanoseconds POSIX).  Equals the event_time_ns
        of the triggering event for rolling time windows.  None for
        count-based rolling features.
    source_event_time_ns : int | None
        event_time_ns (or configured time_semantics timestamp) of the event
        that produced this value.  Set on every update so downstream code
        can trace which event triggered the computation.
    """

    name: str
    value: float | int | bool | None
    is_ready: bool
    window_start_ns: int | None = None
    window_end_ns: int | None = None
    source_event_time_ns: int | None = None


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


# ---------------------------------------------------------------------------
# FeatureSnapshot
# ---------------------------------------------------------------------------

@dataclass
class FeatureSnapshot:
    """All feature values for one instrument at one point in time.

    All timestamp fields are in **nanoseconds**. The ``ts_event`` field
    holds ``event_time_ns`` (the exchange/source timestamp) for consistency
    with the primary time semantics used by feature windows.

    Parameters
    ----------
    ts_event : int
        event_time_ns — exchange/source timestamp in nanoseconds.
    instrument_id : str | None
        Instrument identifier from the source event.
    values : dict[str, FeatureValue]
        All features keyed by FeatureSpec.name.
    receive_time_ns : int | None
        Local system reception timestamp (nanoseconds). Preserved for
        latency measurement and receive-time replay.
    process_time_ns : int | None
        Timestamp when SpecFeatureEngine stamped the event during on_event().
        None if not stamped (warmup mode, or engine not configured to stamp).
    """

    ts_event: int               # event_time_ns (nanoseconds)
    instrument_id: str | None
    values: dict[str, FeatureValue]
    receive_time_ns: int | None = None
    process_time_ns: int | None = None

    # ------------------------------------------------------------------
    # Convenience accessors
    # ------------------------------------------------------------------

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

    def latency_ns(self) -> int | None:
        """receive_time_ns - ts_event. None if receive_time_ns absent."""
        if self.receive_time_ns is None:
            return None
        return self.receive_time_ns - self.ts_event

    def processing_latency_ns(self) -> int | None:
        """process_time_ns - receive_time_ns. None if either absent."""
        if self.process_time_ns is None or self.receive_time_ns is None:
            return None
        return self.process_time_ns - self.receive_time_ns

    def __len__(self) -> int:
        return len(self.values)
