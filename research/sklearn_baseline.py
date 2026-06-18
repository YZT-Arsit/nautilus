"""sklearn baseline training/evaluation for the ML V1 dataset (CPU-only).

Reads the month-chunked parquet dataset produced by
``scripts/build_ml_dataset.py`` (``split=train/`` + ``split=validation/`` +
``feature_columns.json``), trains tabular sklearn baselines, evaluates on the
validation split, and writes per-model artifacts (``model.joblib`` /
``metadata.json`` / ``metrics.json`` / ``feature_columns.json``) plus a top-level
``summary.json``.

Design rules:

* **Features** are exactly the ``f_*`` columns listed in ``feature_columns.json``
  - the single shared source of truth for train/inference parity. Forward-looking
  / bookkeeping columns (``close_t``, ``future_return_15m``, ``label_horizon_ts``,
  ``event_time_ns``, ``split``, ``label_class``, ...) are **never** used as
  features (leakage guard).
* **Target** is ``label_code`` (SHORT=0 / NO_TRADE=1 / LONG=2).
* The **test** split is never touched here.
* Heavy deps (numpy/pandas/sklearn/joblib) are imported lazily inside functions
  so this module imports cheaply (CLI preflight / ``--help`` need no sklearn) and
  ``py_compile`` works without the scientific stack installed. Imports **no**
  ``lightgbm`` (B2) and **no** ``nautilus_trader``.

Run on the server via ``uv run --no-sync python`` (deps live in ``.venv`` but are
not in ``uv.lock``; a plain ``uv run``/``uv sync`` would uninstall them).
"""
from __future__ import annotations

import glob
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from research.label_builder import CODE_TO_LABEL, LABEL_CODES

NUM_CLASSES = 3
CLASS_LABELS = [0, 1, 2]                       # SHORT, NO_TRADE, LONG (sorted)
DEFAULT_MODELS = ("logistic_regression", "hist_gradient_boosting")
DEFAULT_SEED = 42

# Columns that must NEVER be fed to a model as a feature (forward-looking,
# bookkeeping, or the target itself). Pure defensive guard - feature_columns.json
# only lists f_* names, but we assert it.
FORBIDDEN_FEATURE_COLUMNS = frozenset({
    "close_t", "future_return_15m", "label_horizon", "label_horizon_ts",
    "event_time_ns", "split", "label_class", "label_code", "is_valid",
    "instrument_id",
})


# --- dataset loading --------------------------------------------------------

def load_feature_columns(dataset_dir: str | Path) -> list[str]:
    """Read ``feature_columns.json`` (the authoritative ordered feature list)."""
    path = Path(dataset_dir) / "feature_columns.json"
    if not path.exists():
        raise ValueError(f"feature_columns.json not found in dataset: {path}")
    cols = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(cols, list) or not cols:
        raise ValueError("feature_columns.json must be a non-empty list")
    bad = [c for c in cols if c in FORBIDDEN_FEATURE_COLUMNS]
    if bad:
        raise ValueError(f"feature_columns.json contains forbidden feature(s): {bad}")
    return list(cols)


def _split_parquet_files(dataset_dir: str | Path, split: str) -> list[str]:
    return sorted(glob.glob(str(Path(dataset_dir) / f"split={split}" / "*.parquet")))


def load_split(
    dataset_dir: str | Path,
    split: str,
    feature_columns: list[str],
    *,
    max_rows: int | None = None,
):
    """Load one split's parquet parts into ``(X, y, n_rows)``.

    ``X`` is a float32 ``ndarray`` of the ``feature_columns`` (in order); ``y`` is
    an int ``ndarray`` of ``label_code``. Raises ``ValueError`` on a missing split,
    a missing feature column, or a ``label_code`` outside ``{0, 1, 2}``.
    """
    import numpy as np      # noqa: PLC0415
    import pandas as pd     # noqa: PLC0415

    files = _split_parquet_files(dataset_dir, split)
    if not files:
        raise ValueError(f"no parquet parts for split={split} under {dataset_dir}")

    frames = [pd.read_parquet(f) for f in files]
    df = pd.concat(frames, ignore_index=True) if len(frames) > 1 else frames[0]

    missing = [c for c in feature_columns if c not in df.columns]
    if missing:
        raise ValueError(f"split={split} missing feature column(s): {missing}")
    if "label_code" not in df.columns:
        raise ValueError(f"split={split} missing target column 'label_code'")

    if max_rows is not None and max_rows >= 0:
        df = df.head(max_rows)

    X = df[feature_columns].to_numpy(dtype=np.float32)
    y = df["label_code"].to_numpy()
    uniq = set(int(v) for v in np.unique(y))
    bad = uniq - set(CLASS_LABELS)
    if bad:
        raise ValueError(f"split={split} has label_code values outside {{0,1,2}}: {sorted(bad)}")
    y = y.astype(np.int64)
    return X, y, int(X.shape[0])


# --- models -----------------------------------------------------------------

def build_model(name: str, *, seed: int = DEFAULT_SEED, n_jobs: int = 1):
    """Construct an (untrained) sklearn estimator for ``name``.

    * ``logistic_regression``: ``StandardScaler`` -> ``LogisticRegression`` with
      ``class_weight="balanced"`` (multinomial via the default lbfgs solver; the
      removed ``multi_class`` arg is intentionally not passed for sklearn 1.9).
    * ``hist_gradient_boosting``: ``HistGradientBoostingClassifier`` with
      ``class_weight="balanced"`` (supported since sklearn 1.5).
    """
    from sklearn.ensemble import HistGradientBoostingClassifier  # noqa: PLC0415
    from sklearn.linear_model import LogisticRegression          # noqa: PLC0415
    from sklearn.pipeline import Pipeline                         # noqa: PLC0415
    from sklearn.preprocessing import StandardScaler              # noqa: PLC0415

    if name == "logistic_regression":
        # n_jobs intentionally not passed: it has no effect on LogisticRegression
        # since sklearn 1.8 and is removed in 1.10 (the lbfgs solver is single-job).
        # The CLI --n-jobs flag is still accepted/recorded for forward use.
        return Pipeline([
            ("scaler", StandardScaler()),
            ("clf", LogisticRegression(max_iter=500, class_weight="balanced",
                                       random_state=seed)),
        ])
    if name == "hist_gradient_boosting":
        return HistGradientBoostingClassifier(
            max_iter=100, learning_rate=0.1, class_weight="balanced",
            random_state=seed,
        )
    raise ValueError(f"unknown model '{name}'; allowed: {sorted(DEFAULT_MODELS)}")


def model_class_weight(name: str) -> str:
    """The class-weight strategy used for ``name`` (recorded in metadata)."""
    return "balanced"


# --- evaluation -------------------------------------------------------------

def evaluate(model, X_val, y_val) -> dict[str, Any]:
    """Compute validation metrics (does not refit). Returns a JSON-able dict."""
    import numpy as np  # noqa: PLC0415
    from sklearn.metrics import (  # noqa: PLC0415
        accuracy_score,
        balanced_accuracy_score,
        confusion_matrix,
        f1_score,
        precision_recall_fscore_support,
    )

    y_pred = model.predict(X_val)
    proba = model.predict_proba(X_val)

    prec, rec, f1, support = precision_recall_fscore_support(
        y_val, y_pred, labels=CLASS_LABELS, zero_division=0)
    cm = confusion_matrix(y_val, y_pred, labels=CLASS_LABELS)

    def _dist(arr) -> dict[str, int]:
        vals, counts = np.unique(arr, return_counts=True)
        out = {CODE_TO_LABEL[c]: 0 for c in CLASS_LABELS}
        for v, c in zip(vals, counts):
            out[CODE_TO_LABEL[int(v)]] = int(c)
        return out

    max_prob = proba.max(axis=1)
    qs = np.quantile(max_prob, [0.1, 0.25, 0.5, 0.75, 0.9])
    per_class_avg_proba = proba.mean(axis=0)

    metrics: dict[str, Any] = {
        "accuracy": float(accuracy_score(y_val, y_pred)),
        "balanced_accuracy": float(balanced_accuracy_score(y_val, y_pred)),
        "macro_f1": float(f1_score(y_val, y_pred, labels=CLASS_LABELS,
                                   average="macro", zero_division=0)),
        "weighted_f1": float(f1_score(y_val, y_pred, labels=CLASS_LABELS,
                                      average="weighted", zero_division=0)),
        "per_class": {
            CODE_TO_LABEL[c]: {
                "precision": float(prec[i]), "recall": float(rec[i]),
                "f1": float(f1[i]), "support": int(support[i]),
            } for i, c in enumerate(CLASS_LABELS)
        },
        "confusion_matrix": {
            "labels": [CODE_TO_LABEL[c] for c in CLASS_LABELS],
            "matrix": cm.astype(int).tolist(),
        },
        "prediction_distribution": _dist(y_pred),
        "true_label_distribution": _dist(y_val),
        "probability_summary": {
            "max_prob_mean": float(max_prob.mean()),
            "max_prob_quantiles": {q: float(v) for q, v in
                                   zip(["p10", "p25", "p50", "p75", "p90"], qs)},
            "per_class_avg_proba": {CODE_TO_LABEL[c]: float(per_class_avg_proba[i])
                                    for i, c in enumerate(CLASS_LABELS)},
        },
        "validation_rows": int(len(y_val)),
    }
    return metrics


def _coefficients(name: str, model, feature_columns: list[str]) -> dict | None:
    """LogisticRegression coefficient table (or ``None`` if not applicable)."""
    clf = model.named_steps["clf"] if hasattr(model, "named_steps") else model
    if not hasattr(clf, "coef_"):
        return None
    coef = clf.coef_
    return {
        "classes": [CODE_TO_LABEL[int(c)] for c in clf.classes_],
        "features": list(feature_columns),
        "coef": coef.astype(float).tolist(),
        "intercept": clf.intercept_.astype(float).tolist(),
    }


def train_one(name, X_tr, y_tr, X_val, y_val, feature_columns, *,
              seed: int = DEFAULT_SEED, n_jobs: int = 1) -> tuple[Any, dict]:
    """Fit ``name`` on train, evaluate on validation. Returns ``(model, metrics)``."""
    model = build_model(name, seed=seed, n_jobs=n_jobs)
    model.fit(X_tr, y_tr)
    metrics = evaluate(model, X_val, y_val)
    metrics["feature_count"] = len(feature_columns)
    metrics["train_rows"] = int(len(y_tr))
    coef = _coefficients(name, model, feature_columns)
    if coef is not None:
        metrics["coefficients"] = coef
    return model, metrics


# --- artifacts --------------------------------------------------------------

def feature_columns_hash(feature_columns: list[str]) -> str:
    return hashlib.sha256(json.dumps(list(feature_columns)).encode("utf-8")).hexdigest()


def build_metadata(name, *, dataset_path, feature_columns, train_split, validation_split,
                   seed, command_args, created_at=None) -> dict[str, Any]:
    import platform  # noqa: PLC0415

    import numpy as np      # noqa: PLC0415
    import pandas as pd     # noqa: PLC0415
    import sklearn          # noqa: PLC0415

    return {
        "model_type": name,
        "sklearn_version": sklearn.__version__,
        "numpy_version": np.__version__,
        "pandas_version": pd.__version__,
        "python_version": platform.python_version(),
        "train_split": train_split,
        "validation_split": validation_split,
        "dataset_path": str(dataset_path),
        "feature_count": len(feature_columns),
        "feature_columns_hash": feature_columns_hash(feature_columns),
        "label_mapping": dict(LABEL_CODES),
        "class_weight": model_class_weight(name),
        "random_state": seed,
        "created_at": created_at or datetime.now(timezone.utc).isoformat(),
        "command_args": command_args,
    }


def write_model_artifact(out_dir, name, model, metadata, metrics, feature_columns,
                         *, save_model: bool = True) -> Path:
    """Write one model's artifact subdir. Returns the subdir path."""
    sub = Path(out_dir) / name
    sub.mkdir(parents=True, exist_ok=True)
    if save_model:
        import joblib  # noqa: PLC0415
        joblib.dump(model, sub / "model.joblib")
    (sub / "metadata.json").write_text(json.dumps(metadata, indent=2, default=str),
                                       encoding="utf-8")
    (sub / "metrics.json").write_text(json.dumps(metrics, indent=2, default=str),
                                      encoding="utf-8")
    (sub / "feature_columns.json").write_text(json.dumps(list(feature_columns), indent=2),
                                              encoding="utf-8")
    return sub


def write_summary(out_dir, results: dict[str, dict], *, dataset_path,
                  feature_columns, train_rows, validation_rows) -> Path:
    """Write the top-level ``summary.json`` rolling up each model's key metrics."""
    summary = {
        "dataset_path": str(dataset_path),
        "feature_count": len(feature_columns),
        "train_rows": train_rows,
        "validation_rows": validation_rows,
        "models": {
            name: {
                "accuracy": m["accuracy"],
                "balanced_accuracy": m["balanced_accuracy"],
                "macro_f1": m["macro_f1"],
                "weighted_f1": m["weighted_f1"],
                "prediction_distribution": m["prediction_distribution"],
            } for name, m in results.items()
        },
    }
    path = Path(out_dir) / "summary.json"
    path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    return path
