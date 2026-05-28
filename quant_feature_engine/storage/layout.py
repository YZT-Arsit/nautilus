"""Hive partition path construction and parsing.

We keep path construction entirely in this module so writers and readers stay
in sync. The Hive convention is ``key=value`` segments joined by ``/``; PyArrow
Dataset uses exactly the same convention so we get free predicate pruning.

Examples
--------
Raw bar partition::

    data/raw/asset_class=stock/exchange=SSE/frequency=1m/trading_date=2026-05-26/

Feature partition::

    data/features/feature_group=technical/frequency=1m/trading_date=2026-05-26/
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_KV_PATTERN = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.+)$")


@dataclass(frozen=True)
class PartitionKey:
    """A set of Hive partition values. Order-preserving so paths are stable."""

    values: tuple[tuple[str, str], ...]

    @classmethod
    def from_dict(cls, d: dict[str, str], order: tuple[str, ...]) -> "PartitionKey":
        """Build a key from a dict + a column ordering. Missing keys raise."""
        try:
            return cls(tuple((k, str(d[k])) for k in order))
        except KeyError as e:
            raise KeyError(f"Missing partition column {e.args[0]!r} in {d}") from e

    def to_path(self, root: Path | str) -> Path:
        """Append ``key=value`` segments to ``root``."""
        p = Path(root)
        for k, v in self.values:
            p = p / f"{k}={v}"
        return p

    def to_str(self) -> str:
        return "/".join(f"{k}={v}" for k, v in self.values)


def parse_partition_path(path: Path | str) -> dict[str, str]:
    """Walk a path and extract every ``key=value`` segment.

    Useful for the manifest writer: given the file we just wrote, recover its
    logical partition without needing the caller to pass it twice.
    """
    out: dict[str, str] = {}
    for part in Path(path).parts:
        m = _KV_PATTERN.match(part)
        if m:
            out[m.group(1)] = m.group(2)
    return out
