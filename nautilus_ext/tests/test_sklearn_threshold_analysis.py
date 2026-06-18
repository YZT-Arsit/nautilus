"""Tests for research/threshold_analysis.py + scripts/analyze_sklearn_thresholds.py.

The numeric core is exercised with hand-built probability arrays (no model); the
CLI is exercised with a tiny picklable stub model + synthetic validation parquet
under ``tmp_path``. Never reads the real dataset or writes real outputs/models.
Requires numpy/pandas/joblib (run on the server ``.venv`` via uv run --no-sync).
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

pytest.importorskip("numpy")
pytest.importorskip("pandas")

import numpy as np  # noqa: E402

from research.threshold_analysis import analyze, analyze_model, load_validation  # noqa: E402
from scripts.analyze_sklearn_thresholds import preflight, run  # noqa: E402

# label codes: SHORT=0, NO_TRADE=1, LONG=2; proba columns ordered [SHORT, NO_TRADE, LONG]


def _proba_row(short, no_trade, long_):
    return [short, no_trade, long_]


# --- core: LONG / SHORT filtering -------------------------------------------

def test_long_signal_filtering_and_precision():
    # 3 confident-LONG rows (P(LONG)=0.8), 2 of them truly LONG, 1 truly SHORT
    proba = np.array([_proba_row(0.1, 0.1, 0.8)] * 3)
    y = np.array([2, 2, 0])
    fr = np.array([0.01, 0.02, -0.01])
    res = analyze(proba, y, fr, thresholds=(0.5,), top_pcts=(), n_days=1)
    lon = res["thresholds"]["0.50"]["long"]
    assert lon["count"] == 3
    assert lon["precision"] == pytest.approx(2 / 3)
    assert lon["false_short_rate"] == pytest.approx(1 / 3)
    assert lon["avg_future_return_15m"] == pytest.approx((0.01 + 0.02 - 0.01) / 3)


def test_short_signal_uses_negated_return():
    proba = np.array([_proba_row(0.8, 0.1, 0.1)] * 2)
    y = np.array([0, 2])                          # one true SHORT, one true LONG (wrong)
    fr = np.array([-0.02, 0.03])                  # short_return = +0.02, -0.03
    res = analyze(proba, y, fr, thresholds=(0.5,), top_pcts=(), n_days=1)
    sho = res["thresholds"]["0.50"]["short"]
    assert sho["count"] == 2
    assert sho["precision"] == pytest.approx(0.5)
    assert sho["false_long_rate"] == pytest.approx(0.5)
    assert sho["avg_short_return_15m"] == pytest.approx((0.02 - 0.03) / 2)


def test_threshold_raises_precision_drops_coverage():
    # high-confidence LONG correct; low-confidence LONG wrong
    proba = np.array([_proba_row(0.05, 0.15, 0.80),
                      _proba_row(0.30, 0.30, 0.40)])
    y = np.array([2, 0])
    fr = np.array([0.02, -0.01])
    res = analyze(proba, y, fr, thresholds=(0.40, 0.70), top_pcts=(), n_days=1)
    lo, hi = res["thresholds"]["0.40"]["long"], res["thresholds"]["0.70"]["long"]
    assert lo["count"] == 2 and hi["count"] == 1
    assert hi["precision"] == 1.0 and lo["precision"] == pytest.approx(0.5)


# --- core: combined directional ---------------------------------------------

def test_combined_directional_metrics_and_wrong_direction():
    proba = np.array([_proba_row(0.1, 0.1, 0.8),    # pred LONG, true LONG (correct)
                      _proba_row(0.8, 0.1, 0.1),    # pred SHORT, true LONG (wrong dir)
                      _proba_row(0.1, 0.8, 0.1)])   # pred NO_TRADE -> not a signal
    y = np.array([2, 2, 1])
    fr = np.array([0.02, 0.02, 0.0])
    res = analyze(proba, y, fr, thresholds=(0.5,), top_pcts=(), n_days=2)
    c = res["thresholds"]["0.50"]["combined"]
    assert c["signal_count"] == 2                  # NO_TRADE excluded
    assert c["directional_precision"] == pytest.approx(0.5)
    assert c["wrong_direction_rate"] == pytest.approx(0.5)
    # signed: LONG row +0.02 ; SHORT row -(+0.02) = -0.02 ; mean = 0
    assert c["avg_signed_return"] == pytest.approx(0.0)
    assert c["signals_per_day"] == pytest.approx(2 / 2)


def test_no_signal_threshold_returns_none_fields():
    proba = np.array([_proba_row(0.34, 0.33, 0.33)])
    y = np.array([1])
    fr = np.array([0.0])
    res = analyze(proba, y, fr, thresholds=(0.70,), top_pcts=(), n_days=1)
    c = res["thresholds"]["0.70"]["combined"]
    assert c["signal_count"] == 0
    assert c["directional_precision"] is None and c["avg_signed_return"] is None
    assert res["thresholds"]["0.70"]["long"]["count"] == 0


def test_output_structure_keys():
    rng = np.random.default_rng(0)
    proba = rng.dirichlet([1, 1, 1], size=300)
    y = rng.integers(0, 3, size=300)
    fr = rng.normal(0, 0.01, size=300)
    res = analyze(proba, y, fr, n_days=5)
    assert set(res) >= {"validation_rows", "n_days", "label_threshold", "cost",
                        "thresholds", "top_pct", "best_threshold_by_signed_return_minus_cost"}
    for t in ("0.35", "0.50", "0.70"):
        assert set(res["thresholds"][t]) == {"long", "short", "combined"}
    for pct in ("0.20", "0.10", "0.05", "0.01"):
        assert "combined" in res["top_pct"][pct]


def test_avg_signed_return_sign():
    # all confident LONG, all truly profitable -> positive avg signed return
    proba = np.array([_proba_row(0.1, 0.1, 0.8)] * 50)
    y = np.full(50, 2)
    fr = np.full(50, 0.005)
    res = analyze(proba, y, fr, thresholds=(0.5,), top_pcts=(), n_days=1)
    c = res["thresholds"]["0.50"]["combined"]
    assert c["avg_signed_return"] == pytest.approx(0.005)
    assert c["avg_signed_return_minus_cost"] == pytest.approx(0.005 - 0.0010)
    assert c["cost_label_hit_rate"] == pytest.approx(1.0)   # 0.005 > 0.0015


# --- CLI end-to-end with stub model -----------------------------------------

class _StubModel:
    """Picklable stand-in: P(LONG) rises with f_a, P(SHORT) with -f_a."""
    classes_ = np.array([0, 1, 2])

    def predict_proba(self, X):
        a = np.asarray(X)[:, 0]
        long_ = 1.0 / (1.0 + np.exp(-3.0 * a))
        short = 1.0 / (1.0 + np.exp(3.0 * a))
        no_trade = np.full_like(a, 0.5)
        stacked = np.stack([short, no_trade, long_], axis=1)
        return stacked / stacked.sum(axis=1, keepdims=True)


FEATURES = ["f_a", "f_b", "f_c"]


def _make_validation_dataset(tmp_path, n=400):
    import pandas as pd
    ds = tmp_path / "outputs" / "research_datasets" / "ds"
    (ds / "split=validation").mkdir(parents=True)
    rng = np.random.default_rng(7)
    a = rng.normal(0, 1, n)
    fr = 0.01 * a + rng.normal(0, 0.005, n)          # return correlated with f_a
    y = np.where(fr > 0.0015, 2, np.where(fr < -0.0015, 0, 1)).astype("int8")
    df = pd.DataFrame({"f_a": a.astype("float32"),
                       "f_b": rng.normal(0, 1, n).astype("float32"),
                       "f_c": rng.normal(0, 1, n).astype("float32"),
                       "label_code": y,
                       "future_return_15m": fr.astype("float64"),
                       "event_time_ns": (np.arange(n) * 60_000_000_000
                                         + 1_767_225_600_000_000_000).astype("int64")})
    df.to_parquet(ds / "split=validation" / "part-2026-01.parquet", index=False)
    return ds


def _make_models_dir(tmp_path, names=("logistic_regression", "hist_gradient_boosting")):
    import joblib
    md = tmp_path / "outputs" / "models" / "sk_baseline"
    for name in names:
        sub = md / name
        sub.mkdir(parents=True)
        joblib.dump(_StubModel(), sub / "model.joblib")
        (sub / "feature_columns.json").write_text(json.dumps(FEATURES), encoding="utf-8")
    return md


def _args(dataset, models_dir, **over):
    base = dict(dataset=str(dataset), models_dir=str(models_dir),
                models="logistic_regression,hist_gradient_boosting", out=None,
                max_validation_rows=None, dry_run=False)
    base.update(over)
    return SimpleNamespace(**base)


def test_cli_run_writes_threshold_analysis(tmp_path):
    ds = _make_validation_dataset(tmp_path)
    md = _make_models_dir(tmp_path)
    analysis, out = run(_args(ds, md))
    assert out == md / "threshold_analysis.json" and out.exists()
    on_disk = json.loads(out.read_text())
    assert set(on_disk["models"]) == {"logistic_regression", "hist_gradient_boosting"}
    res = on_disk["models"]["logistic_regression"]
    assert res["validation_rows"] == 400 and res["n_days"] is not None
    # the stub has real directional signal -> some threshold yields signals
    assert any(res["thresholds"][t]["combined"]["signal_count"] > 0 for t in res["thresholds"])


def test_cli_dry_run_writes_nothing(tmp_path):
    ds = _make_validation_dataset(tmp_path)
    md = _make_models_dir(tmp_path)
    assert run(_args(ds, md, dry_run=True)) is None
    assert not (md / "threshold_analysis.json").exists()


def test_cli_output_guards(tmp_path):
    ds = _make_validation_dataset(tmp_path)
    md = _make_models_dir(tmp_path)
    with pytest.raises(ValueError, match="backtests"):
        preflight(_args(ds, md, out=str(tmp_path / "outputs" / "backtests" / "x.json")))
    with pytest.raises(ValueError, match="research_datasets"):
        preflight(_args(ds, md, out=str(tmp_path / "outputs" / "research_datasets" / "x.json")))
    with pytest.raises(ValueError, match="outputs/models"):
        preflight(_args(ds, md, out=str(tmp_path / "outputs" / "elsewhere" / "x.json")))


def test_cli_missing_model_artifact_raises(tmp_path):
    ds = _make_validation_dataset(tmp_path)
    md = _make_models_dir(tmp_path, names=("logistic_regression",))
    with pytest.raises(ValueError, match="missing model artifact"):
        preflight(_args(ds, md))           # hist_gradient_boosting absent


def test_analyze_model_reorders_proba_columns(tmp_path):
    ds = _make_validation_dataset(tmp_path)
    fcols = FEATURES
    X, y, fr, ev = load_validation(ds, fcols)
    res = analyze_model(_StubModel(), X, y, fr, event_time_ns=ev)
    assert res["validation_rows"] == 400


# --- source scan ------------------------------------------------------------

def test_threshold_analysis_source_clean():
    import research.threshold_analysis as core
    import scripts.analyze_sklearn_thresholds as cli

    for mod in (core, cli):
        src = inspect.getsource(mod)
        for banned in ("import lightgbm", "from lightgbm", "import torch",
                       "import tensorflow", "import xgboost", "import catboost",
                       "import nautilus_trader", "from nautilus_trader"):
            assert banned not in src, f"{mod.__name__}: {banned}"
        for forbidden in ("run_strategy", "nautilus_backtest", "build_backend", "BacktestEngine"):
            assert forbidden not in src, f"{mod.__name__}: {forbidden}"
        for net in ("import websocket", "import aiohttp", "import requests", "import socket"):
            assert net not in src, f"{mod.__name__}: {net}"
        for order in ("api_key", "secret", "place_order", "new_order", "cancel_order"):
            assert order not in src, f"{mod.__name__}: {order}"
