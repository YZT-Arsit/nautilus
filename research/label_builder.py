"""Forward-return + cost-aware 3-class labels (pure-Python, stdlib only).

Labels are the **only** place future bars are read. The label at bar ``t`` looks
exactly ``horizon`` bars ahead:

    future_return_H = close[t + H] / close[t] - 1

and is classified against a **cost-aware** band so the model only calls a move
"tradeable" when it beats round-trip cost plus a buffer:

    threshold = 2 * fee_rate + buffer          # default 2*0.0005 + 0.0005 = 0.0015
    LONG     if future_return_H >  +threshold
    SHORT    if future_return_H <  -threshold
    NO_TRADE otherwise

Rows whose horizon bar does not exist (the last ``H`` bars of the series) get
``is_valid_label = False`` and ``NaN`` return; the dataset builder drops them.
This module never computes features and never forward-fills.
"""
from __future__ import annotations

import math
from typing import Any

from research.features import to_columns

_NAN = float("nan")

DEFAULT_HORIZON = 15
DEFAULT_FEE_RATE = 0.0005
DEFAULT_BUFFER = 0.0005

# Stable, documented class <-> code mapping (do not reorder).
LABEL_CODES: dict[str, int] = {"SHORT": 0, "NO_TRADE": 1, "LONG": 2}
CODE_TO_LABEL: dict[int, str] = {v: k for k, v in LABEL_CODES.items()}


def label_threshold(fee_rate: float = DEFAULT_FEE_RATE, buffer: float = DEFAULT_BUFFER) -> float:
    """Round-trip cost (2x fee) plus a buffer -> the LONG/SHORT decision band."""
    return 2.0 * float(fee_rate) + float(buffer)


def classify_return(fr: float, threshold: float) -> str:
    """Map a forward return to LONG / SHORT / NO_TRADE against ``threshold``."""
    if math.isnan(fr):
        return "NO_TRADE"
    if fr > threshold:
        return "LONG"
    if fr < -threshold:
        return "SHORT"
    return "NO_TRADE"


def build_labels(
    table: Any,
    *,
    horizon: int = DEFAULT_HORIZON,
    fee_rate: float = DEFAULT_FEE_RATE,
    buffer: float = DEFAULT_BUFFER,
) -> dict[str, list]:
    """Build forward-return + cost-aware 3-class labels for a whole bar series.

    Returns a dict-of-lists (length N) with: ``future_return_15m``,
    ``label_class``, ``label_code``, ``label_horizon`` (constant H),
    ``label_horizon_ts`` (ts of bar t+H or ``None``), and ``is_valid_label``.
    The horizon is **exactly** ``t + horizon`` (no off-by-one).
    """
    cols = to_columns(table)
    close = cols["close"]
    ts = cols["event_time_ns"]
    n = len(close)
    thr = label_threshold(fee_rate, buffer)

    fr_col: list[float] = []
    cls_col: list[str] = []
    code_col: list[Any] = []
    htz_col: list[Any] = []
    valid_col: list[bool] = []
    horizon_col: list[int] = []

    for t in range(n):
        ht = t + horizon
        horizon_col.append(horizon)
        if ht < n and close[t] != 0:
            fr = close[ht] / close[t] - 1.0
            cls = classify_return(fr, thr)
            fr_col.append(fr)
            cls_col.append(cls)
            code_col.append(LABEL_CODES[cls])
            htz_col.append(int(ts[ht]))
            valid_col.append(True)
        else:
            fr_col.append(_NAN)
            cls_col.append("NO_TRADE")   # placeholder; is_valid_label=False marks it droppable
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


def label_distribution(label_classes: list[str], *, valid_mask: list[bool] | None = None) -> dict[str, int]:
    """Count LONG/SHORT/NO_TRADE over labels (optionally restricted to valid rows)."""
    counts = {"LONG": 0, "SHORT": 0, "NO_TRADE": 0}
    for i, c in enumerate(label_classes):
        if valid_mask is not None and not valid_mask[i]:
            continue
        if c in counts:
            counts[c] += 1
    return counts
