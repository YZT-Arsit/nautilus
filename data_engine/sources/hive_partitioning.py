"""Hive-partition fragment selection for unified Parquet roots (pyarrow, no pandas).

A single ``market_data`` root can hold several partition layouts side by side --
for example bars under ``bar_type=...`` and trades under ``data_type=...``.  When
that happens, pyarrow's *global* dataset schema may be inferred from a fragment of
the **wrong** layout, which breaks a per-loader schema guard (e.g. a bar loader
sees a trade fragment's schema and reports ``close`` missing, or vice versa).

These helpers select only the fragments whose Hive ``key=value`` path segments
match the requested equality ``filters``, so each loader can run its schema guard
and column projection against its own layout's fragments.

This module imports no ``nautilus_trader`` -- it is part of the self-owned
``data_engine`` layer.
"""
from __future__ import annotations

from typing import Any


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
