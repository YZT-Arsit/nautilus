"""Dataset builder for B3-label variants (pure-Python; additive).

Same V2 feature matrix and output schema as :mod:`research.dataset_builder_v2`,
but the label is produced by :func:`research.label_builder_v2.build_labels_variant`
(configurable horizon / asymmetric thresholds / long-only-binary). Reuses the V2
parquet schema (``DATASET_COLUMNS_V2`` / ``DTYPE_SPEC_V2`` /
``parquet_part_writer_v2``) so multiclass variant datasets are consumable by the
existing sklearn/LightGBM/threshold-analysis stack unchanged. ``build_dataset_variant``
shares the ``build_dataset_v2`` call signature (plus task/threshold kwargs) so a
``functools.partial`` of it works as ``build_fn`` for the month-chunked writer.

No features read the future; the label's ``t+H`` is the only future read; no test
tail is read (validation's last H rows drop as horizon-invalid).
"""
from __future__ import annotations

import math
from typing import Any

from research.dataset_builder_v2 import DATASET_COLUMNS_V2, DTYPE_SPEC_V2, parquet_part_writer_v2  # noqa: F401
from research.features_v2 import FEATURE_COLUMNS_V2, compute_features_v2
from research.label_builder import DEFAULT_BUFFER, DEFAULT_FEE_RATE
from research.label_builder_v2 import (
    DEFAULT_HORIZON,
    DEFAULT_LONG_THRESHOLD,
    DEFAULT_SHORT_THRESHOLD,
    MULTICLASS,
    build_labels_variant,
    label_distribution_variant,
    task_codes,
)
from research.splits import DEFAULT_SPLITS, assign_splits, purge_mask, split_summary


def _is_nan(x: Any) -> bool:
    return isinstance(x, float) and math.isnan(x)


def build_dataset_variant(
    bars: Any,
    *,
    horizon: int = DEFAULT_HORIZON,
    fee_rate: float = DEFAULT_FEE_RATE,
    buffer: float = DEFAULT_BUFFER,
    splits: dict[str, tuple[str, str]] = DEFAULT_SPLITS,
    default_instrument_id: str = "UNKNOWN",
    task: str = MULTICLASS,
    long_threshold: float = DEFAULT_LONG_THRESHOLD,
    short_threshold: float = DEFAULT_SHORT_THRESHOLD,
) -> tuple[list[dict], dict]:
    """Build (rows, summary) using V2 features + a label variant.

    ``fee_rate``/``buffer`` are accepted (writer passes them) but the variant
    label uses the explicit ``long_threshold``/``short_threshold`` instead of the
    cost-derived band. Horizon-specific purge/drop are automatic via the label's
    ``label_horizon_ts`` (= ``t+H``).
    """
    from research.features import to_columns  # local import; pure-Python

    cols = to_columns(bars)
    n_raw = len(cols.get("close", []))
    order = sorted(range(n_raw), key=lambda i: int(cols["event_time_ns"][i]))
    scols = {k: [v[i] for i in order] for k, v in cols.items()}
    iids = scols.get("instrument_id") or [default_instrument_id] * n_raw

    feats = compute_features_v2(scols)
    labels = build_labels_variant(scols, task=task, horizon=horizon,
                                  long_threshold=long_threshold, short_threshold=short_threshold)
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
        d = by_split.setdefault(r["split"], {k: 0 for k in task_codes(task)})
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
        "label_distribution_total": label_distribution_variant([r["label_class"] for r in rows], task),
        "label_distribution_by_split": by_split,
        "feature_columns": list(FEATURE_COLUMNS_V2),
        "nan_counts": nan_counts,
        "first_ts": min(kept_ts) if kept_ts else None,
        "last_ts": max(kept_ts) if kept_ts else None,
        "first_valid_index": first_valid,
        "horizon": horizon,
        "task_type": task,
        "long_threshold": long_threshold,
        "short_threshold": short_threshold if task == MULTICLASS else None,
        "label_mapping": task_codes(task),
    }
    return rows, summary
