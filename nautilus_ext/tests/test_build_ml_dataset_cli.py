"""Tests for scripts/build_ml_dataset.py (offline; monkeypatch/tmp_path only).

No real data, no real outputs, no pandas: ``load_events`` is injected and a
pandas-free JSON part-writer is used, so the CLI's orchestration is fully
exercised without touching historical_data or writing real parquet.
"""
from __future__ import annotations

import inspect
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from scripts.build_ml_dataset import (  # noqa: E402
    bar_to_row,
    build_parser,
    parse_splits,
    preflight,
    run,
)


def _args(tmp_path, **over):
    base = dict(
        root=str(tmp_path), exchange="BINANCE", venue_type="spot", symbol="BTCUSDT",
        bar_type="1m", timestamp_column="ts", splits="train,validation",
        out=str(tmp_path / "outputs/research_datasets/ds"), horizon=15, fee_rate=0.0005,
        buffer=0.0005, lead_in=120, tail=15, start=None, end=None,
        dry_run=False, plan_only=False, overwrite=False,
    )
    base.update(over)
    return SimpleNamespace(**base)


def _boundary_events():
    # Dec 2025 (train) -> Jan 2026 (validation), 240 1m bars; BarEvent-like objects.
    start = int(datetime(2025, 12, 31, 21, 0, tzinfo=timezone.utc).timestamp()) * 1_000_000_000
    n = 240
    close = [100.0 + 5.0 * math.sin(i / 7.0) + 0.01 * i for i in range(n)]
    return [SimpleNamespace(event_time_ns=start + i * 60_000_000_000,
                            instrument_id="BTCUSDT.BINANCE", open=close[i] - 0.1,
                            high=close[i] + 0.5, low=close[i] - 0.5, close=close[i],
                            volume=100.0 + 0.1 * i) for i in range(n)]


def _json_writer(rows, dest):
    dest.write_text(json.dumps([r["event_time_ns"] for r in rows]), encoding="utf-8")


def _fake_loader(events):
    def _le(cfg):
        return [], list(events)
    return _le


# --- 1. import / parser does not load ---------------------------------------

def test_parser_builds_without_loading():
    p = build_parser()
    args = p.parse_args(["--out", "outputs/research_datasets/x"])
    assert args.splits == "train,validation"   # default
    assert parse_splits(args.splits) == ["train", "validation"]


# --- 2/3/4. output path guards ----------------------------------------------

def test_invalid_output_path_rejected(tmp_path):
    with pytest.raises(ValueError, match="research_datasets"):
        preflight(_args(tmp_path, out=str(tmp_path / "outputs/somewhere/ds")))


def test_historical_data_output_rejected(tmp_path):
    with pytest.raises(ValueError, match="historical_data"):
        preflight(_args(tmp_path, out=str(tmp_path / "historical_data/ds")))


def test_outputs_backtests_output_rejected(tmp_path):
    with pytest.raises(ValueError, match="backtests"):
        preflight(_args(tmp_path, out=str(tmp_path / "outputs/backtests/ds")))


# --- 5. existing dir rejected ------------------------------------------------

def test_existing_output_dir_rejected(tmp_path):
    out = tmp_path / "outputs/research_datasets/ds"
    out.mkdir(parents=True)
    with pytest.raises(ValueError, match="already exists"):
        preflight(_args(tmp_path, out=str(out)))


# --- 6/7. splits validation -------------------------------------------------

def test_invalid_splits_rejected(tmp_path):
    with pytest.raises(ValueError, match="invalid splits"):
        preflight(_args(tmp_path, splits="train,bogus"))


def test_empty_splits_rejected(tmp_path):
    with pytest.raises(ValueError, match="non-empty"):
        preflight(_args(tmp_path, splits=" , "))


# --- 8/9. window param guards -----------------------------------------------

def test_lead_in_below_120_rejected(tmp_path):
    with pytest.raises(ValueError, match="lead_in"):
        preflight(_args(tmp_path, lead_in=119))


def test_tail_below_horizon_rejected(tmp_path):
    with pytest.raises(ValueError, match="tail"):
        preflight(_args(tmp_path, horizon=15, tail=10))


# --- 10. dry-run writes nothing & does not load -----------------------------

def test_dry_run_does_not_write_or_load(tmp_path):
    def _raise(cfg):
        raise AssertionError("load_events must not be called in dry-run")

    out = tmp_path / "outputs/research_datasets/ds"
    res = run(_args(tmp_path, out=str(out), dry_run=True), load_events_fn=_raise)
    assert res is None
    assert not out.exists()


def test_plan_only_alias_does_not_write(tmp_path):
    out = tmp_path / "outputs/research_datasets/ds"
    res = run(_args(tmp_path, out=str(out), plan_only=True),
              load_events_fn=lambda cfg: (_ for _ in ()).throw(AssertionError("no load")))
    assert res is None and not out.exists()


# --- 11/12/13/14. synthetic full run ----------------------------------------

def test_synthetic_run_builds_and_writes(tmp_path):
    out = tmp_path / "outputs/research_datasets/ds"
    summary, final = run(_args(tmp_path, out=str(out)),
                         load_events_fn=_fake_loader(_boundary_events()),
                         part_writer=_json_writer)
    assert final == out and out.exists()
    assert (out / "summary.json").exists()
    assert (out / "feature_columns.json").exists()
    assert (out / "split=train").exists() and (out / "split=validation").exists()
    # 12 + 13: keep_splits = train,validation; test excluded by default.
    assert summary["keep_splits"] == ["train", "validation"]
    assert set(summary["split_counts"]) <= {"train", "validation"}
    assert "test" not in summary["split_counts"]
    # 14: summary.json present + matches returned summary output_rows.
    on_disk = json.loads((out / "summary.json").read_text())
    assert on_disk["output_rows"] == summary["output_rows"]


def test_window_defaults_to_train_validation_range(tmp_path):
    captured = {}

    def _capture(cfg):
        captured.update(cfg)
        return [], _boundary_events()

    out = tmp_path / "outputs/research_datasets/ds"
    run(_args(tmp_path, out=str(out)), load_events_fn=_capture, part_writer=_json_writer)
    assert captured["start"] == "2024-06-17"      # train start
    assert captured["end"] == "2026-04-30"        # validation end (no test)
    assert captured["filters"]["symbol"] == "BTCUSDT"


def test_bar_to_row_accepts_obj_and_dict():
    e = SimpleNamespace(event_time_ns=1, instrument_id="X", open=1.0, high=2.0,
                        low=0.5, close=1.5, volume=3.0)
    r = bar_to_row(e)
    assert r["event_time_ns"] == 1 and r["close"] == 1.5
    rd = bar_to_row({"event_time_ns": 2, "instrument_id": "Y", "open": 1, "high": 1,
                     "low": 1, "close": 1, "volume": 0})
    assert rd["event_time_ns"] == 2


# --- 15/16/17. source scan --------------------------------------------------

def test_cli_source_clean():
    import scripts.build_ml_dataset as cli

    src = inspect.getsource(cli)
    for banned in ("import sklearn", "import lightgbm", "import scipy",
                   "import torch", "import tensorflow", "import nautilus_trader",
                   "from nautilus_trader"):
        assert banned not in src, banned
    # no backtest / run_strategy / strategy_framework execution wiring
    for forbidden in ("run_strategy", "nautilus_backtest", "build_backend",
                      "BacktestEngine"):
        assert forbidden not in src, forbidden
    for net in ("import websocket", "import aiohttp", "import requests", "import socket"):
        assert net not in src
    for order in ("api_key", "secret", "place_order", "new_order", "cancel_order"):
        assert order not in src
