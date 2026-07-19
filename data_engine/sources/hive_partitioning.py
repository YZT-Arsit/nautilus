"""Hive-fragment selection for the locked ``market_data`` layout.

Every market partition uses exactly
``asset_class/exchange/venue_type/symbol/data_type/freq/date``. Bars and trades
coexist under that one schema and are separated by ``data_type``.

These helpers select only the fragments whose Hive ``key=value`` path segments
match the requested equality ``filters``, so each loader can run its schema guard
and column projection against its own layout's fragments.

This module imports no ``nautilus_trader`` -- it is part of the self-owned
``data_engine`` layer.
"""
from __future__ import annotations

from typing import Any

LOCKED_MARKET_PARTITION_KEYS = (
    "asset_class", "exchange", "venue_type", "symbol", "data_type", "freq", "date",
)
REQUIRED_MARKET_FILTER_KEYS = LOCKED_MARKET_PARTITION_KEYS[:-1]


def hive_partition_values(path: str) -> dict[str, str]:
    """Parse Hive ``key=value`` directory segments out of a fragment path.

    Handles both POSIX ``/`` and Windows ``\\`` separators.
    """
    values: dict[str, str] = {}
    for segment in path.replace("\\", "/").split("/"):
        key, sep, value = segment.partition("=")
        if sep:
            values[key] = value
    return values


def matching_fragments(dataset, filters: dict[str, Any]) -> list:
    """Return the dataset fragments whose Hive partition values satisfy ``filters``.

    Matching is pure path-segment equality (string compare), independent of how
    pyarrow would prune -- a fragment from a different layout simply lacks the
    filtered key and is dropped.  Selecting fragments *before* the schema guard is
    what lets a loader ignore a foreign partition layout under a unified root.

    With empty ``filters`` every fragment matches; the caller decides whether that
    is meaningful for a mixed root (it raises a clear schema error downstream if
    the first fragment is of the wrong layout).
    """
    matched = []
    for fragment in dataset.get_fragments():
        parts = hive_partition_values(fragment.path)
        if all(str(parts.get(key)) == str(value) for key, value in filters.items()):
            matched.append(fragment)
    return matched


def validate_market_filters(filters: dict[str, Any], *, data_type: str) -> None:
    """Require a fully-qualified locked-layout selector (date remains optional)."""
    missing = [key for key in REQUIRED_MARKET_FILTER_KEYS if key not in filters]
    if missing:
        raise ValueError(f"market_data filters missing locked partition keys: {missing}")
    if str(filters["data_type"]) != data_type:
        raise ValueError(
            f"market_data loader requires data_type={data_type!r}, "
            f"got {filters['data_type']!r}"
        )
    extra = [key for key in filters if key not in LOCKED_MARKET_PARTITION_KEYS]
    if extra:
        raise ValueError(f"unsupported market_data partition filters: {extra}")


def select_date_window_fragments(fragments, start, end, warmup_rows: int = 0) -> list:
    """Select the live date window plus enough immediately preceding partitions.

    ``count_rows`` reads Parquet metadata, not column data. This keeps warmup
    outside the requested live window without scanning the full history.
    """
    dated = []
    for fragment in fragments:
        value = hive_partition_values(fragment.path).get("date")
        if value is None:
            raise ValueError(f"locked market_data fragment has no date partition: {fragment.path}")
        dated.append((value, fragment))

    live = [fragment for value, fragment in dated
            if (start is None or value >= start.isoformat())
            and (end is None or value <= end.isoformat())]
    if not live:
        return []
    if start is None or warmup_rows <= 0:
        return live

    prior = sorted(
        ((value, fragment) for value, fragment in dated if value < start.isoformat()),
        key=lambda item: item[0],
        reverse=True,
    )
    selected_prior = []
    rows = 0
    for _value, fragment in prior:
        selected_prior.append(fragment)
        rows += fragment.count_rows()
        if rows >= warmup_rows:
            break
    return list(reversed(selected_prior)) + live
