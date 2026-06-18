"""LightGBM CPU multiclass baseline (B2) for the ML V1 dataset.

Trains a single ``LGBMClassifier`` (objective=multiclass, num_class=3) on the
``split=train`` parquet, evaluates classification + threshold/cost-aware metrics
on ``split=validation``, and writes artifacts under
``outputs/models/.../lightgbm/``. Reuses the B1 dataset loaders
(:mod:`research.sklearn_baseline`) and the B1.5 threshold analysis
(:mod:`research.threshold_analysis`) so train/inference/evaluation stay identical
across models.

CPU only: no GPU, no Optuna, no grid search, small fixed params. First version
uses a **fixed ``n_estimators``** (no early stopping) for determinism; early
stopping can be added later via ``lightgbm.early_stopping`` callbacks. The test
split is never read; no backtest. Heavy deps (lightgbm/sklearn/numpy/pandas/
joblib) are imported lazily; imports no ``nautilus_trader``.

Run on the server via ``uv run --no-sync python`` (lightgbm lives in ``.venv`` but
not in ``uv.lock``).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from research.label_builder import LABEL_CODES
from research.sklearn_baseline import (
    evaluate,
    feature_columns_hash,
    load_feature_columns,
    load_split,
)
from research.threshold_analysis import analyze_model, load_validation

MODEL_NAME = "lightgbm"
DEFAULT_SEED = 42
DEFAULT_MIN_SIGNALS = 100
B1_LR_BASELINE = "outputs/models/ml_v1_btcusdt_1m_sklearn_baseline/threshold_analysis.json"

# Small fixed CPU params (no sweep). subsample<1 also sets subsample_freq=1 so
# bagging actually engages (avoids LightGBM's "bagging_freq=0" no-op warning).
DEFAULT_PARAMS: dict[str, Any] = {
    "learning_rate": 0.05,
    "n_estimators": 500,
    "num_leaves": 31,
    "max_depth": -1,
    "min_child_samples": 100,
    "subsample": 0.8,
    "colsample_bytree": 0.8,
    "reg_lambda": 1.0,
}


def build_model(params: dict | None = None, *, seed: int = DEFAULT_SEED, n_jobs: int = 1):
    """Construct an (untrained) ``LGBMClassifier`` + the resolved param dict."""
    from lightgbm import LGBMClassifier  # noqa: PLC0415

    p = dict(DEFAULT_PARAMS)
    p.update(params or {})
    kwargs: dict[str, Any] = dict(
        objective="multiclass", num_class=3, boosting_type="gbdt",
        learning_rate=p["learning_rate"], n_estimators=p["n_estimators"],
        num_leaves=p["num_leaves"], max_depth=p["max_depth"],
        min_child_samples=p["min_child_samples"], colsample_bytree=p["colsample_bytree"],
        reg_lambda=p["reg_lambda"], class_weight="balanced", random_state=seed,
        n_jobs=n_jobs, verbosity=-1,
    )
    if p["subsample"] < 1.0:
        kwargs["subsample"] = p["subsample"]
        kwargs["subsample_freq"] = 1
    return LGBMClassifier(**kwargs), p


def feature_importances(model, feature_columns: list[str]) -> dict[str, Any]:
    """Split + gain importances aligned to ``feature_columns``."""
    split = [int(x) for x in model.feature_importances_]
    try:
        gain = [float(x) for x in model.booster_.feature_importance(importance_type="gain")]
    except Exception:                                    # pragma: no cover - defensive
        gain = None
    return {"features": list(feature_columns), "split": split, "gain": gain}


def _to_df(X, feature_columns):
    # Fit + predict on a named DataFrame so LightGBM does not emit the
    # "X does not have valid feature names" warning (numpy fit -> numpy predict).
    import pandas as pd  # noqa: PLC0415
    return pd.DataFrame(X, columns=list(feature_columns))


def train(dataset_dir, *, params: dict | None = None, seed: int = DEFAULT_SEED,
          n_jobs: int = 1, max_train_rows: int | None = None,
          max_validation_rows: int | None = None) -> dict[str, Any]:
    """Fit LightGBM on train, evaluate classification + threshold metrics on validation."""
    feature_columns = load_feature_columns(dataset_dir)
    X_tr, y_tr, n_tr = load_split(dataset_dir, "train", feature_columns, max_rows=max_train_rows)
    X_val, y_val, fr_val, ev_val = load_validation(dataset_dir, feature_columns,
                                                   max_rows=max_validation_rows)

    model, used_params = build_model(params, seed=seed, n_jobs=n_jobs)
    Xtr_df, Xval_df = _to_df(X_tr, feature_columns), _to_df(X_val, feature_columns)
    model.fit(Xtr_df, y_tr)

    metrics = evaluate(model, Xval_df, y_val)
    metrics["feature_count"] = len(feature_columns)
    metrics["train_rows"] = int(n_tr)
    importances = feature_importances(model, feature_columns)
    threshold = analyze_model(model, Xval_df, y_val, fr_val, event_time_ns=ev_val)

    return {"model": model, "feature_columns": feature_columns, "used_params": used_params,
            "metrics": metrics, "feature_importance": importances, "threshold_analysis": threshold,
            "train_rows": int(n_tr), "validation_rows": int(len(y_val))}


def build_metadata(*, dataset_path, feature_columns, used_params, seed, command_args,
                   created_at=None) -> dict[str, Any]:
    import platform  # noqa: PLC0415
    from datetime import datetime, timezone  # noqa: PLC0415

    import lightgbm  # noqa: PLC0415
    import numpy as np  # noqa: PLC0415
    import pandas as pd  # noqa: PLC0415
    import sklearn  # noqa: PLC0415

    return {
        "model_type": MODEL_NAME,
        "lightgbm_version": lightgbm.__version__,
        "sklearn_version": sklearn.__version__,
        "numpy_version": np.__version__,
        "pandas_version": pd.__version__,
        "python_version": platform.python_version(),
        "train_split": "train",
        "validation_split": "validation",
        "dataset_path": str(dataset_path),
        "feature_count": len(feature_columns),
        "feature_columns_hash": feature_columns_hash(feature_columns),
        "label_mapping": dict(LABEL_CODES),
        "class_weight": "balanced",
        "random_state": seed,
        "params": used_params,
        "created_at": created_at or datetime.now(timezone.utc).isoformat(),
        "command_args": command_args,
    }


def best_threshold_by_directional_precision(analysis: dict, *, min_signals: int = DEFAULT_MIN_SIGNALS):
    """Threshold maximizing combined directional_precision with >= min_signals signals."""
    best = None
    for t, info in analysis["thresholds"].items():
        c = info["combined"]
        if c["signal_count"] >= min_signals and c["directional_precision"] is not None:
            if best is None or c["directional_precision"] > best[1]:
                best = (t, c["directional_precision"])
    return best[0] if best else None


def compare_to_baseline(b2_analysis: dict, baseline_analysis: dict | None, *,
                        baseline_model: str = "logistic_regression") -> dict | None:
    """Compare B2's best-signed-return threshold against the B1 baseline's."""
    if not baseline_analysis:
        return None
    base = baseline_analysis.get("models", {}).get(baseline_model)
    if not base:
        return None

    def pick(a):
        t = a.get("best_threshold_by_signed_return_minus_cost")
        return t, (a["thresholds"][t]["combined"] if t else None)

    b2_t, b2c = pick(b2_analysis)
    base_t, basec = pick(base)
    beats = bool(
        b2c and basec and b2c.get("avg_signed_return") is not None
        and b2c.get("directional_precision") is not None
        and b2c["directional_precision"] > 0.50
        and b2c["avg_signed_return"] > 0.0015
        and (b2c["signals_per_day"] or 0) > (basec["signals_per_day"] or 0)
    )
    return {"baseline_model": baseline_model, "b2_best_threshold": b2_t,
            "baseline_best_threshold": base_t, "b2_combined": b2c,
            "baseline_combined": basec, "beats_baseline": beats}


def build_summary(result: dict, *, dataset_path, comparison: dict | None = None,
                  min_signals: int = DEFAULT_MIN_SIGNALS) -> dict[str, Any]:
    m, thr = result["metrics"], result["threshold_analysis"]
    return {
        "model_type": MODEL_NAME,
        "dataset_path": str(dataset_path),
        "feature_count": m["feature_count"],
        "train_rows": result["train_rows"],
        "validation_rows": result["validation_rows"],
        "classification": {k: m[k] for k in ("accuracy", "balanced_accuracy",
                                             "macro_f1", "weighted_f1", "prediction_distribution")},
        "best_threshold_by_avg_signed_return_minus_cost":
            thr["best_threshold_by_signed_return_minus_cost"],
        "best_threshold_by_directional_precision_min_signals": {
            "min_signals": min_signals,
            "threshold": best_threshold_by_directional_precision(thr, min_signals=min_signals),
        },
        "comparison_to_b1_lr": comparison,
        "beats_b1_threshold_baseline": (comparison["beats_baseline"] if comparison else None),
    }


def write_artifacts(out_dir, result: dict, metadata: dict, *, save_model: bool = True,
                    comparison: dict | None = None, min_signals: int = DEFAULT_MIN_SIGNALS,
                    dataset_path=None) -> tuple[Path, dict]:
    sub = Path(out_dir) / MODEL_NAME
    sub.mkdir(parents=True, exist_ok=True)
    if save_model:
        import joblib  # noqa: PLC0415
        joblib.dump(result["model"], sub / "model.joblib")
    (sub / "metadata.json").write_text(json.dumps(metadata, indent=2, default=str), encoding="utf-8")
    (sub / "metrics.json").write_text(json.dumps(result["metrics"], indent=2, default=str), encoding="utf-8")
    (sub / "threshold_analysis.json").write_text(
        json.dumps(result["threshold_analysis"], indent=2, default=str), encoding="utf-8")
    (sub / "feature_importance.json").write_text(
        json.dumps(result["feature_importance"], indent=2, default=str), encoding="utf-8")
    (sub / "feature_columns.json").write_text(
        json.dumps(list(result["feature_columns"]), indent=2), encoding="utf-8")
    summary = build_summary(result, dataset_path=dataset_path or "", comparison=comparison,
                            min_signals=min_signals)
    (Path(out_dir) / "summary.json").write_text(json.dumps(summary, indent=2, default=str),
                                                encoding="utf-8")
    return sub, summary
