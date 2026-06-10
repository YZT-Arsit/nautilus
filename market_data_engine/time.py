"""Timestamp helpers for the data engine.

All event times are nanoseconds. ``to_event_time_ns`` converts a raw value (often
a numeric string from a CSV) in a given unit to nanoseconds.
"""
from __future__ import annotations

from typing import Any

ONE_SECOND_NS = 1_000_000_000

# unit -> nanoseconds multiplier
_TIMESTAMP_UNITS = {"ns": 1, "us": 1_000, "ms": 1_000_000, "s": ONE_SECOND_NS}
SUPPORTED_TIME_UNITS = frozenset(_TIMESTAMP_UNITS)


def validate_time_unit(unit: str) -> None:
    """Raise a clear ValueError if ``unit`` is not supported."""
    if unit not in _TIMESTAMP_UNITS:
        valid = ", ".join(_TIMESTAMP_UNITS)
        raise ValueError(f"unsupported timestamp_unit {unit!r}. Supported units: {valid}")


def to_event_time_ns(value: Any, unit: str = "ns") -> int:
    """Convert ``value`` in ``unit`` to an integer nanosecond timestamp.

    Accepts numeric strings (e.g. from CSV). Raises ValueError for an unsupported
    unit, a missing value, or a non-numeric value.
    """
    validate_time_unit(unit)
    if value is None or value == "":
        raise ValueError("timestamp value is required but missing")
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"timestamp value is not numeric: {value!r}") from None
    return int(numeric * _TIMESTAMP_UNITS[unit])
