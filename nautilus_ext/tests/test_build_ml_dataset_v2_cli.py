"""Tests for scripts/build_ml_dataset_v2.py (offline; tmp_path synthetic parquet).

Builds a tiny hive bar tree under tmp_path with the extended order-flow columns,
then exercises the direct-parquet loader + v2 builder + month-chunked writer.
Never reads the real dataset or writes real outputs. Requires pandas/pyarrow
(run on the server .venv via uv run --no-sync).
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

from research.features_v2 import FEATURE_COLUMNS_V2  # noqa: E402
from scripts.build_ml_dataset_v2 import (  # noqa: E402
    REQUIRED_BAR_COLUMNS,
    build_parser,
    load_bars,
    parse_splits,
    preflight,
    run,
)


def _write_day(root, day, n=300, *, exchange="BINANCE", venue="spot", symbol="BTCUSDT",
               bartype="1m", drop=None):
    d = (root / f"exchange={exchange}" / f"venue_type={venue}" / f"symbol={symbol}"
         / f"bar_type={bartype}" / f"date={day}")
    d.mkdir(parents=True)
    ts = pd.date_range(start=f"{day} 00:00:00", periods=n, freq="1min")
    close = [100.0 + 5.0 * math.sin(i / 7.0) + 0.01 * i for i in range(n)]
    vol = [100.0 + 10.0 * math.sin(i / 5.0) + 0.1 * i for i in range(n)]
    frac = [0.5 + 0.2 * math.sin(i / 3.0) for i in range(n)]
    df = pd.DataFrame({
        "ts": ts, "instrument_id": ["BTCUSDT.BINANCE"] * n,
        "open": [c - 0.1 for c in close], "high": [c + 0.5 for c in close],
        "low": [c - 0.5 for c in close], "close": close, "volume": vol,
        "quote_volume": [vol[i] * close[i] for i in range(n)],
        "trade_count": [int(50 + i % 7) for i in range(n)],
        "taker_buy_volume": [vol[i] * frac[i] for i in range(n)],
        "taker_buy_quote_volume": [vol[i] * close[i] * frac[i] for i in range(n)],
    })
    if drop:
        df = df.drop(columns=[drop])
    df.to_parquet(d / "part-0.parquet", index=False)


def _make_hive(tmp_path, days=("2024-06-17", "2024-06-18"), n=300, drop=None):
    root = tmp_path / "historical_data" / "market_data"
    for day in days:
        _write_day(root, day, n=n, drop=drop)
    return root


def _args(tmp_path, root, **over):
    base = dict(root=str(root), exchange="BINANCE", venue_type="spot", symbol="BTCUSDT",
                bar_type="1m", splits="train,validation",
                out=str(tmp_path / "outputs/research_datasets/ml_v2"), horizon=15,
                fee_rate=0.0005, buffer=0.0005, lead_in=120, tail=15,
                start="2024-06-17", end="2024-06-18",
                dry_run=False, plan_only=False, overwrite=False)
    base.update(over)
    return SimpleNamespace(**base)


# --- parser / guards --------------------------------------------------------

def test_parser_builds_without_loading():
    p = build_parser()
    a = p.parse_args(["--out", "outputs/research_datasets/x"])
    assert a.splits == "train,validation" and parse_splits(a.splits) == ["train", "validation"]


def test_output_guards(tmp_path):
    root = _make_hive(tmp_path)
    with pytest.raises(ValueError, match="research_datasets"):
        preflight(_args(tmp_path, root, out=str(tmp_path / "outputs/elsewhere/x")))
    with pytest.raises(ValueError, match="historical_data"):
        preflight(_args(tmp_path, root, out=str(tmp_path / "historical_data/x")))
    with pytest.raises(ValueError, match="backtests"):
        preflight(_args(tmp_path, root, out=str(tmp_path / "outputs/backtests/x")))


def test_test_split_rejected(tmp_path):
    root = _make_hive(tmp_path)
    with pytest.raises(ValueError, match="invalid splits"):
        preflight(_args(tmp_path, root, splits="train,test"))


def test_window_guards(tmp_path):
    root = _make_hive(tmp_path)
    with pytest.raises(ValueError, match="lead_in"):
        preflight(_args(tmp_path, root, lead_in=119))
    with pytest.raises(ValueError, match="tail"):
        preflight(_args(tmp_path, root, horizon=15, tail=10))


def test_existing_output_dir_rejected(tmp_path):
    root = _make_hive(tmp_path)
    out = tmp_path / "outputs/research_datasets/ml_v2"
    out.mkdir(parents=True)
    with pytest.raises(ValueError, match="already exists"):
        preflight(_args(tmp_path, root, out=str(out)))


def test_dry_run_does_not_load_or_write(tmp_path):
    root = _make_hive(tmp_path)
    out = tmp_path / "outputs/research_datasets/ml_v2"

    def _raise(*a, **k):
        raise AssertionError("loader must not run in dry-run")

    assert run(_args(tmp_path, root, out=str(out), dry_run=True), bars_loader=_raise) is None
    assert not out.exists()


# --- direct parquet loader --------------------------------------------------

def test_loader_reads_orderflow_columns(tmp_path):
    root = _make_hive(tmp_path)
    big = load_bars(_args(tmp_path, root), "2024-06-17", "2024-06-18")
    for c in REQUIRED_BAR_COLUMNS:
        assert c in big.columns
    assert "event_time_ns" in big.columns and str(big["event_time_ns"].dtype) == "int64"
    assert len(big) == 600


def test_missing_orderflow_column_raises(tmp_path):
    root = _make_hive(tmp_path, days=("2024-06-17",), drop="taker_buy_volume")
    with pytest.raises(ValueError, match="taker_buy_volume"):
        run(_args(tmp_path, root, start="2024-06-17", end="2024-06-17"))


def test_missing_trade_count_raises(tmp_path):
    root = _make_hive(tmp_path, days=("2024-06-17",), drop="trade_count")
    with pytest.raises(ValueError, match="trade_count"):
        run(_args(tmp_path, root, start="2024-06-17", end="2024-06-17"))


# --- full synthetic build ---------------------------------------------------

def test_full_v2_build_writes_dataset(tmp_path):
    root = _make_hive(tmp_path)
    out = tmp_path / "outputs/research_datasets/ml_v2"
    summary, final = run(_args(tmp_path, root, out=str(out)))
    assert final == out and out.exists()
    assert (out / "split=train").exists()
    assert not (out / "split=test").exists()
    # feature_columns.json = 46 f2_*
    fc = json.loads((out / "feature_columns.json").read_text())
    assert fc == list(FEATURE_COLUMNS_V2) and len(fc) == 46
    assert summary["keep_splits"] == ["train", "validation"]
    assert "test" not in summary["split_counts"]
    # read a part back: f2_ float32, event_time_ns int64, no NaN in kept rows
    part = next((out / "split=train").glob("*.parquet"))
    df = pd.read_parquet(part)
    assert str(df["event_time_ns"].dtype) == "int64"
    for c in FEATURE_COLUMNS_V2:
        assert str(df[c].dtype) == "float32"
        assert df[c].isna().sum() == 0


def test_plan_only_prints_v2_feature_count(tmp_path, capsys):
    root = _make_hive(tmp_path)
    out = tmp_path / "outputs/research_datasets/ml_v2"
    assert run(_args(tmp_path, root, out=str(out), plan_only=True)) is None
    assert "46" in capsys.readouterr().out


# --- source scan ------------------------------------------------------------

def test_cli_v2_source_clean():
    import scripts.build_ml_dataset_v2 as cli

    src = inspect.getsource(cli)
    for banned in ("import sklearn", "import lightgbm", "import torch", "import tensorflow",
                   "import nautilus_trader", "from nautilus_trader"):
        assert banned not in src, banned
    for forbidden in ("run_strategy", "nautilus_backtest", "build_backend", "BacktestEngine"):
        assert forbidden not in src, forbidden
    for net in ("import websocket", "import aiohttp", "import requests", "import socket"):
        assert net not in src
    for order in ("api_key", "secret", "place_order", "new_order", "cancel_order"):
        assert order not in src
