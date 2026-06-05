"""
Timestamp extraction and representation for the compute feature layer.

Three timestamps are distinguished:

event_time_ns
    Assigned by the exchange or data source. Represents when the market
    event happened. Use this for feature windows (rolling VWAP, volatility,
    bar-close features). This is the "logical time" of the event.

receive_time_ns
    When the local system received the event from the network or feed.
    Always >= event_time_ns in live trading (network + processing latency).
    Preserved for latency measurement, realistic backtest replay, and
    infrastructure monitoring.

process_time_ns
    When SpecFeatureEngine.on_event() processed the event (stamped by the
    engine at processing time using time.time_ns()). Use only for system
    latency monitoring — never for feature window computation unless a
    feature explicitly declares process-time semantics.

Extraction fallback chain
-------------------------
For events that do not yet carry ns-precision timestamps (e.g. legacy
BarInput with only ts_event in milliseconds), the helpers fall back
gracefully:

    event_time_ns  = event.event_time_ns
                  or event.ts_event * 1_000_000   (ms → ns)
                  or 0

    receive_time_ns = event.receive_time_ns
                   or event_time_ns               (assume no latency if absent)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class EventTimestamps:
    """Three-field timestamp bundle for one market event.

    Parameters
    ----------
    event_time_ns : int
        Exchange/source timestamp (nanoseconds POSIX).
    receive_time_ns : int
        Local system reception timestamp (nanoseconds POSIX).
    process_time_ns : int | None
        When SpecFeatureEngine stamped the event during on_event().
        None until the engine processes it.
    """

    event_time_ns: int
    receive_time_ns: int
    process_time_ns: int | None = None

    @property
    def latency_ns(self) -> int:
        """Network + feed latency: receive_time_ns - event_time_ns."""
        return self.receive_time_ns - self.event_time_ns

    @property
    def processing_latency_ns(self) -> int | None:
        """Engine processing latency: process_time_ns - receive_time_ns."""
        if self.process_time_ns is None:
            return None
        return self.process_time_ns - self.receive_time_ns


def extract_timestamps(event: Any) -> EventTimestamps:
    """Extract an EventTimestamps from any duck-typed event.

    Falls back from ns-precision fields to ms-precision ts_event.
    process_time_ns is always None here — the engine stamps it later.

    Parameters
    ----------
    event : Any
        Any object with optional event_time_ns, receive_time_ns, ts_event attrs.

    Returns
    -------
    EventTimestamps
        With event_time_ns and receive_time_ns populated.
    """
    # Primary: nanosecond-precision exchange timestamp
    et = getattr(event, "event_time_ns", None)
    if et is None:
        # Legacy fallback: ts_event is milliseconds → convert
        ts_ms = getattr(event, "ts_event", None) or 0
        et = int(ts_ms) * 1_000_000

    # Primary: nanosecond-precision receive timestamp
    rt = getattr(event, "receive_time_ns", None)
    if rt is None:
        rt = et  # assume no network latency when field is absent

    return EventTimestamps(event_time_ns=int(et), receive_time_ns=int(rt))


def select_timestamp(ts: EventTimestamps, semantics: str) -> int:
    """Return the appropriate timestamp field based on time_semantics.

    Parameters
    ----------
    ts : EventTimestamps
    semantics : str
        One of ``"event_time"``, ``"receive_time"``, ``"process_time"``.

    Returns
    -------
    int
        Nanosecond timestamp for the given semantics.
        Falls back to event_time_ns if the requested field is None.
    """
    if semantics == "receive_time":
        return ts.receive_time_ns
    if semantics == "process_time":
        return ts.process_time_ns if ts.process_time_ns is not None else ts.event_time_ns
    return ts.event_time_ns  # "event_time" (default)
