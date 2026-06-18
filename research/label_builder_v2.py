"""Label variants for B3-label research (pure-Python, stdlib only; additive).

Extends the V1 cost-aware 3-class label with three configurable variants, while
leaving :mod:`research.label_builder` (horizon=15, symmetric 0.0015, 3-class) and
all existing datasets/tests untouched:

* **multiclass** (symmetric or asymmetric): SHORT=0 / NO_TRADE=1 / LONG=2, with
  separate ``long_threshold`` / ``short_threshold`` and configurable ``horizon``.
* **long_only_binary**: REST=0 / LONG=1 (negative moves are REST, *not* SHORT) -
  the honest framing given the SHORT side stays unprofitable.

The forward return uses **exactly** ``close[t+H] / close[t] - 1`` (the only future
read). To keep the downstream threshold-analysis / training stack unchanged for
the multiclass variants, the realized forward return is stored under the canonical
column name ``future_return_15m`` regardless of ``H`` (it is "the label's forward
return"); the true horizon is recorded in ``label_horizon`` + metadata. Rows whose
horizon bar ``t+H`` does not exist are ``is_valid_label=False`` and dropped by the
builder - no test tail is ever read. No features, no forward-fill.
"""
from __future__ import annotations

import math
from typing import Any

from research.features import to_columns
from research.label_builder import DEFAULT_BUFFER, DEFAULT_FEE_RATE, LABEL_CODES, label_threshold

_NAN = float("nan")

MULTICLASS = "multiclass"
LONG_ONLY_BINARY = "long_only_binary"
VALID_TASKS = (MULTICLASS, LONG_ONLY_BINARY)

MULTICLASS_CODES: dict[str, int] = dict(LABEL_CODES)          # SHORT=0/NO_TRADE=1/LONG=2
BINARY_CODES: dict[str, int] = {"REST": 0, "LONG": 1}

DEFAULT_HORIZON = 15
DEFAULT_LONG_THRESHOLD = 0.0015
DEFAULT_SHORT_THRESHOLD = 0.0015


def task_codes(task: str) -> dict[str, int]:
    if task == MULTICLASS:
        return dict(MULTICLASS_CODES)
    if task == LONG_ONLY_BINARY:
        return dict(BINARY_CODES)
    raise ValueError(f"unknown task {task!r}; allowed: {VALID_TASKS}")


def classify_multiclass(fr: float, long_threshold: float, short_threshold: float) -> str:
    if math.isnan(fr):
        return "NO_TRADE"
    if fr > long_threshold:
        return "LONG"
    if fr < -short_threshold:
        return "SHORT"
    return "NO_TRADE"


def classify_long_binary(fr: float, long_threshold: float) -> str:
    if math.isnan(fr):
        return "REST"
    return "LONG" if fr > long_threshold else "REST"


def build_labels_variant(
    table: Any,
    *,
    task: str = MULTICLASS,
    horizon: int = DEFAULT_HORIZON,
    long_threshold: float = DEFAULT_LONG_THRESHOLD,
    short_threshold: float = DEFAULT_SHORT_THRESHOLD,
) -> dict[str, list]:
    """Build label-variant columns for a whole bar series.

    Returns a dict-of-lists (length N): ``future_return_15m`` (the realized
    ``close[t+H]/close[t]-1``, canonical slot), ``label_class``, ``label_code``,
    ``label_horizon`` (constant H), ``label_horizon_ts``, ``is_valid_label``.
    """
    if task not in VALID_TASKS:
        raise ValueError(f"unknown task {task!r}; allowed: {VALID_TASKS}")
    if horizon <= 0:
        raise ValueError("horizon must be > 0")
    if long_threshold <= 0 or short_threshold <= 0:
        raise ValueError("thresholds must be > 0")

    cols = to_columns(table)
    close, ts = cols["close"], cols["event_time_ns"]
    n = len(close)
    codes = task_codes(task)

    fr_col, cls_col, code_col, htz_col, valid_col, horizon_col = [], [], [], [], [], []
    for t in range(n):
        ht = t + horizon
        horizon_col.append(horizon)
        if ht < n and close[t] != 0:
            fr = close[ht] / close[t] - 1.0
            cls = (classify_multiclass(fr, long_threshold, short_threshold) if task == MULTICLASS
                   else classify_long_binary(fr, long_threshold))
            fr_col.append(fr)
            cls_col.append(cls)
            code_col.append(codes[cls])
            htz_col.append(int(ts[ht]))
            valid_col.append(True)
        else:
            fr_col.append(_NAN)
            cls_col.append("NO_TRADE" if task == MULTICLASS else "REST")
            code_col.append(None)
            htz_col.append(None)
            valid_col.append(False)

    return {
        "future_return_15m": fr_col,
        "label_class": cls_col,
        "label_code": code_col,
        "label_horizon": horizon_col,
        "label_horizon_ts": htz_col,
        "is_valid_label": valid_col,
    }


def label_distribution_variant(label_classes: list[str], task: str,
                               *, valid_mask: list[bool] | None = None) -> dict[str, int]:
    """Count labels per class for the task (only valid rows if mask given)."""
    counts = {k: 0 for k in task_codes(task)}
    for i, c in enumerate(label_classes):
        if valid_mask is not None and not valid_mask[i]:
            continue
        if c in counts:
            counts[c] += 1
    return counts


def variant_label_threshold_meta(task: str, horizon: int, long_threshold: float,
                                 short_threshold: float, fee_rate: float = DEFAULT_FEE_RATE,
                                 buffer: float = DEFAULT_BUFFER) -> dict[str, Any]:
    """Metadata block describing the label variant (cost framing included)."""
    return {
        "task_type": task,
        "horizon": horizon,
        "long_threshold": long_threshold,
        "short_threshold": short_threshold if task == MULTICLASS else None,
        "label_mapping": task_codes(task),
        "round_trip_fee": 2.0 * float(fee_rate),
        "default_cost_threshold": label_threshold(fee_rate, buffer),
    }
