"""Train/validation/test split assignment + horizon purge (pure-Python).

Splits are assigned by the **UTC calendar date** of each bar's
``event_time_ns``. The ranges are non-overlapping and contiguous:

    train:      2024-06-17 .. 2025-12-31
    validation: 2026-01-01 .. 2026-04-30
    test:       2026-05-01 .. 2026-06-16

Purge/embargo: because features are causal (only past bars), the only
cross-split leakage risk is a **label** whose horizon bar ``t+H`` falls in a
different split than bar ``t`` (e.g. a late-train row whose 15-bar forward
return reads validation prices). :func:`purge_mask` flags exactly those rows for
dropping. A forward-purge is sufficient for causal features; no leading-edge
embargo of the next split is needed (the next split's features only read their
own past, which is legitimately available live).
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

# name -> (start_date_inclusive, end_date_inclusive) as ISO strings.
DEFAULT_SPLITS: dict[str, tuple[str, str]] = {
    "train": ("2024-06-17", "2025-12-31"),
    "validation": ("2026-01-01", "2026-04-30"),
    "test": ("2026-05-01", "2026-06-16"),
}

_NS_PER_S = 1_000_000_000


def _ts_to_date(ts_ns: int) -> date:
    return datetime.fromtimestamp(int(ts_ns) // _NS_PER_S, tz=timezone.utc).date()


def _bounds(splits: dict[str, tuple[str, str]]) -> dict[str, tuple[date, date]]:
    return {k: (date.fromisoformat(a), date.fromisoformat(b)) for k, (a, b) in splits.items()}


def validate_splits(splits: dict[str, tuple[str, str]] = DEFAULT_SPLITS) -> None:
    """Raise ValueError if any two split date ranges overlap."""
    b = _bounds(splits)
    items = list(b.items())
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            (n1, (a1, e1)), (n2, (a2, e2)) = items[i], items[j]
            if a1 <= e2 and a2 <= e1:
                raise ValueError(f"split ranges overlap: {n1} and {n2}")


def split_of_ts(ts_ns: int, splits: dict[str, tuple[str, str]] = DEFAULT_SPLITS) -> str | None:
    """Return the split name for a timestamp, or ``None`` if outside all ranges."""
    d = _ts_to_date(ts_ns)
    for name, (a, e) in _bounds(splits).items():
        if a <= d <= e:
            return name
    return None


def assign_splits(ts_list: list[int], splits: dict[str, tuple[str, str]] = DEFAULT_SPLITS) -> list:
    """Vectorless split assignment for a list of timestamps."""
    b = _bounds(splits)

    def _assign(ts_ns: int) -> str | None:
        d = _ts_to_date(ts_ns)
        for name, (a, e) in b.items():
            if a <= d <= e:
                return name
        return None

    return [_assign(t) for t in ts_list]


def purge_mask(
    row_splits: list,
    horizon_ts_list: list,
    splits: dict[str, tuple[str, str]] = DEFAULT_SPLITS,
) -> list[bool]:
    """Return a per-row mask where ``True`` == drop (label horizon crosses split).

    A row is purged iff it has a horizon bar (``horizon_ts`` not ``None``) whose
    split differs from the row's split - including the case where the horizon
    bar falls outside all ranges. Rows without a horizon (last H bars) return
    ``False`` here; they are already dropped as horizon-invalid by the label.
    """
    b = _bounds(splits)

    def _split(ts_ns: int) -> str | None:
        d = _ts_to_date(ts_ns)
        for name, (a, e) in b.items():
            if a <= d <= e:
                return name
        return None

    out: list[bool] = []
    for rs, hts in zip(row_splits, horizon_ts_list):
        if hts is None:
            out.append(False)
        else:
            out.append(_split(int(hts)) != rs)
    return out


def split_summary(row_splits: list) -> dict[str, int]:
    """Count rows per split (``None`` reported under the key ``'none'``)."""
    counts: dict[str, int] = {}
    for s in row_splits:
        k = s if s is not None else "none"
        counts[k] = counts.get(k, 0) + 1
    return counts
