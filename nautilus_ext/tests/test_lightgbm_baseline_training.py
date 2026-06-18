"""Tests for research/lightgbm_baseline.py + scripts/train_lightgbm_baseline.py.

Offline + synthetic: tiny LightGBM fits on ``tmp_path`` parquet (never the real
dataset, never real outputs/models). Requires lightgbm/sklearn/pandas/joblib, so
these run on the server ``.venv`` via ``uv run --no-sync python -m pytest``.
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
pytest.importorskip("lightgbm")
pytest.importorskip("sklearn")

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from research.label_builder import CODE_TO_LABEL  # noqa: E402
from research.lightgbm_baseline import (  # noqa: E402
    best_threshold_by_directional_precision,
    build_model,
    compare_to_baseline,
    train,
)
from research.sklearn_baseline import load_feature_columns, load_split  # noqa: E402
from scripts.train_lightgbm_baseline import preflight, run  # noqa: E402

FEATURES = ["f_a", "f_b", "f_c", "f_d"]
# Tiny fast params for synthetic data (override the heavy defaults).
TINY = dict(n_estimators=25, num_leaves=7, min_child_samples=5, max_depth=3,
            subsample=0.9, colsample_bytree=0.9, learning_rate=0.1, reg_lambda=1.0)


def _frame(n, seed):
    rng = np.random.default_rng(seed)
    a = rng.normal(0, 1, n)
    fr = 0.01 * a + rng.normal(0, 0.004, n)              # return tied to f_a -> learnable
    y = np.where(fr > 0.0015, 2, np.where(fr < -0.0015, 0, 1)).astype("int8")
    y[0], y[1], y[2] = 0, 1, 2                            # guarantee all classes
    return pd.DataFrame({
        "f_a": a.astype("float32"), "f_b": rng.normal(0, 1, n).astype("float32"),
        "f_c": rng.normal(0, 1, n).astype("float32"), "f_d": rng.normal(0, 1, n).astype("float32"),
        "label_code": y, "future_return_15m": fr.astype("float64"),
        "label_class": [CODE_TO_LABEL[int(c)] for c in y],
        "close_t": rng.normal(100, 1, n).astype("float64"),
        "event_time_ns": (np.arange(n) * 60_000_000_000 + 1_767_225_600_000_000_000).astype("int64"),
    })


def _make_dataset(tmp_path, *, n_train=600, n_val=300):
    ds = tmp_path / "outputs" / "research_datasets" / "ds"
    (ds / "split=train").mkdir(parents=True)
    (ds / "split=validation").mkdir(parents=True)
    _frame(n_train // 2, 1).to_parquet(ds / "split=train" / "part-2024-06.parquet", index=False)
    _frame(n_train - n_train // 2, 2).to_parquet(ds / "split=train" / "part-2024-07.parquet", index=False)
    _frame(n_val, 9).to_parquet(ds / "split=validation" / "part-2026-01.parquet", index=False)
    (ds / "feature_columns.json").write_text(json.dumps(FEATURES), encoding="utf-8")
    return ds


def _args(dataset, out, **over):
    base = dict(dataset=str(dataset), out=str(out), seed=42, n_estimators=25, learning_rate=0.1,
                num_leaves=7, max_depth=3, min_child_samples=5, subsample=0.9,
                colsample_bytree=0.9, reg_lambda=1.0, n_jobs=1, max_train_rows=None,
                max_validation_rows=None, min_signals=20, compare_to=None,
                no_save_model=False, overwrite=False, dry_run=False)
    base.update(over)
    return SimpleNamespace(**base)


# --- A. import / source -----------------------------------------------------

def test_b2_source_clean():
    import research.lightgbm_baseline as core
    import scripts.train_lightgbm_baseline as cli

    for mod in (core, cli):
        src = inspect.getsource(mod)
        for banned in ("import torch", "import tensorflow", "import xgboost",
                       "import catboost", "import optuna", "import nautilus_trader",
                       "from nautilus_trader"):
            assert banned not in src, f"{mod.__name__}: {banned}"
        for forbidden in ("run_strategy", "nautilus_backtest", "build_backend", "BacktestEngine"):
            assert forbidden not in src, f"{mod.__name__}: {forbidden}"
        for net in ("import websocket", "import aiohttp", "import requests", "import socket"):
            assert net not in src, f"{mod.__name__}: {net}"
        for order in ("api_key", "secret", "place_order", "new_order", "cancel_order"):
            assert order not in src, f"{mod.__name__}: {order}"


def test_lightgbm_only_in_b2():
    import research.lightgbm_baseline as core
    assert "lightgbm" in inspect.getsource(core).lower()       # B2 owns lightgbm


# --- B. dataset loading (reused B1 loaders) ---------------------------------

def test_dataset_loading_features_and_target(tmp_path):
    ds = _make_dataset(tmp_path)
    fcols = load_feature_columns(ds)
    assert fcols == FEATURES
    X, y, n = load_split(ds, "train", fcols)
    assert X.shape[1] == len(FEATURES) and str(X.dtype) == "float32"
    assert set(int(v) for v in np.unique(y)) <= {0, 1, 2}


def test_missing_feature_raises(tmp_path):
    ds = _make_dataset(tmp_path)
    with pytest.raises(ValueError, match="missing feature column"):
        load_split(ds, "train", FEATURES + ["f_missing"])


def test_invalid_label_code_raises(tmp_path):
    ds = _make_dataset(tmp_path)
    df = pd.read_parquet(ds / "split=train" / "part-2024-06.parquet")
    df.loc[0, "label_code"] = 9
    df.to_parquet(ds / "split=train" / "part-2024-06.parquet", index=False)
    with pytest.raises(ValueError, match="outside"):
        load_split(ds, "train", FEATURES)


# --- C. model training ------------------------------------------------------

def test_tiny_lightgbm_trains(tmp_path):
    ds = _make_dataset(tmp_path)
    res = train(ds, params=TINY, seed=42)
    model = res["model"]
    Xval = pd.DataFrame(np.random.default_rng(0).normal(0, 1, (40, 4)), columns=FEATURES)
    proba = model.predict_proba(Xval)
    assert proba.shape == (40, 3)
    assert set(int(c) for c in model.classes_) == {0, 1, 2}
    assert res["validation_rows"] == 300 and res["train_rows"] == 600


def test_build_model_sets_bagging_freq_when_subsampling():
    model, p = build_model({"subsample": 0.8}, seed=42)
    assert model.get_params()["subsample"] == pytest.approx(0.8)
    assert model.get_params()["subsample_freq"] == 1          # bagging actually engages


def test_class_imbalance_no_crash(tmp_path):
    ds = _make_dataset(tmp_path, n_train=800, n_val=200)
    res = train(ds, params=TINY, seed=42)
    assert 0.0 <= res["metrics"]["macro_f1"] <= 1.0


# --- D. metrics + threshold analysis ----------------------------------------

def test_classification_and_threshold_metrics_present(tmp_path):
    ds = _make_dataset(tmp_path)
    res = train(ds, params=TINY, seed=42)
    m = res["metrics"]
    for k in ("accuracy", "balanced_accuracy", "macro_f1", "weighted_f1",
              "per_class", "confusion_matrix", "prediction_distribution",
              "true_label_distribution", "probability_summary", "feature_count",
              "train_rows", "validation_rows"):
        assert k in m, k
    assert len(m["confusion_matrix"]["matrix"]) == 3
    fi = res["feature_importance"]
    assert fi["features"] == FEATURES and len(fi["split"]) == 4
    thr = res["threshold_analysis"]
    for t in ("0.35", "0.50", "0.70"):
        assert set(thr["thresholds"][t]) == {"long", "short", "combined"}
    assert "best_threshold_by_signed_return_minus_cost" in thr


def test_best_threshold_helper(tmp_path):
    ds = _make_dataset(tmp_path)
    res = train(ds, params=TINY, seed=42)
    # with a low min_signals the helper should find some threshold (or None if no signals)
    t = best_threshold_by_directional_precision(res["threshold_analysis"], min_signals=1)
    assert t is None or t in res["threshold_analysis"]["thresholds"]


def test_compare_to_baseline_structure():
    fake_b2 = {"best_threshold_by_signed_return_minus_cost": "0.60",
               "thresholds": {"0.60": {"combined": {"avg_signed_return": 0.002,
                              "directional_precision": 0.55, "signals_per_day": 5.0,
                              "signal_count": 600}}}}
    fake_b1 = {"models": {"logistic_regression": {
        "best_threshold_by_signed_return_minus_cost": "0.60",
        "thresholds": {"0.60": {"combined": {"avg_signed_return": 0.0025,
                       "directional_precision": 0.508, "signals_per_day": 1.0,
                       "signal_count": 122}}}}}}
    cmp = compare_to_baseline(fake_b2, fake_b1)
    assert cmp["beats_baseline"] is True                  # prec>0.5, signed>0.0015, more sig/day
    assert compare_to_baseline(fake_b2, None) is None


# --- E. artifacts -----------------------------------------------------------

def test_full_cli_run_writes_all_artifacts(tmp_path):
    ds = _make_dataset(tmp_path)
    out = tmp_path / "outputs" / "models" / "lgbm"
    summary, final = run(_args(ds, out))
    assert final == out and out.exists()
    sub = out / "lightgbm"
    for f in ("model.joblib", "metadata.json", "metrics.json", "threshold_analysis.json",
              "feature_importance.json", "feature_columns.json"):
        assert (sub / f).exists(), f
    assert (out / "summary.json").exists()
    meta = json.loads((sub / "metadata.json").read_text())
    assert meta["model_type"] == "lightgbm" and "lightgbm_version" in meta and "params" in meta
    assert summary["model_type"] == "lightgbm"
    assert "best_threshold_by_avg_signed_return_minus_cost" in summary


def test_saved_lightgbm_reloads_and_predicts(tmp_path):
    import joblib
    ds = _make_dataset(tmp_path)
    out = tmp_path / "outputs" / "models" / "lgbm"
    run(_args(ds, out))
    model = joblib.load(out / "lightgbm" / "model.joblib")
    X, _, n = load_split(ds, "validation", load_feature_columns(ds))
    proba = model.predict_proba(pd.DataFrame(X, columns=FEATURES))
    assert proba.shape == (n, 3)


def test_no_save_model_skips_joblib(tmp_path):
    ds = _make_dataset(tmp_path)
    out = tmp_path / "outputs" / "models" / "lgbm"
    run(_args(ds, out, no_save_model=True))
    assert not (out / "lightgbm" / "model.joblib").exists()
    assert (out / "lightgbm" / "metrics.json").exists()


def test_existing_output_dir_raises(tmp_path):
    ds = _make_dataset(tmp_path)
    out = tmp_path / "outputs" / "models" / "lgbm"
    out.mkdir(parents=True)
    with pytest.raises(ValueError, match="already exists"):
        preflight(_args(ds, out))


def test_comparison_written_when_compare_to_present(tmp_path):
    ds = _make_dataset(tmp_path)
    # synthetic B1 baseline file
    b1 = tmp_path / "outputs" / "models" / "b1" / "threshold_analysis.json"
    b1.parent.mkdir(parents=True)
    b1.write_text(json.dumps({"models": {"logistic_regression": {
        "best_threshold_by_signed_return_minus_cost": "0.60",
        "thresholds": {"0.60": {"combined": {"avg_signed_return": 0.001,
                       "directional_precision": 0.5, "signals_per_day": 1.0,
                       "signal_count": 100}}}}}}), encoding="utf-8")
    out = tmp_path / "outputs" / "models" / "lgbm"
    summary, _ = run(_args(ds, out, compare_to=str(b1)))
    assert summary["comparison_to_b1_lr"] is not None
    assert "beats_b1_threshold_baseline" in summary


# --- F. CLI guards ----------------------------------------------------------

def test_output_guard_rejects_backtests(tmp_path):
    ds = _make_dataset(tmp_path)
    with pytest.raises(ValueError, match="backtests"):
        preflight(_args(ds, tmp_path / "outputs" / "backtests" / "x"))


def test_output_guard_rejects_historical(tmp_path):
    ds = _make_dataset(tmp_path)
    with pytest.raises(ValueError, match="historical_data"):
        preflight(_args(ds, tmp_path / "historical_data" / "x"))


def test_output_guard_rejects_non_models(tmp_path):
    ds = _make_dataset(tmp_path)
    with pytest.raises(ValueError, match="outputs/models"):
        preflight(_args(ds, tmp_path / "outputs" / "elsewhere" / "x"))


def test_dry_run_writes_nothing(tmp_path):
    ds = _make_dataset(tmp_path)
    out = tmp_path / "outputs" / "models" / "lgbm"
    assert run(_args(ds, out, dry_run=True)) is None
    assert not out.exists()


def test_max_rows_smoke(tmp_path):
    ds = _make_dataset(tmp_path)
    out = tmp_path / "outputs" / "models" / "lgbm"
    summary, _ = run(_args(ds, out, max_train_rows=120, max_validation_rows=80))
    assert summary["train_rows"] == 120 and summary["validation_rows"] == 80
