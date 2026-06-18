"""Threshold / cost-aware evaluation of trained classifiers (B1.5).

Given a model's ``predict_proba`` output on the validation split plus the realized
``future_return_15m``, this answers: *is there a high-confidence directional
subset worth trading?* For a grid of probability thresholds (and top-k%
confidence buckets) it reports, per model:

* **LONG** signals (pred=LONG & P(LONG) >= t): precision, coverage, realized
  forward return, cost-label hit-rate.
* **SHORT** signals (pred=SHORT & P(SHORT) >= t): same, on ``-future_return_15m``.
* **combined directional**: directional precision, wrong-direction rate,
  avg signed return, cost-label hit-rate, signals/day.

Cost framing: the label threshold is ``2*fee + buffer = 0.0015`` and the
round-trip fee is ``2*fee = 0.0010``. A directional subset is only interesting if
``avg_signed_return`` clears ~0.0010 (fees) and ideally 0.0015 (label). LONG/SHORT
``precision`` is already the cost-label hit-rate because the label itself is
defined by the 0.0015 threshold.

The numeric core (:func:`analyze`) is pure-numpy and model-free, so it is fully
unit-testable with hand-built probability arrays. Heavy deps (numpy/pandas/joblib)
are imported lazily. Imports **no** lightgbm and **no** nautilus_trader. This reads
only the validation split; the test split is never touched.
"""
from __future__ import annotations

import glob
import json
from pathlib import Path
from typing import Any

from research.label_builder import DEFAULT_FEE_RATE, LABEL_CODES, label_threshold

SHORT, NO_TRADE, LONG = LABEL_CODES["SHORT"], LABEL_CODES["NO_TRADE"], LABEL_CODES["LONG"]

DEFAULT_THRESHOLDS = (0.35, 0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70)
DEFAULT_TOP_PCTS = (0.20, 0.10, 0.05, 0.01)
DEFAULT_COST = 2.0 * DEFAULT_FEE_RATE          # round-trip fee = 0.0010
_NS_PER_DAY = 86_400_000_000_000


def _n_days(event_time_ns) -> int | None:
    if event_time_ns is None:
        return None
    import numpy as np  # noqa: PLC0415
    days = np.asarray(event_time_ns, dtype="int64") // _NS_PER_DAY
    n = int(len(np.unique(days)))
    return n or None


def _long_metrics(mask, y, fr, n, lt, cost) -> dict[str, Any]:
    import numpy as np  # noqa: PLC0415
    cnt = int(mask.sum())
    base = {"count": cnt, "coverage": cnt / n if n else 0.0}
    if cnt == 0:
        base.update({k: None for k in ("precision", "false_short_rate", "false_no_trade_rate",
                     "avg_future_return_15m", "median_future_return_15m", "hit_rate_positive_return",
                     "cost_label_hit", "avg_return_minus_cost", "avg_return_minus_threshold")})
        return base
    sy, sfr = y[mask], fr[mask]
    base.update({
        "precision": float((sy == LONG).mean()),
        "false_short_rate": float((sy == SHORT).mean()),
        "false_no_trade_rate": float((sy == NO_TRADE).mean()),
        "avg_future_return_15m": float(sfr.mean()),
        "median_future_return_15m": float(np.median(sfr)),
        "hit_rate_positive_return": float((sfr > 0).mean()),
        "cost_label_hit": float((sfr > lt).mean()),
        "avg_return_minus_cost": float((sfr - cost).mean()),
        "avg_return_minus_threshold": float((sfr - lt).mean()),
    })
    return base


def _short_metrics(mask, y, fr, n, lt, cost) -> dict[str, Any]:
    import numpy as np  # noqa: PLC0415
    cnt = int(mask.sum())
    base = {"count": cnt, "coverage": cnt / n if n else 0.0}
    if cnt == 0:
        base.update({k: None for k in ("precision", "false_long_rate", "false_no_trade_rate",
                     "avg_short_return_15m", "median_short_return_15m", "hit_rate_positive_short_return",
                     "cost_label_hit", "avg_return_minus_cost", "avg_return_minus_threshold")})
        return base
    sy, ssr = y[mask], -fr[mask]
    base.update({
        "precision": float((sy == SHORT).mean()),
        "false_long_rate": float((sy == LONG).mean()),
        "false_no_trade_rate": float((sy == NO_TRADE).mean()),
        "avg_short_return_15m": float(ssr.mean()),
        "median_short_return_15m": float(np.median(ssr)),
        "hit_rate_positive_short_return": float((ssr > 0).mean()),
        "cost_label_hit": float((ssr > lt).mean()),
        "avg_return_minus_cost": float((ssr - cost).mean()),
        "avg_return_minus_threshold": float((ssr - lt).mean()),
    })
    return base


def _combined_metrics(lmask, smask, y, fr, n, n_days, lt, cost) -> dict[str, Any]:
    import numpy as np  # noqa: PLC0415
    sig = lmask | smask
    cnt = int(sig.sum())
    base = {"signal_count": cnt, "signal_coverage": cnt / n if n else 0.0,
            "signals_per_day": (cnt / n_days) if n_days else None}
    if cnt == 0:
        base.update({k: None for k in ("directional_precision", "wrong_direction_rate",
                     "no_trade_rate", "avg_signed_return", "median_signed_return",
                     "positive_signed_return_rate", "cost_label_hit_rate",
                     "avg_signed_return_minus_cost")})
        return base
    signed = np.where(lmask, fr, np.where(smask, -fr, np.nan))[sig]
    correct = ((lmask & (y == LONG)) | (smask & (y == SHORT)))[sig]
    wrong = ((lmask & (y == SHORT)) | (smask & (y == LONG)))[sig]
    no_trade = (y == NO_TRADE)[sig]
    base.update({
        "directional_precision": float(correct.mean()),
        "wrong_direction_rate": float(wrong.mean()),
        "no_trade_rate": float(no_trade.mean()),
        "avg_signed_return": float(signed.mean()),
        "median_signed_return": float(np.median(signed)),
        "positive_signed_return_rate": float((signed > 0).mean()),
        "cost_label_hit_rate": float((signed > lt).mean()),
        "avg_signed_return_minus_cost": float((signed - cost).mean()),
    })
    return base


def analyze(proba, y_true, future_return, *, n_days=None,
            thresholds=DEFAULT_THRESHOLDS, top_pcts=DEFAULT_TOP_PCTS,
            lt: float | None = None, cost: float = DEFAULT_COST) -> dict[str, Any]:
    """Threshold + top-k% analysis. ``proba`` columns must be ordered [SHORT, NO_TRADE, LONG]."""
    import numpy as np  # noqa: PLC0415

    lt = label_threshold() if lt is None else lt
    proba = np.asarray(proba, dtype=float)
    y = np.asarray(y_true).astype(int)
    fr = np.asarray(future_return, dtype=float)
    n = int(len(y))
    p_short, p_long = proba[:, 0], proba[:, 2]
    pred = proba.argmax(axis=1)
    conf_dir = np.where(pred == LONG, p_long, np.where(pred == SHORT, p_short, -1.0))

    out: dict[str, Any] = {"validation_rows": n, "n_days": n_days,
                           "label_threshold": lt, "cost": cost, "thresholds": {}, "top_pct": {}}
    for t in thresholds:
        lmask = (pred == LONG) & (p_long >= t)
        smask = (pred == SHORT) & (p_short >= t)
        out["thresholds"][f"{t:.2f}"] = {
            "long": _long_metrics(lmask, y, fr, n, lt, cost),
            "short": _short_metrics(smask, y, fr, n, lt, cost),
            "combined": _combined_metrics(lmask, smask, y, fr, n, n_days, lt, cost),
        }

    order = np.argsort(-conf_dir)        # most-confident directional first (NO_TRADE -> -1, last)
    for pct in top_pcts:
        k = int(n * pct)
        sel = order[:k]
        sel = sel[conf_dir[sel] >= 0]    # keep only directional predictions
        lmask = np.zeros(n, dtype=bool)
        smask = np.zeros(n, dtype=bool)
        lmask[sel[pred[sel] == LONG]] = True
        smask[sel[pred[sel] == SHORT]] = True
        floor = float(conf_dir[sel].min()) if len(sel) else None
        out["top_pct"][f"{pct:.2f}"] = {
            "k": int(k), "confidence_floor": floor,
            "combined": _combined_metrics(lmask, smask, y, fr, n, n_days, lt, cost),
        }

    best = None
    for t, info in out["thresholds"].items():
        c = info["combined"]
        if c["signal_count"] >= 100 and c["avg_signed_return_minus_cost"] is not None:
            if best is None or c["avg_signed_return_minus_cost"] > best[1]:
                best = (t, c["avg_signed_return_minus_cost"])
    out["best_threshold_by_signed_return_minus_cost"] = best[0] if best else None
    return out


# --- model / dataset glue ---------------------------------------------------

def _canonical_proba(model, X):
    """Run predict_proba and reorder columns to [SHORT, NO_TRADE, LONG]."""
    import numpy as np  # noqa: PLC0415
    proba = np.asarray(model.predict_proba(X), dtype=float)
    classes = [int(c) for c in model.classes_]
    for c in (SHORT, NO_TRADE, LONG):
        if c not in classes:
            raise ValueError(f"model is missing class {c}; classes_={classes}")
    return proba[:, [classes.index(SHORT), classes.index(NO_TRADE), classes.index(LONG)]]


def analyze_model(model, X, y_true, future_return, *, event_time_ns=None,
                  thresholds=DEFAULT_THRESHOLDS, top_pcts=DEFAULT_TOP_PCTS) -> dict[str, Any]:
    canonical = _canonical_proba(model, X)
    return analyze(canonical, y_true, future_return, n_days=_n_days(event_time_ns),
                   thresholds=thresholds, top_pcts=top_pcts)


def load_validation(dataset_dir, feature_columns, *, max_rows: int | None = None):
    """Load ``split=validation`` parquet → ``(X, y, future_return_15m, event_time_ns)``."""
    import numpy as np   # noqa: PLC0415
    import pandas as pd  # noqa: PLC0415

    files = sorted(glob.glob(str(Path(dataset_dir) / "split=validation" / "*.parquet")))
    if not files:
        raise ValueError(f"no parquet parts for split=validation under {dataset_dir}")
    df = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)

    missing = [c for c in feature_columns if c not in df.columns]
    if missing:
        raise ValueError(f"validation missing feature column(s): {missing}")
    for required in ("label_code", "future_return_15m"):
        if required not in df.columns:
            raise ValueError(f"validation missing required column '{required}'")

    if max_rows is not None and max_rows >= 0:
        df = df.head(max_rows)

    X = df[feature_columns].to_numpy(dtype=np.float32)
    y = df["label_code"].to_numpy().astype(np.int64)
    fr = df["future_return_15m"].to_numpy(dtype=np.float64)
    ev = df["event_time_ns"].to_numpy().astype(np.int64) if "event_time_ns" in df.columns else None
    return X, y, fr, ev


def write_analysis(out_path, analysis: dict) -> Path:
    p = Path(out_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(analysis, indent=2, default=str), encoding="utf-8")
    return p
