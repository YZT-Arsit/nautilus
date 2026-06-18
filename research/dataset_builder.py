"""Assemble features + labels + splits into a training dataset (pure-Python).

Pipeline (all point-in-time; the only future read is the label horizon):

    bars -> sort by time -> compute features -> compute labels -> assign splits
         -> purge label-horizon-crossing rows -> drop warmup / horizon / nan
         -> dataset rows + summary

The numeric core is pure-Python so it runs without numpy/pandas. pandas/pyarrow
are optional and only used by the convenience helpers (:func:`to_dataframe`,
:func:`to_parquet`) - never called by the builder itself, so a missing pandas
never breaks dataset construction. This module imports **no** ``nautilus_trader``
and touches no network/exchange/account API.
"""
from __future__ import annotations

import math
from typing import Any

from research.features import FEATURE_COLUMNS, compute_features, to_columns
from research.label_builder import (
    DEFAULT_BUFFER,
    DEFAULT_FEE_RATE,
    DEFAULT_HORIZON,
    build_labels,
    label_distribution,
)
from research.splits import DEFAULT_SPLITS, assign_splits, purge_mask, split_summary

# Non-feature dataset columns (in order), followed by the f_* feature columns.
_BASE_COLUMNS = [
    "event_time_ns",
    "instrument_id",
    "split",
    "close_t",
    "label_horizon",
    "label_horizon_ts",
    "future_return_15m",
    "label_class",
    "label_code",
    "is_valid",
]
DATASET_COLUMNS: list[str] = _BASE_COLUMNS + FEATURE_COLUMNS


def _is_nan(x: Any) -> bool:
    return isinstance(x, float) and math.isnan(x)


def build_dataset(
    bars: Any,
    *,
    horizon: int = DEFAULT_HORIZON,
    fee_rate: float = DEFAULT_FEE_RATE,
    buffer: float = DEFAULT_BUFFER,
    splits: dict[str, tuple[str, str]] = DEFAULT_SPLITS,
    default_instrument_id: str = "UNKNOWN",
) -> tuple[list[dict], dict]:
    """Build the (rows, summary) dataset from a bar series.

    ``bars`` may be a pandas DataFrame, dict-of-lists, or list-of-dicts with at
    least open/high/low/close/volume/event_time_ns (instrument_id optional).
    Returns ``(rows, summary)`` where ``rows`` is a list of dicts keyed by
    :data:`DATASET_COLUMNS` (only kept rows) and ``summary`` is a stats dict.
    """
    cols = to_columns(bars)
    n_raw = len(cols.get("close", []))

    # 1. stable sort by event_time_ns (reindex every column).
    order = sorted(range(n_raw), key=lambda i: int(cols["event_time_ns"][i]))
    scols = {k: [v[i] for i in order] for k, v in cols.items()}
    iids = scols.get("instrument_id") or [default_instrument_id] * n_raw

    # 2. features + labels + splits over the full (sorted) series.
    feats = compute_features(scols)
    labels = build_labels(scols, horizon=horizon, fee_rate=fee_rate, buffer=buffer)
    row_splits = assign_splits([int(t) for t in scols["event_time_ns"]], splits)
    purge = purge_mask(row_splits, labels["label_horizon_ts"], splits)

    # 3. empirical first-valid index: first row with all features finite. NaN rows
    #    before it are warmup; NaN rows at/after it are degenerate (nan_feature).
    def _finite(t: int) -> bool:
        return all(not _is_nan(feats[name][t]) for name in FEATURE_COLUMNS)

    finite = [_finite(t) for t in range(n_raw)]
    first_valid = next((t for t in range(n_raw) if finite[t]), n_raw)

    # 4. per-feature NaN counts over RAW rows (diagnostic).
    nan_counts = {name: sum(1 for v in feats[name] if _is_nan(v)) for name in FEATURE_COLUMNS}

    # 5. classify each row to keep or a single drop reason (priority order).
    dropped = {"no_split": 0, "horizon": 0, "purge": 0, "warmup": 0, "nan_feature": 0}
    rows: list[dict] = []
    for t in range(n_raw):
        if row_splits[t] is None:
            dropped["no_split"] += 1
            continue
        if not labels["is_valid_label"][t]:
            dropped["horizon"] += 1
            continue
        if purge[t]:
            dropped["purge"] += 1
            continue
        if not finite[t]:
            if t < first_valid:
                dropped["warmup"] += 1
            else:
                dropped["nan_feature"] += 1
            continue
        row = {
            "event_time_ns": int(scols["event_time_ns"][t]),
            "instrument_id": iids[t] if iids[t] is not None else default_instrument_id,
            "split": row_splits[t],
            "close_t": float(scols["close"][t]),
            "label_horizon": labels["label_horizon"][t],
            "label_horizon_ts": labels["label_horizon_ts"][t],
            "future_return_15m": labels["future_return_15m"][t],
            "label_class": labels["label_class"][t],
            "label_code": labels["label_code"][t],
            "is_valid": True,
        }
        for name in FEATURE_COLUMNS:
            row[name] = feats[name][t]
        rows.append(row)

    # 6. summary.
    kept_classes = [r["label_class"] for r in rows]
    by_split: dict[str, dict] = {}
    for r in rows:
        sp = r["split"]
        d = by_split.setdefault(sp, {"LONG": 0, "SHORT": 0, "NO_TRADE": 0})
        d[r["label_class"]] += 1
    kept_ts = [r["event_time_ns"] for r in rows]
    summary = {
        "raw_rows": n_raw,
        "output_rows": len(rows),
        "dropped_warmup_rows": dropped["warmup"],
        "dropped_horizon_rows": dropped["horizon"],
        "dropped_purge_rows": dropped["purge"],
        "dropped_nan_feature_rows": dropped["nan_feature"],
        "dropped_no_split_rows": dropped["no_split"],
        "split_counts": split_summary([r["split"] for r in rows]),
        "label_distribution_total": label_distribution(kept_classes),
        "label_distribution_by_split": by_split,
        "feature_columns": list(FEATURE_COLUMNS),
        "nan_counts": nan_counts,
        "first_ts": min(kept_ts) if kept_ts else None,
        "last_ts": max(kept_ts) if kept_ts else None,
        "first_valid_index": first_valid,
        "horizon": horizon,
        "label_threshold": 2.0 * float(fee_rate) + float(buffer),
    }
    return rows, summary


# --- optional I/O helpers (NOT used by the builder or the Phase A tests) -----

def to_dataframe(rows: list[dict]):
    """Convert dataset rows to a pandas DataFrame (requires pandas)."""
    import pandas as pd  # noqa: PLC0415

    return pd.DataFrame(rows, columns=DATASET_COLUMNS)


def to_parquet(rows: list[dict], path: str) -> str:
    """Write dataset rows to a parquet file (requires pandas+pyarrow). Explicit only."""
    df = to_dataframe(rows)
    df.to_parquet(path, index=False)
    return path
