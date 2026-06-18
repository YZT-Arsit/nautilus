"""Tests for scripts/build_ml_dataset_v2_variants.py + dataset_builder_label_variants.

Synthetic hive bar tree under tmp_path; no real dataset/outputs. Requires
pandas/pyarrow (server .venv via uv run --no-sync).
"""
from __future__ import annotations

import inspect
import json
import math
import sys
from types import SimpleNamespace
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

pytest.importorskip("pandas")

import pandas as pd  # noqa: E402

from research.dataset_builder_label_variants import build_dataset_variant  # noqa: E402
from research.features_v2 import FEATURE_COLUMNS_V2  # noqa: E402
from research.label_builder_v2 import LONG_ONLY_BINARY, MULTICLASS  # noqa: E402
from scripts.build_ml_dataset_v2_variants import build_parser, preflight, run  # noqa: E402

_MIN = 60_000_000_000
_TRAIN_START = int(pd.Timestamp("2024-07-01", tz="UTC").timestamp()) * 1_000_000_000


def _isnan(x):
    return isinstance(x, float) and math.isnan(x)


def _ext_bars(start_ns, n):
    close = [100.0 + 5.0 * math.sin(i / 7.0) + 0.01 * i for i in range(n)]
    vol = [100.0 + 10.0 * math.sin(i / 5.0) + 0.1 * i for i in range(n)]
    frac = [0.5 + 0.2 * math.sin(i / 3.0) for i in range(n)]
    return {
        "event_time_ns": [start_ns + i * _MIN for i in range(n)],
        "instrument_id": ["BTCUSDT.BINANCE"] * n,
        "open": [c - 0.1 for c in close], "high": [c + 0.5 for c in close],
        "low": [c - 0.5 for c in close], "close": close, "volume": vol,
        "quote_volume": [vol[i] * close[i] for i in range(n)],
        "trade_count": [int(50 + i % 7) for i in range(n)],
        "taker_buy_volume": [vol[i] * frac[i] for i in range(n)],
        "taker_buy_quote_volume": [vol[i] * close[i] * frac[i] for i in range(n)],
    }


def _write_day(root, day, n=300, *, drop=None):
    d = (root / "exchange=BINANCE" / "venue_type=spot" / "symbol=BTCUSDT"
         / "bar_type=1m" / f"date={day}")
    d.mkdir(parents=True)
    ts = pd.date_range(start=f"{day} 00:00:00", periods=n, freq="1min")
    b = _ext_bars(0, n)
    df = pd.DataFrame({"ts": ts, "instrument_id": b["instrument_id"], "open": b["open"],
                       "high": b["high"], "low": b["low"], "close": b["close"],
                       "volume": b["volume"], "quote_volume": b["quote_volume"],
                       "trade_count": b["trade_count"], "taker_buy_volume": b["taker_buy_volume"],
                       "taker_buy_quote_volume": b["taker_buy_quote_volume"]})
    if drop:
        df = df.drop(columns=[drop])
    df.to_parquet(d / "part-0.parquet", index=False)


def _make_hive(tmp_path, days=("2024-06-17", "2024-06-18"), n=300):
    root = tmp_path / "historical_data" / "market_data"
    for day in days:
        _write_day(root, day, n=n)
    return root


def _args(tmp_path, root, **over):
    base = dict(root=str(root), exchange="BINANCE", venue_type="spot", symbol="BTCUSDT",
                bar_type="1m", splits="train,validation",
                out=str(tmp_path / "outputs/research_datasets/ml_v2_variant"),
                task="multiclass", horizon=30, long_threshold=0.0015, short_threshold=0.0015,
                fee_rate=0.0005, buffer=0.0005, lead_in=120, tail=None,
                start="2024-06-17", end="2024-06-18",
                dry_run=False, plan_only=False, overwrite=False)
    base.update(over)
    return SimpleNamespace(**base)


# --- D. dataset builder (direct) --------------------------------------------

def test_builder_schema_horizon_and_features():
    rows, s = build_dataset_variant(_ext_bars(_TRAIN_START, 250), horizon=30, task=MULTICLASS)
    assert rows
    base = {"event_time_ns", "instrument_id", "split", "close_t", "label_horizon",
            "label_horizon_ts", "future_return_15m", "label_class", "label_code", "is_valid"}
    for r in rows:
        assert base <= set(r) and all(f in r for f in FEATURE_COLUMNS_V2)
        assert r["label_horizon"] == 30                       # horizon recorded
        assert not _isnan(r["future_return_15m"])
        for f in FEATURE_COLUMNS_V2:
            assert not _isnan(r[f])
    assert s["task_type"] == "multiclass" and s["horizon"] == 30
    assert s["label_mapping"] == {"SHORT": 0, "NO_TRADE": 1, "LONG": 2}


def test_builder_horizon_label_value_matches_close_t_plus_h():
    bars = _ext_bars(_TRAIN_START, 250)
    rows, _ = build_dataset_variant(bars, horizon=30, task=MULTICLASS)
    close = bars["close"]; ev = bars["event_time_ns"]
    idx = {t: i for i, t in enumerate(ev)}
    r = rows[0]
    t = idx[r["event_time_ns"]]
    assert abs(r["future_return_15m"] - (close[t + 30] / close[t] - 1.0)) < 1e-9


def test_builder_binary_long_only():
    rows, s = build_dataset_variant(_ext_bars(_TRAIN_START, 250), horizon=15,
                                    task=LONG_ONLY_BINARY, long_threshold=0.0015)
    codes = {r["label_code"] for r in rows}
    classes = {r["label_class"] for r in rows}
    assert codes <= {0, 1} and classes <= {"REST", "LONG"}
    assert "SHORT" not in classes
    assert s["label_mapping"] == {"REST": 0, "LONG": 1}
    assert set(s["label_distribution_total"]) == {"REST", "LONG"}


def test_builder_no_test_split_and_purge_accounting():
    rows, s = build_dataset_variant(_ext_bars(_TRAIN_START, 300), horizon=30, task=MULTICLASS)
    assert "test" not in s["split_counts"]
    total = (s["dropped_warmup_rows"] + s["dropped_horizon_rows"] + s["dropped_purge_rows"]
             + s["dropped_nan_feature_rows"] + s["dropped_no_split_rows"])
    assert s["raw_rows"] == s["output_rows"] + total
    assert s["dropped_horizon_rows"] == 30                    # last H bars invalid (no t+H)


# --- E. CLI guards ----------------------------------------------------------

def test_output_guards(tmp_path):
    root = _make_hive(tmp_path)
    with pytest.raises(ValueError, match="research_datasets"):
        preflight(_args(tmp_path, root, out=str(tmp_path / "outputs/elsewhere/x")))
    with pytest.raises(ValueError, match="historical_data"):
        preflight(_args(tmp_path, root, out=str(tmp_path / "historical_data/x")))
    with pytest.raises(ValueError, match="backtests"):
        preflight(_args(tmp_path, root, out=str(tmp_path / "outputs/backtests/x")))


def test_existing_output_rejected(tmp_path):
    root = _make_hive(tmp_path)
    out = tmp_path / "outputs/research_datasets/ml_v2_variant"
    out.mkdir(parents=True)
    with pytest.raises(ValueError, match="already exists"):
        preflight(_args(tmp_path, root, out=str(out)))


def test_test_split_rejected(tmp_path):
    root = _make_hive(tmp_path)
    with pytest.raises(ValueError, match="invalid splits"):
        preflight(_args(tmp_path, root, splits="train,test"))


def test_invalid_horizon_and_threshold_rejected(tmp_path):
    root = _make_hive(tmp_path)
    with pytest.raises(ValueError, match="horizon"):
        preflight(_args(tmp_path, root, horizon=0))
    with pytest.raises(ValueError, match="thresholds"):
        preflight(_args(tmp_path, root, long_threshold=0.0))


def test_dry_run_writes_nothing(tmp_path):
    root = _make_hive(tmp_path)
    out = tmp_path / "outputs/research_datasets/ml_v2_variant"

    def _raise(*a, **k):
        raise AssertionError("loader must not run in dry-run")

    assert run(_args(tmp_path, root, out=str(out), dry_run=True), bars_loader=_raise) is None
    assert not out.exists()


def test_plan_only_prints_variant_info(tmp_path, capsys):
    root = _make_hive(tmp_path)
    out = tmp_path / "outputs/research_datasets/ml_v2_variant"
    assert run(_args(tmp_path, root, out=str(out), plan_only=True,
                     task="long_only_binary", horizon=30)) is None
    txt = capsys.readouterr().out
    assert "long_only_binary" in txt and "30" in txt


# --- full synthetic build ---------------------------------------------------

def test_full_variant_build_writes_dataset(tmp_path):
    root = _make_hive(tmp_path)
    out = tmp_path / "outputs/research_datasets/ml_v2_h30_sym"
    summary, final = run(_args(tmp_path, root, out=str(out), horizon=30))
    assert final == out and out.exists()
    assert (out / "split=train").exists() and not (out / "split=test").exists()
    fc = json.loads((out / "feature_columns.json").read_text())
    assert fc == list(FEATURE_COLUMNS_V2) and len(fc) == 46
    part = next((out / "split=train").glob("*.parquet"))
    df = pd.read_parquet(part)
    assert (df["label_horizon"] == 30).all()
    assert df["label_code"].isin([0, 1, 2]).all()
    for c in FEATURE_COLUMNS_V2:
        assert str(df[c].dtype) == "float32" and df[c].isna().sum() == 0


def test_parser_has_task_and_threshold_args():
    p = build_parser()
    a = p.parse_args(["--out", "outputs/research_datasets/x", "--task", "long_only_binary",
                      "--horizon", "60", "--long-threshold", "0.0012"])
    assert a.task == "long_only_binary" and a.horizon == 60 and a.long_threshold == 0.0012


# --- F. source scan ---------------------------------------------------------

def test_variant_modules_source_clean():
    import research.dataset_builder_label_variants as b
    import scripts.build_ml_dataset_v2_variants as cli

    for mod in (b, cli):
        src = inspect.getsource(mod)
        for banned in ("import sklearn", "import lightgbm", "import torch", "import tensorflow",
                       "import nautilus_trader", "from nautilus_trader"):
            assert banned not in src, f"{mod.__name__}: {banned}"
        for forbidden in ("run_strategy", "nautilus_backtest", "build_backend", "BacktestEngine"):
            assert forbidden not in src, f"{mod.__name__}: {forbidden}"
        for net in ("import websocket", "import aiohttp", "import requests", "import socket"):
            assert net not in src
