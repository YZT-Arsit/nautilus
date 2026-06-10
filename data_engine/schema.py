"""Minimal bar schema constants.

Documents the field contract for bar events without over-engineering. Sources
use these names as defaults; configs may override the source column names.
"""
from __future__ import annotations

# The field every bar must carry.
BAR_REQUIRED_FIELDS = ("close",)

# Filled with sensible defaults when absent (open/high/low -> close, volume -> 0).
BAR_OPTIONAL_FIELDS = ("open", "high", "low", "volume")

# Canonical event-time field name (nanoseconds).
EVENT_TIME_FIELD = "event_time_ns"
