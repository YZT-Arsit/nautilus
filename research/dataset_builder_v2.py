"""Assemble V2 features + labels + splits into a training dataset (pure-Python).

Mirrors :mod:`research.dataset_builder` but uses the V2 feature set
(:mod:`research.features_v2`), which requires the extended bar columns
(``quote_volume``, ``trade_count``, ``taker_buy_volume``,
``taker_buy_quote_volume``). The label/split logic is reused unchanged
(``label_builder``, ``splits``); only the feature matrix differs. The V1 builder
is left untouched. Pure-Python numeric core (no numpy/pandas); pandas/pyarrow are
used only by the optional parquet part-writer. Imports no nautilus_trader.

Initial label is the current one (horizon 15, symmetric threshold 0.0015) - the
label sweep is a later phase. ``build_dataset_v2`` has the same call signature as
``build_dataset`` so it can be passed as ``build_fn`` to the month-chunked
``dataset_writer.build_dataset_partitioned``.
"""
from __future__ import annotations

import math
from typing import Any

from research.features_v2 import FEATURE_COLUMNS_V2, compute_features_v2
from research.label_builder import (
    DEFAULT_BUFFER,
    DEFAULT_FEE_RATE,
    DEFAULT_HORIZON,
    build_labels,
    label_distribution,
)
from research.splits import DEFAULT_SPLITS, assign_splits, purge_mask, split_summary

_BASE_COLUMNS = [
    "event_time_ns", "instrument_id", "split", "close_t", "label_horizon",
    "label_horizon_ts", "future_return_15m", "label_class", "label_code", "is_valid",
]
DATASET_COLUMNS_V2: list[str] = _BASE_COLUMNS + FEATURE_COLUMNS_V2

# Output dtype spec (features float32; base columns match V1).
DTYPE_SPEC_V2: dict[str, str] = {
    **{name: "float32" for name in FEATURE_COLUMNS_V2},
    "event_time_ns": "int64",
    "label_horizon_ts": "int64",
    "close_t": "float64",
    "future_return_15m": "float64",
    "label_code": "int8",
    "label_horizon": "int16",
    "is_valid": "bool",
    "split": "category",
    "label_class": "category",
    "instrument_id": "category",
}


def _is_nan(x: Any) -> bool:
    return isinstance(x, float) and math.isnan(x)


def build_dataset_v2(
    bars: Any,
    *,
    horizon: int = DEFAULT_HORIZON,
    fee_rate: float = DEFAULT_FEE_RATE,
    buffer: float = DEFAULT_BUFFER,
    splits: dict[str, tuple[str, str]] = DEFAULT_SPLITS,
    default_instrument_id: str = "UNKNOWN",
) -> tuple[list[dict], dict]:
    """Build the (rows, summary) V2 dataset from an extended bar series."""
    from research.features import to_columns  # local import; pure-Python

    cols = to_columns(bars)
    n_raw = len(cols.get("close", []))

    order = sorted(range(n_raw), key=lambda i: int(cols["event_time_ns"][i]))
    scols = {k: [v[i] for i in order] for k, v in cols.items()}
    iids = scols.get("instrument_id") or [default_instrument_id] * n_raw

    feats = compute_features_v2(scols)            # raises KeyError if order-flow cols missing
    labels = build_labels(scols, horizon=horizon, fee_rate=fee_rate, buffer=buffer)
    row_splits = assign_splits([int(t) for t in scols["event_time_ns"]], splits)
    purge = purge_mask(row_splits, labels["label_horizon_ts"], splits)

    def _finite(t: int) -> bool:
        return all(not _is_nan(feats[name][t]) for name in FEATURE_COLUMNS_V2)

    finite = [_finite(t) for t in range(n_raw)]
    first_valid = next((t for t in range(n_raw) if finite[t]), n_raw)
    nan_counts = {name: sum(1 for v in feats[name] if _is_nan(v)) for name in FEATURE_COLUMNS_V2}

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
            dropped["warmup" if t < first_valid else "nan_feature"] += 1
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
        for name in FEATURE_COLUMNS_V2:
            row[name] = feats[name][t]
        rows.append(row)

    by_split: dict[str, dict] = {}
    for r in rows:
        d = by_split.setdefault(r["split"], {"LONG": 0, "SHORT": 0, "NO_TRADE": 0})
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
        "label_distribution_total": label_distribution([r["label_class"] for r in rows]),
        "label_distribution_by_split": by_split,
        "feature_columns": list(FEATURE_COLUMNS_V2),
        "nan_counts": nan_counts,
        "first_ts": min(kept_ts) if kept_ts else None,
        "last_ts": max(kept_ts) if kept_ts else None,
        "first_valid_index": first_valid,
        "horizon": horizon,
        "label_threshold": 2.0 * float(fee_rate) + float(buffer),
    }
    return rows, summary


def parquet_part_writer_v2(rows: list[dict], dest) -> None:
    """Typed V2 parquet part writer (requires pandas+pyarrow)."""
    import pandas as pd  # noqa: PLC0415

    df = pd.DataFrame(rows, columns=DATASET_COLUMNS_V2)
    for col, dt in DTYPE_SPEC_V2.items():
        if col in df.columns:
            df[col] = df[col].astype(dt)
    df.to_parquet(dest, index=False)
