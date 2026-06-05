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
    engine at processing time using clock.now_ns()). Use only for system
    latency monitoring — never for feature window computation unless a
    feature explicitly declares process-time semantics.

Extraction fallback chain
-------------------------
For events that do not yet carry ns-precision timestamps (e.g. legacy
BarInput with only ts_event in milliseconds), the helpers fall back
gracefully:

    event_time_ns  = event.event_time_ns
                  or convert_legacy_ts_event_to_ns(event.ts_event, config.legacy_ts_event_unit)
                  or 0

    receive_time_ns = event.receive_time_ns
                   or event_time_ns               (assume no latency if absent)

Legacy ts_event units
---------------------
Different data vendors use different units for the legacy ts_event field.
Configure via TimestampConfig:

    TimestampConfig(legacy_ts_event_unit="ms")  # NautilusTrader default
    TimestampConfig(legacy_ts_event_unit="us")  # microseconds
    TimestampConfig(legacy_ts_event_unit="ns")  # nanoseconds (no conversion)

Production strictness
---------------------
In live mode, if require_event_time_ns_for_live=True and an event lacks
event_time_ns, extract_timestamps raises RuntimeError with a clear message.
This acts as a data-quality gate so production pipelines catch misconfigured
feeds at startup rather than silently computing wrong feature windows.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# TimestampConfig
# ---------------------------------------------------------------------------

_LEGACY_TS_MULTIPLIERS: dict[str, int] = {
    "ns": 1,
    "us": 1_000,
    "ms": 1_000_000,
}


@dataclass(frozen=True)
class TimestampConfig:
    """Configuration for legacy ts_event unit conversion.

    Parameters
    ----------
    legacy_ts_event_unit : str
        Unit of the legacy ts_event field when event_time_ns is absent.
        One of ``"ns"``, ``"us"``, ``"ms"`` (default ``"ms"`` — the
        NautilusTrader convention).
    require_event_time_ns_for_live : bool
        If True and the event lacks event_time_ns, ``extract_timestamps``
        raises RuntimeError when ``is_live=True``.  Default False (silent
        fallback to legacy ts_event conversion).  Enable in production to
        catch misconfigured feeds early.
    """

    legacy_ts_event_unit: str = "ms"  # "ns" | "us" | "ms"
    require_event_time_ns_for_live: bool = False


def convert_legacy_ts_event_to_ns(value: int, unit: str) -> int:
    """Convert a legacy ts_event integer to nanoseconds.

    Parameters
    ----------
    value : int
        The ts_event value in the source unit.
    unit : str
        Source unit: ``"ns"``, ``"us"``, or ``"ms"``.

    Returns
    -------
    int
        Equivalent value in nanoseconds.

    Raises
    ------
    ValueError
        If ``unit`` is not one of the recognised units.
    """
    multiplier = _LEGACY_TS_MULTIPLIERS.get(unit)
    if multiplier is None:
        raise ValueError(
            f"Unknown legacy_ts_event_unit {unit!r}; expected one of "
            f"{sorted(_LEGACY_TS_MULTIPLIERS)}"
        )
    return int(value) * multiplier


_DEFAULT_CONFIG = TimestampConfig()


# ---------------------------------------------------------------------------
# EventTimestamps
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Extraction helpers
# ---------------------------------------------------------------------------

def extract_timestamps(
    event: Any,
    config: TimestampConfig | None = None,
    *,
    is_live: bool = False,
) -> EventTimestamps:
    """Extract an EventTimestamps from any duck-typed event.

    Falls back from ns-precision fields to the legacy ts_event field,
    converting according to ``config.legacy_ts_event_unit``.
    process_time_ns is always None here — the engine stamps it later.

    Parameters
    ----------
    event : Any
        Any object with optional ``event_time_ns``, ``receive_time_ns``,
        ``ts_event`` attributes.
    config : TimestampConfig | None
        Conversion config.  Defaults to ``TimestampConfig()`` (ms legacy,
        no strict live check).
    is_live : bool
        When True and ``config.require_event_time_ns_for_live`` is True,
        raise RuntimeError if ``event_time_ns`` is absent from the event.

    Returns
    -------
    EventTimestamps
        With event_time_ns and receive_time_ns populated.

    Raises
    ------
    RuntimeError
        If ``is_live=True`` and ``config.require_event_time_ns_for_live=True``
        and the event lacks ``event_time_ns``.
    """
    cfg = config if config is not None else _DEFAULT_CONFIG

    # Primary: nanosecond-precision exchange timestamp
    et = getattr(event, "event_time_ns", None)
    if et is None:
        if is_live and cfg.require_event_time_ns_for_live:
            raise RuntimeError(
                f"event_time_ns is required in live mode but was not set on "
                f"{type(event).__name__!r}. Set event_time_ns on the event, or "
                f"disable require_event_time_ns_for_live in TimestampConfig."
            )
        ts_legacy = getattr(event, "ts_event", None) or 0
        et = convert_legacy_ts_event_to_ns(int(ts_legacy), cfg.legacy_ts_event_unit)

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
