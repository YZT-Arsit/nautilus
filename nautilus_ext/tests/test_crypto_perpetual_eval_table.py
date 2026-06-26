"""Tests for scripts/build_crypto_perpetual_eval_table.py.

Pure-data tests: feed synthetic summary/equity/trades, assert benchmark, excess,
fee scenarios, break-even, gross/commission math, exposure/holding, NA handling,
caveat presence, and CSV/MD output. No network, no backtest, no private endpoint.
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
        "total_return": -0.040626, "net_pnl": -4062.60225,
        "gross_realized_pnl": -1767.7,
        "max_drawdown": 0.061809, "max_drawdown_pct": 0.061809,
        "sharpe": float("nan"), "sortino": float("nan"), "volatility": float("nan"),
        "trade_count": 33, "fill_count": 66, "long_trade_count": 0,
        "short_trade_count": 33, "win_rate": 0.151515, "profit_factor": 0.49922,
        "avg_trade_pnl": -53.57, "avg_win": 352.44, "avg_loss": -126.07,
        "total_commission": 2294.90225, "commission_to_gross_pnl": 1.29824,
        "avg_commission_per_trade": 69.5425, "turnover": float("nan"),
        "status": "success", "job_id": "JOB",
    }
    base.update(over)
    return base


# --- benchmark / excess -----------------------------------------------------

def test_benchmark_and_excess_from_closes():
    assert etb.benchmark_return(100.0, 110.0) == pytest.approx(0.10)
    assert etb.benchmark_return(0.0, 110.0) is None      # guard div-by-zero
    eq = [{"close": 100.0, "position": 0.0, "equity": 100000.0},
          {"close": 110.0, "position": 0.0, "equity": 100000.0}]
    r = etb.build_eval_row(_summary(total_return=0.02), equity_rows=eq)
    assert r["Benchmark Return"] == pytest.approx(0.10)
    assert r["Excess Return"] == pytest.approx(0.02 - 0.10)


def test_benchmark_explicit_closes_override():
    r = etb.build_eval_row(_summary(total_return=-0.040626), benchmark_closes=(67680.4, 69000.0))
    assert r["Benchmark Return"] == pytest.approx(69000.0 / 67680.4 - 1.0)
    assert r["Excess Return"] == pytest.approx(-0.040626 - (69000.0 / 67680.4 - 1.0))


def test_benchmark_na_when_unavailable():
    r = etb.build_eval_row(_summary())            # no equity, no closes
    assert r["Benchmark Return"] == "NA" and r["Excess Return"] == "NA"


# --- fee scenarios ----------------------------------------------------------

def test_fee_scenarios_math():
    # gross = net + commission = -4062.60225 + 2294.90225 = -1767.70
    fs = etb.fee_scenarios(-4062.60225, 2294.90225, 100000.0, half_ratio=0.5, vip_ratio=0.2)
    assert fs["gross"] == pytest.approx(-1767.70)
    assert fs["zero"]["net_pnl"] == pytest.approx(-1767.70)
    assert fs["zero"]["total_return"] == pytest.approx(-1767.70 / 100000.0)
    assert fs["half"]["net_pnl"] == pytest.approx(-1767.70 - 2294.90225 * 0.5)
    assert fs["vip"]["net_pnl"] == pytest.approx(-1767.70 - 2294.90225 * 0.2)
    # gross <= 0 -> not profitable at zero fee, break-even ratio clamped to 0
    assert fs["zero_fee_profitable"] is False
    assert fs["break_even_fee_ratio_vs_current"] == 0.0
    assert "signal-quality" in fs["fee_sensitivity_note"]


def test_fee_scenarios_profitable_but_cost_sensitive():
    # gross +500, current fee 800 -> net -300 (lose), zero-fee +500 (win)
    fs = etb.fee_scenarios(-300.0, 800.0, 100000.0)
    assert fs["zero_fee_profitable"] is True
    assert fs["break_even_fee_ratio_vs_current"] == pytest.approx(500.0 / 800.0)
    assert "cost-sensitive" in fs["fee_sensitivity_note"]


def test_fee_scenario_columns_present_in_row():
    r = etb.build_eval_row(_summary())
    for col in ("Zero Fee Return", "Half Fee Return", "VIP Fee 20% Return",
                "Break-even Fee Ratio", "Zero Fee Profitable", "Net Without Commission",
                "Fee Sensitivity Note", "Zero Fee Final Equity"):
        assert col in r
    assert r["Zero Fee Profitable"] == "No"
    assert r["Net Without Commission"] == pytest.approx(-1767.70, abs=1e-2)


# --- gross / commission -----------------------------------------------------

def test_gross_from_trades_and_commission_ratios():
    trades = [{"realized_pnl": "100"}, {"realized_pnl": "-40"}, {"realized_pnl": "-60"}]
    g = etb.gross_from_trades(trades)
    assert g["gross_pnl"] == pytest.approx(0.0)
    assert g["gross_profit"] == pytest.approx(100.0)
    assert g["gross_loss"] == pytest.approx(-100.0)
    r = etb.build_eval_row(_summary(), trades=trades)
    assert r["Gross Profit"] == pytest.approx(100.0)
    assert r["Commission / Initial Cash"] == pytest.approx(2294.90225 / 100000.0)
    # commission / |net pnl|
    assert r["Commission / Net PnL"] == pytest.approx(2294.90225 / 4062.60225)
    assert r["Avg Commission / Fill"] == pytest.approx(2294.90225 / 66.0)


# --- exposure / holding -----------------------------------------------------

def test_exposure_from_positions():
    ex = etb.exposure_from_positions([0.0, 0.0, -1.0, -1.0, 0.0])
    assert ex["short_exposure_pct"] == pytest.approx(0.4)
    assert ex["long_exposure_pct"] == 0.0
    assert ex["flat_pct"] == pytest.approx(0.6)
    assert ex["exposure_pct"] == pytest.approx(0.4)


def test_holding_from_trades_minutes_and_bars():
    MIN_NS = 60_000_000_000
    trades = [{"entry_time_ns": 0, "exit_time_ns": 30 * MIN_NS},          # 30 min
              {"entry_time_ns": 0, "exit_time_ns": 10 * MIN_NS}]          # 10 min
    h = etb.holding_from_trades(trades, bar_seconds=300)
    assert h["avg_holding_minutes"] == pytest.approx(20.0)
    assert h["avg_holding_bars"] == pytest.approx(4.0)                    # 20min/5min
    assert h["max_holding_minutes"] == pytest.approx(30.0)
    assert h["max_holding_bars"] == pytest.approx(6.0)


def test_exposure_holding_columns_present_short_only():
    MIN_NS = 60_000_000_000
    eq = [{"close": 100.0, "position": 0.0, "equity": 100000.0},
          {"close": 101.0, "position": -1.0, "equity": 99900.0},
          {"close": 102.0, "position": -1.0, "equity": 99800.0}]
    trades = [{"entry_time_ns": MIN_NS, "exit_time_ns": 3 * MIN_NS,
               "realized_pnl": "-50", "quantity": "1", "entry_price": "101", "exit_price": "102"}]
    r = etb.build_eval_row(_summary(), equity_rows=eq, trades=trades)
    assert r["Long Exposure %"] == 0.0
    assert r["Short Exposure %"] == pytest.approx(2 / 3)
    assert r["Exposure %"] == pytest.approx(2 / 3)
    assert r["Avg Holding Time"] == pytest.approx(2.0)                    # minutes
    assert r["Turnover"] == pytest.approx((101.0 + 102.0) / 100000.0)


# --- NA / caveat / perp flags ----------------------------------------------

def test_missing_fields_become_na_not_fabricated():
    s = _summary()
    s.pop("profit_factor", None)
    for k in ("turnover", "sharpe"):
        s[k] = float("nan")
    r = etb.build_eval_row(s)                     # no equity/trades
    assert r["Profit Factor"] == "NA"
    assert r["Sharpe"] == "NA" and r["Turnover"] == "NA"
    assert r["Exposure %"] == "NA" and r["Avg Holding Time"] == "NA"
    assert r["Benchmark Return"] == "NA"


def test_caveat_and_perp_mechanism_flags():
    r = etb.build_eval_row(_summary())
    assert "funding" in r["Caveat"] and "short sample" in r["Caveat"]
    assert r["Funding Modeled"] == "No" and r["Margin Modeled"] == "No"
    assert r["Liquidation Modeled"] == "No" and r["Mark Price Modeled"] == "No"


def test_multi_symbol_future_table():
    rows = etb.build_eval_rows([_summary(symbol="BTCUSDT"),
                                _summary(symbol="ETHUSDT", net_pnl=1234.5, total_return=0.0123)])
    assert [r["Symbol"] for r in rows] == ["BTCUSDT", "ETHUSDT"]


def test_equity_stats_and_annualized_helpers():
    eq = [100.0, 110.0, 90.0, 95.0]
    st = etb.equity_stats(eq, bars_per_day=288)
    assert abs(st["max_drawdown_abs"] - 20.0) < 1e-9
    assert etb._annualized_return(-1.0, 7) is None         # total loss undefined


# --- output -----------------------------------------------------------------

def test_csv_full_and_md_core(tmp_path):
    rows = etb.build_eval_rows([_summary()])
    csv_path = tmp_path / "evaluation_table.csv"
    md_path = tmp_path / "evaluation_table.md"
    etb.rows_to_csv(rows, csv_path)
    etb.rows_to_md(rows, md_path)
    with csv_path.open() as fh:
        got = list(csv.DictReader(fh))
    assert list(got[0].keys()) == etb.FULL_COLUMNS         # full schema in CSV
    assert "Benchmark Return" in got[0] and "Short Exposure %" in got[0]
    md = md_path.read_text().splitlines()
    assert md[0] == "| " + " | ".join(etb.CORE_COLUMNS) + " |"   # core schema in MD
    assert "BTCUSDT" in md[2]


def test_source_has_no_network_or_private_endpoint():
    src = inspect.getsource(etb)
    for banned in ("requests", "urllib", "http://", "https://", "api_key", "apiKey",
                   "secret", "/account", "/order", "leverage", "websocket", "cancel"):
        assert banned not in src, banned
