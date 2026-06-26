"""Tests for scripts/build_vwm_notional_normalized_config.py + the runner's
per-job quantity passthrough. No network, no backtest, no strategy-logic change.
"""
from __future__ import annotations

import ast
import csv
import inspect

import pytest

import scripts.build_vwm_notional_normalized_config as nz
import scripts.run_vwm_batch_backtests as runner


def _fake_prices(prices):
    def reader(data_root, *, exchange, venue_type, symbol, bar_type, start):
        return prices.get(symbol)
    return reader


# --- sizing math ------------------------------------------------------------

def test_order_quantity_formula():
    assert nz.order_quantity(10000, 60000) == pytest.approx(10000 / 60000)
    assert nz.order_quantity(10000, 150) == pytest.approx(66.6666667, rel=1e-6)
    assert nz.order_quantity(10000, 0) is None
    assert nz.order_quantity(10000, "bad") is None


def test_build_sizing_ok_and_missing():
    prices = {"BTCUSDT": 60000.0, "ETHUSDT": 3000.0, "SOLUSDT": 150.0}  # BNB missing
    rows = nz.build_sizing(["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"],
                           exchange="BINANCE", venue_type="futures_um", bar_type="15m",
                           start="2026-03-01", target_notional_usdt=10000.0,
                           data_root=None, price_reader=_fake_prices(prices))
    by = {r["symbol"]: r for r in rows}
    assert by["BTCUSDT"]["status"] == "ok"
    assert by["BTCUSDT"]["order_quantity"] == pytest.approx(10000 / 60000, rel=1e-6)
    # actual notional ~= target for every ok symbol
    for s in ("BTCUSDT", "ETHUSDT", "SOLUSDT"):
        assert by[s]["actual_initial_notional"] == pytest.approx(10000.0, rel=1e-3)
    assert by["BNBUSDT"]["status"] == "missing_data"
    assert by["BNBUSDT"]["order_quantity"] == "NA"
    assert by["BTCUSDT"]["sizing_method"] == "initial_close_target_notional"


def test_build_config_has_per_symbol_quantity_and_skips_missing():
    prices = {"BTCUSDT": 60000.0, "SOLUSDT": 150.0}
    sizing = nz.build_sizing(["BTCUSDT", "SOLUSDT", "BNBUSDT"], exchange="BINANCE",
                             venue_type="futures_um", bar_type="15m", start="2026-03-01",
                             target_notional_usdt=10000.0, data_root=None,
                             price_reader=_fake_prices(prices))
    cfg = nz.build_config(sizing, exchange="BINANCE", venue_type="futures_um", bar_type="15m",
                          start="2026-03-01", end="2026-05-31", initial_cash=100000,
                          data_root="historical_data/market_data",
                          output_root="outputs/backtests/vwm_crypto_perpetual_2026q2_15m_notional_normalized")
    assert cfg["strategy"]["name"] == "vwm"
    assert cfg["strategy"]["params"] == {"mom_len": 5, "avg_len": 20, "atr_len": 5,
                                         "atr_pct": 0.5, "setup_len": 5}      # unchanged
    inc = cfg["universe"]["include"]
    assert [i["symbol"] for i in inc] == ["BTCUSDT", "SOLUSDT"]              # BNB skipped
    assert all("quantity" in i and i["quantity"] > 0 for i in inc)
    assert inc[0]["quantity"] == pytest.approx(10000 / 60000, rel=1e-6)


def test_main_writes_sizing_and_config(tmp_path, monkeypatch):
    monkeypatch.setattr(nz, "read_initial_close",
                        _fake_prices({"BTCUSDT": 60000.0, "ETHUSDT": 3000.0}))
    cfg_path = tmp_path / "cfg.yaml"
    sizing_path = tmp_path / "out" / "position_sizing.csv"
    rc = nz.main(["--out-config", str(cfg_path), "--out-sizing", str(sizing_path),
                  "--symbols", "BTCUSDT,ETHUSDT", "--start", "2026-03-01", "--end", "2026-05-31",
                  "--target-notional-usdt", "10000"])
    assert rc == 0
    assert cfg_path.is_file() and sizing_path.is_file()
    with sizing_path.open() as fh:
        srows = list(csv.DictReader(fh))
    assert {r["symbol"] for r in srows} == {"BTCUSDT", "ETHUSDT"}
    assert set(csv.DictReader(open(sizing_path)).fieldnames) == set(nz.SIZING_CSV_COLUMNS)
    # refuses overwrite without --overwrite
    assert nz.main(["--out-config", str(cfg_path), "--out-sizing", str(sizing_path),
                    "--symbols", "BTCUSDT", "--start", "2026-03-01", "--end", "2026-05-31"]) == 3


# --- runner per-job quantity passthrough ------------------------------------

def test_runner_threads_per_job_quantity():
    cfg = {
        "strategy": {"name": "vwm", "params": {"mom_len": 5}},
        "data": {"root": "historical_data/market_data", "start": "2026-03-01",
                 "end": "2026-05-31", "bar_type": "15m"},
        "execution": {"quantity": 1.0},
        "universe": {"include": [
            {"exchange": "BINANCE", "venue_type": "futures_um", "symbol": "BTCUSDT",
             "instrument_id": "BTCUSDT-PERP.BINANCE", "bar_type": "15m", "quantity": 0.1667},
            {"exchange": "BINANCE", "venue_type": "futures_um", "symbol": "SOLUSDT",
             "instrument_id": "SOLUSDT-PERP.BINANCE", "bar_type": "15m"},  # no quantity
        ]},
        "output": {"root": "outputs/backtests/vwm_crypto_perpetual_2026q2_15m_notional_normalized"},
    }
    jobs = runner.build_jobs(cfg)
    by = {j.symbol: j for j in jobs}
    assert by["BTCUSDT"].quantity == pytest.approx(0.1667)
    assert by["SOLUSDT"].quantity is None
    from pathlib import Path
    btc = runner._resolved_strategy_config(cfg, by["BTCUSDT"], Path("outputs/backtests/x"))
    sol = runner._resolved_strategy_config(cfg, by["SOLUSDT"], Path("outputs/backtests/x"))
    assert btc["execution"]["quantity"] == pytest.approx(0.1667)   # per-job override
    assert sol["execution"]["quantity"] == 1.0                     # falls back to global


# --- safety -----------------------------------------------------------------

def test_config_generator_no_network_or_strategy_import():
    src = inspect.getsource(nz)
    for banned in ("requests", "urllib", "http://", "https://", "api_key", "apiKey",
                   "secret", "/account", "/order", "leverage", "websocket", "cancel"):
        assert banned not in src, banned
    roots = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            roots.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".")[0])
    for forbidden in ("strategy", "feature_engine"):
        assert forbidden not in roots, forbidden
