"""Timestamp helpers for the data engine.

All event times are nanoseconds. ``to_event_time_ns`` converts a raw value (often
a numeric string from a CSV) in a given unit to nanoseconds.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
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

    Accepts numeric strings (e.g. from CSV) and ``datetime``/``date`` objects
    (e.g. a Parquet timestamp column such as Binance Vision's ``ts``). Naive
    datetimes are treated as UTC. Raises ValueError for an unsupported unit, a
    missing value, or a non-numeric value.
    """
    validate_time_unit(unit)
    if value is None or value == "":
        raise ValueError("timestamp value is required but missing")
    # Parquet timestamp columns arrive as datetime/date, not numerics. The
    # ``unit`` argument does not apply here: a datetime already names an instant.
    if isinstance(value, datetime):
        when = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
        return int(round(when.timestamp() * ONE_SECOND_NS))
    if isinstance(value, date):
        when = datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
        return int(round(when.timestamp() * ONE_SECOND_NS))
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"timestamp value is not numeric: {value!r}") from None
    return int(numeric * _TIMESTAMP_UNITS[unit])
