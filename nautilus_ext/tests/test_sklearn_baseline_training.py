"""Tests for research/sklearn_baseline.py + scripts/train_sklearn_baseline.py.

Offline + synthetic: builds tiny parquet datasets under ``tmp_path`` (never reads
the real ``outputs/research_datasets`` dataset, never writes real
``outputs/models``). Requires pandas/pyarrow/sklearn/joblib, so these run on the
server ``.venv`` via ``uv run --no-sync python -m pytest``.
"""
from __future__ import annotations

import inspect
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

pytest.importorskip("pandas")
pytest.importorskip("sklearn")

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from research.label_builder import CODE_TO_LABEL  # noqa: E402
from research.sklearn_baseline import (  # noqa: E402
    FORBIDDEN_FEATURE_COLUMNS,
    build_metadata,
    evaluate,
    load_feature_columns,
    load_split,
    train_one,
    write_model_artifact,
    write_summary,
)
from scripts.train_sklearn_baseline import preflight, run  # noqa: E402

FEATURES = ["f_a", "f_b", "f_c", "f_d"]


def _make_split_frame(n, seed, *, balanced=True):
    rng = np.random.default_rng(seed)
    feats = {f: rng.standard_normal(n).astype("float32") for f in FEATURES}
    if balanced:
        y = rng.integers(0, 3, size=n)
    else:
        # heavy NO_TRADE (1) imbalance, but force >=1 of each class present
        y = np.ones(n, dtype=int)
        y[: max(1, n // 20)] = 0
        y[max(1, n // 20): max(2, n // 10)] = 2
    y[0], y[1], y[2] = 0, 1, 2          # guarantee all three classes
    data = dict(feats)
    data["label_code"] = y.astype("int8")
    # forward-looking / bookkeeping columns that must be excluded from features
    data["close_t"] = rng.standard_normal(n).astype("float64")
    data["future_return_15m"] = rng.standard_normal(n).astype("float64")
    data["label_horizon_ts"] = np.arange(n, dtype="int64")
    data["event_time_ns"] = np.arange(n, dtype="int64")
    data["label_class"] = [CODE_TO_LABEL[int(c)] for c in y]
    return pd.DataFrame(data)


def _make_dataset(tmp_path, *, n_train=240, n_val=90, balanced=True,
                  features=FEATURES, train_parts=2):
    ds = tmp_path / "outputs" / "research_datasets" / "ds"
    (ds / "split=train").mkdir(parents=True)
    (ds / "split=validation").mkdir(parents=True)
    # split train across multiple parts to exercise multi-part concat
    per = n_train // train_parts
    for i in range(train_parts):
        n = per if i < train_parts - 1 else n_train - per * (train_parts - 1)
        _make_split_frame(n, 100 + i, balanced=balanced).to_parquet(
            ds / "split=train" / f"part-2024-0{i + 6}.parquet", index=False)
    _make_split_frame(n_val, 999, balanced=balanced).to_parquet(
        ds / "split=validation" / "part-2026-01.parquet", index=False)
    (ds / "feature_columns.json").write_text(json.dumps(list(features)), encoding="utf-8")
    return ds


def _args(dataset, out, **over):
    base = dict(dataset=str(dataset), out=str(out),
                models="logistic_regression,hist_gradient_boosting", seed=42,
                max_train_rows=None, max_validation_rows=None, n_jobs=1,
                no_save_model=False, overwrite=False, dry_run=False)
    base.update(over)
    return SimpleNamespace(**base)


# --- A. dataset loading -----------------------------------------------------

def test_load_feature_columns_and_split(tmp_path):
    ds = _make_dataset(tmp_path)
    fcols = load_feature_columns(ds)
    assert fcols == FEATURES
    X, y, n = load_split(ds, "train", fcols)
    assert X.shape == (240, len(FEATURES)) and n == 240
    assert str(X.dtype) == "float32"
    assert set(int(v) for v in np.unique(y)) <= {0, 1, 2}


def test_features_exclude_forbidden_columns(tmp_path):
    ds = _make_dataset(tmp_path)
    fcols = load_feature_columns(ds)
    # the authoritative feature list must contain none of the forbidden columns
    assert not (set(fcols) & set(FORBIDDEN_FEATURE_COLUMNS))
    X, _, _ = load_split(ds, "train", fcols)
    # X width == number of features only (forward-looking cols not appended)
    assert X.shape[1] == len(FEATURES)


def test_missing_feature_column_raises(tmp_path):
    ds = _make_dataset(tmp_path)
    with pytest.raises(ValueError, match="missing feature column"):
        load_split(ds, "train", FEATURES + ["f_does_not_exist"])


def test_feature_columns_json_rejects_forbidden(tmp_path):
    ds = _make_dataset(tmp_path)
    (ds / "feature_columns.json").write_text(json.dumps(["f_a", "future_return_15m"]),
                                             encoding="utf-8")
    with pytest.raises(ValueError, match="forbidden"):
        load_feature_columns(ds)


def test_missing_validation_split_raises(tmp_path):
    ds = _make_dataset(tmp_path)
    import shutil
    shutil.rmtree(ds / "split=validation")
    fcols = load_feature_columns(ds)
    with pytest.raises(ValueError, match="no parquet parts for split=validation"):
        load_split(ds, "validation", fcols)


def test_bad_label_code_raises(tmp_path):
    ds = _make_dataset(tmp_path)
    df = pd.read_parquet(ds / "split=validation" / "part-2026-01.parquet")
    df.loc[0, "label_code"] = 7          # out of {0,1,2}
    df.to_parquet(ds / "split=validation" / "part-2026-01.parquet", index=False)
    with pytest.raises(ValueError, match="outside"):
        load_split(ds, "validation", FEATURES)


# --- B. model training ------------------------------------------------------

@pytest.mark.parametrize("name", ["logistic_regression", "hist_gradient_boosting"])
def test_model_trains_and_proba_shape(tmp_path, name):
    ds = _make_dataset(tmp_path)
    fcols = load_feature_columns(ds)
    X_tr, y_tr, _ = load_split(ds, "train", fcols)
    X_val, y_val, n_val = load_split(ds, "validation", fcols)
    model, metrics = train_one(name, X_tr, y_tr, X_val, y_val, fcols, seed=42)
    proba = model.predict_proba(X_val)
    assert proba.shape == (n_val, 3)
    assert set(int(c) for c in model.classes_) == {0, 1, 2}
    assert metrics["feature_count"] == len(FEATURES)
    assert metrics["train_rows"] == X_tr.shape[0]


def test_class_imbalance_does_not_crash(tmp_path):
    ds = _make_dataset(tmp_path, balanced=False)
    fcols = load_feature_columns(ds)
    X_tr, y_tr, _ = load_split(ds, "train", fcols)
    X_val, y_val, _ = load_split(ds, "validation", fcols)
    for name in ("logistic_regression", "hist_gradient_boosting"):
        model, metrics = train_one(name, X_tr, y_tr, X_val, y_val, fcols, seed=42)
        assert 0.0 <= metrics["macro_f1"] <= 1.0


def test_fixed_seed_reproducible(tmp_path):
    ds = _make_dataset(tmp_path)
    fcols = load_feature_columns(ds)
    X_tr, y_tr, _ = load_split(ds, "train", fcols)
    X_val, y_val, _ = load_split(ds, "validation", fcols)
    m1, _ = train_one("logistic_regression", X_tr, y_tr, X_val, y_val, fcols, seed=42)
    m2, _ = train_one("logistic_regression", X_tr, y_tr, X_val, y_val, fcols, seed=42)
    assert np.allclose(m1.predict_proba(X_val), m2.predict_proba(X_val))


# --- C. metrics -------------------------------------------------------------

def test_metrics_shapes_and_distributions(tmp_path):
    ds = _make_dataset(tmp_path)
    fcols = load_feature_columns(ds)
    X_tr, y_tr, _ = load_split(ds, "train", fcols)
    X_val, y_val, n_val = load_split(ds, "validation", fcols)
    model, metrics = train_one("logistic_regression", X_tr, y_tr, X_val, y_val, fcols, seed=42)
    cm = metrics["confusion_matrix"]["matrix"]
    assert len(cm) == 3 and all(len(row) == 3 for row in cm)
    assert "macro_f1" in metrics and "weighted_f1" in metrics
    for cls in ("SHORT", "NO_TRADE", "LONG"):
        assert set(metrics["per_class"][cls]) >= {"precision", "recall", "f1", "support"}
    assert sum(metrics["prediction_distribution"].values()) == n_val
    assert sum(metrics["true_label_distribution"].values()) == n_val
    ps = metrics["probability_summary"]
    assert 0.0 <= ps["max_prob_mean"] <= 1.0
    assert set(ps["per_class_avg_proba"]) == {"SHORT", "NO_TRADE", "LONG"}


def test_logreg_has_coefficients(tmp_path):
    ds = _make_dataset(tmp_path)
    fcols = load_feature_columns(ds)
    X_tr, y_tr, _ = load_split(ds, "train", fcols)
    X_val, y_val, _ = load_split(ds, "validation", fcols)
    _, metrics = train_one("logistic_regression", X_tr, y_tr, X_val, y_val, fcols, seed=42)
    assert "coefficients" in metrics
    assert metrics["coefficients"]["features"] == FEATURES
    assert len(metrics["coefficients"]["coef"]) == 3        # one row per class


# --- D. artifacts -----------------------------------------------------------

def test_full_cli_run_writes_artifacts(tmp_path):
    ds = _make_dataset(tmp_path)
    out = tmp_path / "outputs" / "models" / "sk_baseline"
    summary, final = run(_args(ds, out))
    assert final == out and out.exists()
    for name in ("logistic_regression", "hist_gradient_boosting"):
        sub = out / name
        assert (sub / "model.joblib").exists()
        assert (sub / "metadata.json").exists()
        assert (sub / "metrics.json").exists()
        assert (sub / "feature_columns.json").exists()
        meta = json.loads((sub / "metadata.json").read_text())
        assert meta["model_type"] == name and meta["label_mapping"]["NO_TRADE"] == 1
        assert meta["feature_count"] == len(FEATURES)
    assert (out / "summary.json").exists()
    assert set(summary["models"]) == {"logistic_regression", "hist_gradient_boosting"}


def test_saved_model_reloads_and_predicts(tmp_path):
    import joblib
    ds = _make_dataset(tmp_path)
    out = tmp_path / "outputs" / "models" / "sk_baseline"
    run(_args(ds, out, models="logistic_regression"))
    model = joblib.load(out / "logistic_regression" / "model.joblib")
    X_val, _, n_val = load_split(ds, "validation", load_feature_columns(ds))
    assert model.predict_proba(X_val).shape == (n_val, 3)


def test_existing_output_dir_rejected(tmp_path):
    ds = _make_dataset(tmp_path)
    out = tmp_path / "outputs" / "models" / "sk_baseline"
    out.mkdir(parents=True)
    with pytest.raises(ValueError, match="already exists"):
        preflight(_args(ds, out))


def test_no_save_model_skips_joblib(tmp_path):
    ds = _make_dataset(tmp_path)
    out = tmp_path / "outputs" / "models" / "sk_baseline"
    run(_args(ds, out, models="logistic_regression", no_save_model=True))
    assert not (out / "logistic_regression" / "model.joblib").exists()
    assert (out / "logistic_regression" / "metrics.json").exists()


# --- CLI guards -------------------------------------------------------------

def test_output_path_guard_rejects_non_models(tmp_path):
    ds = _make_dataset(tmp_path)
    with pytest.raises(ValueError, match="outputs/models"):
        preflight(_args(ds, tmp_path / "outputs" / "somewhere" / "x"))


def test_output_path_guard_rejects_backtests(tmp_path):
    ds = _make_dataset(tmp_path)
    with pytest.raises(ValueError, match="backtests"):
        preflight(_args(ds, tmp_path / "outputs" / "backtests" / "x"))


def test_output_path_guard_rejects_historical(tmp_path):
    ds = _make_dataset(tmp_path)
    with pytest.raises(ValueError, match="historical_data"):
        preflight(_args(ds, tmp_path / "historical_data" / "x"))


def test_invalid_model_rejected(tmp_path):
    ds = _make_dataset(tmp_path)
    out = tmp_path / "outputs" / "models" / "x"
    with pytest.raises(ValueError, match="invalid models"):
        preflight(_args(ds, out, models="logistic_regression,lightgbm"))


def test_dry_run_writes_nothing(tmp_path):
    ds = _make_dataset(tmp_path)
    out = tmp_path / "outputs" / "models" / "x"
    assert run(_args(ds, out, dry_run=True)) is None
    assert not out.exists()


def test_max_rows_smoke(tmp_path):
    ds = _make_dataset(tmp_path)
    fcols = load_feature_columns(ds)
    X, y, n = load_split(ds, "train", fcols, max_rows=50)
    assert n == 50 and X.shape[0] == 50


def test_metadata_fields_complete(tmp_path):
    ds = _make_dataset(tmp_path)
    fcols = load_feature_columns(ds)
    meta = build_metadata("logistic_regression", dataset_path=str(ds),
                          feature_columns=fcols, train_split="train",
                          validation_split="validation", seed=42,
                          command_args={"x": 1}, created_at="2026-01-01T00:00:00+00:00")
    required = {"model_type", "sklearn_version", "numpy_version", "pandas_version",
                "python_version", "train_split", "validation_split", "dataset_path",
                "feature_count", "feature_columns_hash", "label_mapping",
                "class_weight", "random_state", "created_at", "command_args"}
    assert required <= set(meta)


# --- E. source scan ---------------------------------------------------------

def test_b1_source_clean():
    import research.sklearn_baseline as core
    import scripts.train_sklearn_baseline as cli

    for mod in (core, cli):
        src = inspect.getsource(mod)
        for banned in ("import lightgbm", "from lightgbm", "import torch",
                       "import tensorflow", "import xgboost", "import catboost",
                       "import optuna", "import nautilus_trader", "from nautilus_trader"):
            assert banned not in src, f"{mod.__name__}: {banned}"
        for forbidden in ("run_strategy", "nautilus_backtest", "build_backend",
                          "BacktestEngine"):
            assert forbidden not in src, f"{mod.__name__}: {forbidden}"
        for net in ("import websocket", "import aiohttp", "import requests",
                    "import socket", "import urllib"):
            assert net not in src, f"{mod.__name__}: {net}"
        for order in ("api_key", "secret", "place_order", "new_order", "cancel_order"):
            assert order not in src, f"{mod.__name__}: {order}"
        assert "outputs/backtests" not in src or "refusing to write under outputs/backtests" in src
