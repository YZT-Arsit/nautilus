"""Tests for scripts/build_crypto_perpetual_eval_table.py.

Pure-data tests: feed synthetic summary dicts, assert the eval-table mapping,
NA handling, derived metrics, multi-symbol support, and caveat presence. No
network, no backtest, no private endpoint.
"""
from __future__ import annotations

import csv
import inspect
import math

import pytest

import scripts.build_crypto_perpetual_eval_table as etb


def _summary(**over):
    base = {
        "exchange": "BINANCE", "venue_type": "futures_um", "symbol": "BTCUSDT",
        "bar_type": "5m", "start": "2024-06-01", "end": "2024-06-07",
        "num_bars": 2016, "initial_cash": 100000.0, "final_equity": 95937.4,
        "total_return": -0.040626, "net_pnl": -4062.6,
        "max_drawdown": 0.061809, "max_drawdown_pct": 0.061809,
        "sharpe": float("nan"), "sortino": float("nan"), "volatility": float("nan"),
        "trade_count": 33, "fill_count": 66, "long_trade_count": 0,
        "short_trade_count": 33, "win_rate": 0.151515, "profit_factor": 0.49922,
        "avg_trade_pnl": -53.57, "avg_win": 352.44, "avg_loss": -126.07,
        "total_commission": 2294.9, "commission_to_gross_pnl": 1.29824,
        "turnover": float("nan"), "status": "success", "job_id": "JOB",
    }
    base.update(over)
    return base


def test_single_btcusdt_row_maps_core_fields():
    rows = etb.build_eval_rows([_summary()])
    assert len(rows) == 1
    r = rows[0]
    assert list(r.keys()) == etb.EVAL_COLUMNS          # exact schema/order
    assert r["Market Type"] == "crypto_perpetual"
    assert r["Contract Type"] == "USD-M perpetual"
    assert r["Exchange"] == "BINANCE" and r["Symbol"] == "BTCUSDT"
    assert r["Bar Type"] == "5m"
    assert r["Net PnL"] == -4062.6 and r["Total Return"] == -0.040626
    assert r["Short Trades"] == 33 and r["Long Trades"] == 0
    assert r["Status"] == "success"


def test_days_and_derived_calc():
    r = etb.build_eval_rows([_summary(start="2024-06-01", end="2024-06-07")])[0]
    assert r["Days"] == 7                               # inclusive
    # annualized = (1+tr)^(365/days)-1 ; finite for a non-total-loss return
    assert isinstance(r["Annualized Return"], float) and math.isfinite(r["Annualized Return"])


def test_missing_fields_become_na_not_fabricated():
    s = _summary()
    s.pop("profit_factor", None)                 # absent field
    for k in ("avg_win", "turnover", "sharpe"):  # present-but-NaN fields
        s[k] = float("nan")
    r = etb.build_eval_rows([s])[0]              # no equity -> sharpe/turnover NA
    assert r["Profit Factor"] == "NA"
    assert r["Sharpe"] == "NA" and r["Turnover"] == "NA"
    assert r["Avg Win"] == "NA"


def test_caveat_present_and_mentions_funding():
    r = etb.build_eval_rows([_summary()])[0]
    assert "funding" in r["Caveat"]
    assert "short sample" in r["Caveat"]          # 7d < 30d threshold


def test_multi_symbol_future_table():
    rows = etb.build_eval_rows([
        _summary(symbol="BTCUSDT"),
        _summary(symbol="ETHUSDT", net_pnl=1234.5, total_return=0.0123),
    ])
    assert len(rows) == 2
    assert [r["Symbol"] for r in rows] == ["BTCUSDT", "ETHUSDT"]
    assert rows[1]["Net PnL"] == 1234.5


def test_equity_stats_compute_when_summary_nan():
    # rising equity -> finite vol/sharpe, positive; max drawdown 0
    eq = [100000.0 * (1.0 + 0.0001 * i) for i in range(50)]
    r = etb.build_eval_rows([_summary()], equity=eq, bars_per_day=288)[0]
    assert isinstance(r["Volatility"], float) and r["Volatility"] >= 0.0
    assert isinstance(r["Sharpe"], float)          # filled from equity, not NA
    assert r["Max Drawdown"] == 0.0                # monotonic up


def test_equity_stats_drawdown_and_helpers():
    eq = [100.0, 110.0, 90.0, 95.0]
    st = etb.equity_stats(eq, bars_per_day=288)
    assert abs(st["max_drawdown_abs"] - 20.0) < 1e-9    # peak 110 -> trough 90
    assert etb.turnover_from_trades(
        [{"quantity": "1", "entry_price": "100", "exit_price": "101"}], 100000.0
    ) == pytest.approx((100.0 + 101.0) / 100000.0)
    assert etb.turnover_from_trades([], 100000.0) is None
    assert etb._annualized_return(-1.0, 7) is None      # total loss -> undefined


def test_csv_and_md_roundtrip(tmp_path):
    rows = etb.build_eval_rows([_summary()])
    csv_path = tmp_path / "evaluation_table.csv"
    md_path = tmp_path / "evaluation_table.md"
    etb.rows_to_csv(rows, csv_path)
    etb.rows_to_md(rows, md_path)
    with csv_path.open() as fh:
        got = list(csv.DictReader(fh))
    assert len(got) == 1 and got[0]["Symbol"] == "BTCUSDT"
    assert list(got[0].keys()) == etb.EVAL_COLUMNS
    md = md_path.read_text()
    assert md.startswith("| Market Type |") and "BTCUSDT" in md


def test_source_has_no_network_or_private_endpoint():
    src = inspect.getsource(etb)
    for banned in ("requests", "urllib", "http://", "https://", "api_key", "apiKey",
                   "secret", "/account", "/order", "/position", "leverage", "websocket"):
        assert banned not in src, banned
