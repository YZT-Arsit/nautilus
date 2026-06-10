"""Small, dependency-free validation helpers (no pandas)."""
from __future__ import annotations

from typing import Any


def _prefix(row_number: int | None) -> str:
    return f"row {row_number}: " if row_number is not None else ""


def require_numeric(value: Any, field_name: str, row_number: int | None = None) -> float:
    """Return ``value`` as a float, or raise ValueError naming the field/row."""
    if value is None or value == "":
        raise ValueError(f"{_prefix(row_number)}required field {field_name!r} is missing")
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ValueError(
            f"{_prefix(row_number)}field {field_name!r} is not numeric: {value!r}"
        ) from None


def optional_numeric(value: Any, default: float, field_name: str, row_number: int | None = None) -> float:
    """Return ``value`` as a float, ``default`` when absent, or raise if malformed."""
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ValueError(
            f"{_prefix(row_number)}field {field_name!r} is not numeric: {value!r}"
        ) from None
