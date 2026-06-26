"""Tests for scripts/build_vwm_sizing_config.py (notional + realized_vol modes).

No network, no backtest, no strategy-logic change. Readers are injected so the
math runs without pyarrow / data.
"""
from __future__ import annotations

import ast
import csv
import inspect

import pytest

import scripts.build_vwm_sizing_config as sz


def _price_reader(prices):
    def r(data_root, *, exchange, venue_type, symbol, bar_type, start):
        return prices.get(symbol)
    return r


def _closes_reader(closes_by_symbol):
    def r(data_root, *, exchange, venue_type, symbol, bar_type, start, end):
        return closes_by_symbol.get(symbol, [])
    return r


def _sized(symbols, *, mode, prices=None, closes=None, target_notional=10000.0,
           target_risk=50.0, min_n=1000.0, max_n=20000.0):
    return sz.build_sizing(
        symbols, mode=mode, exchange="BINANCE", venue_type="futures_um", bar_type="15m",
        start="2026-03-01", end="2026-05-31", target_notional_usdt=target_notional,
        target_risk_usdt_per_bar=target_risk, min_notional_usdt=min_n, max_notional_usdt=max_n,
        data_root=None, price_reader=_price_reader(prices or {}),
        closes_reader=_closes_reader(closes or {}))


# --- realized vol math ------------------------------------------------------

def test_realized_vol_formula():
    import math
    # symmetric series -> two returns +x, -x, mean 0 -> population std == x
    rv = sz.realized_vol([100.0, 101.0, 100.0])
    assert rv == pytest.approx(abs(math.log(101 / 100)), rel=1e-9)
    assert sz.realized_vol([100.0, 100.0, 100.0, 100.0]) == 0.0     # flat -> 0
    assert sz.realized_vol([100.0, 101.0]) is None                  # < 3 closes


# --- notional mode (backward compatible) ------------------------------------

def test_notional_mode():
    rows = _sized(["BTCUSDT", "BNBUSDT"], mode="notional",
                  prices={"BTCUSDT": 60000.0})        # BNB missing
    by = {r["symbol"]: r for r in rows}
    assert by["BTCUSDT"]["sizing_status"] == "ok"
    assert by["BTCUSDT"]["sizing_method"] == "initial_close_target_notional"
    assert float(by["BTCUSDT"]["final_order_quantity"]) == pytest.approx(10000 / 60000, rel=1e-6)
    assert float(by["BTCUSDT"]["final_initial_notional"]) == pytest.approx(10000.0, rel=1e-3)
    assert by["BNBUSDT"]["sizing_status"] == "missing_data"


# --- realized_vol mode + caps + defenses ------------------------------------

def test_vol_mode_ok_and_caps():
    closes = {
        "OKUSDT": [100.0, 101.0] * 20,        # vol ~0.00995 -> notional ~5025 (ok)
        "HIVOL": [100.0, 130.0] * 20,         # huge vol -> notional < min -> below_min
        "LOVOL": [100.0 + i * 0.001 for i in range(40)],  # tiny vol -> notional > max -> capped
        "FLAT": [100.0] * 20,                 # vol 0 -> failed_zero_vol
        "MISS": [],                           # missing_data
        "SHORT": [100.0, 101.0],              # insufficient_data
    }
    rows = _sized(list(closes), mode="realized_vol", closes=closes)
    by = {r["symbol"]: r for r in rows}
    assert by["OKUSDT"]["sizing_status"] == "ok"
    assert by["OKUSDT"]["sizing_method"] == "realized_vol_target"
    # final notional ~= target_risk / realized_vol
    rv = sz.realized_vol(closes["OKUSDT"])
    assert float(by["OKUSDT"]["final_initial_notional"]) == pytest.approx(50.0 / rv, rel=1e-2)
    assert by["HIVOL"]["sizing_status"] == "below_min"
    assert float(by["HIVOL"]["final_initial_notional"]) == pytest.approx(1000.0, rel=1e-3)
    assert by["LOVOL"]["sizing_status"] == "capped_max_notional"
    assert float(by["LOVOL"]["final_initial_notional"]) == pytest.approx(20000.0, rel=1e-3)
    assert by["FLAT"]["sizing_status"] == "failed_zero_vol"
    assert by["MISS"]["sizing_status"] == "missing_data"
    assert by["SHORT"]["sizing_status"] == "insufficient_data"


def test_build_config_uses_final_quantity_and_skips_unusable():
    closes = {"OKUSDT": [100.0, 101.0] * 20, "FLAT": [100.0] * 20, "MISS": []}
    rows = _sized(list(closes), mode="realized_vol", closes=closes)
    cfg = sz.build_config(rows, exchange="BINANCE", venue_type="futures_um", bar_type="15m",
                          start="2026-03-01", end="2026-05-31", initial_cash=100000,
                          data_root="historical_data/market_data", output_root="outputs/backtests/x",
                          sizing_method="realized_vol_target")
    assert cfg["strategy"]["name"] == "vwm"
    inc = {i["symbol"]: i for i in cfg["universe"]["include"]}
    assert set(inc) == {"OKUSDT"}                                   # FLAT/MISS skipped
    assert inc["OKUSDT"]["quantity"] == pytest.approx(
        float(next(r for r in rows if r["symbol"] == "OKUSDT")["final_order_quantity"]))


def test_main_writes_sizing_and_config(tmp_path, monkeypatch):
    monkeypatch.setattr(sz, "read_window_closes",
                        _closes_reader({"BTCUSDT": [100.0, 101.0] * 30, "ETHUSDT": [50.0, 50.5] * 30}))
    cfg_path = tmp_path / "cfg.yaml"
    sizing_path = tmp_path / "out" / "position_sizing.csv"
    rc = sz.main(["--out-config", str(cfg_path), "--out-sizing", str(sizing_path),
                  "--symbols", "BTCUSDT,ETHUSDT", "--start", "2026-03-01", "--end", "2026-05-31",
                  "--sizing-mode", "realized_vol", "--target-risk-usdt-per-bar", "50",
                  "--min-notional-usdt", "1000", "--max-notional-usdt", "20000"])
    assert rc == 0 and cfg_path.is_file() and sizing_path.is_file()
    with sizing_path.open() as fh:
        rdr = csv.DictReader(fh)
        assert set(rdr.fieldnames) == set(sz.SIZING_CSV_COLUMNS)
        syms = {r["symbol"] for r in rdr}
    assert syms == {"BTCUSDT", "ETHUSDT"}
    # refuses overwrite
    assert sz.main(["--out-config", str(cfg_path), "--out-sizing", str(sizing_path),
                    "--symbols", "BTCUSDT", "--start", "2026-03-01", "--end", "2026-05-31",
                    "--sizing-mode", "realized_vol"]) == 3


# --- safety -----------------------------------------------------------------

def test_no_network_or_strategy_import():
    src = inspect.getsource(sz)
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
