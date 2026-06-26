"""Tests for scripts/build_strategy_batch_eval_table.py.

Synthetic run dirs on tmp_path; no network, no backtest, no private endpoint.
Also asserts the single and matrix builders it reuses still work.
"""
from __future__ import annotations

import csv
import inspect
import json
from types import SimpleNamespace

import pytest

import scripts.build_strategy_batch_eval_table as bx
import scripts.build_crypto_perpetual_eval_table as base
import scripts.build_crypto_perpetual_matrix_eval_table as mx


def _summary(symbol="BTCUSDT", bar_type="15m", start="2024-06-01", end="2024-08-29", **over):
    s = {
        "exchange": "BINANCE", "venue_type": "futures_um", "symbol": symbol, "bar_type": bar_type,
        "start": start, "end": end, "num_bars": 8640, "initial_cash": 100000.0,
        "final_equity": 108841.87, "total_return": 0.0884187, "net_pnl": 8841.87,
        "gross_realized_pnl": 9500.0, "max_drawdown_pct": 0.0815634, "trade_count": 128,
        "fill_count": 256, "long_trade_count": 0, "short_trade_count": 128, "win_rate": 0.414062,
        "profit_factor": 1.62598, "avg_trade_pnl": 69.1, "avg_win": 300.0, "avg_loss": -120.0,
        "total_commission": 3000.0, "commission_to_gross_pnl": 0.49771,
        "avg_commission_per_trade": 23.4, "status": "success",
        "job_id": f"BINANCE_futures_um_{symbol}_{bar_type}_{start.replace('-','')}_{end.replace('-','')}",
        "sharpe": 1.71295, "sortino": float("nan"), "volatility": float("nan"), "turnover": float("nan"),
    }
    s.update(over)
    return s


_MIN = 60_000_000_000


def _write_run(root, suffix, *, start, end, cells):
    rd = root / f"vwm_btcusdt_perpetual_matrix_{suffix}"
    summaries = []
    for (sym, bt, nbars, tr) in cells:
        s = _summary(symbol=sym, bar_type=bt, start=start, end=end, num_bars=nbars,
                     total_return=tr, final_equity=100000 * (1 + tr), net_pnl=tr * 100000,
                     gross_realized_pnl=tr * 100000 + 3000)
        summaries.append(s)
        jd = rd / s["job_id"]; jd.mkdir(parents=True)
        with (jd / "equity_curve.csv").open("w", newline="") as fh:
            w = csv.writer(fh); w.writerow(["event_time_ns", "close", "position", "equity"])
            for i in range(5):
                w.writerow([i * _MIN, 100.0 + i, (-1.0 if i % 2 else 0.0), 100000.0 * (1 + tr * i / 4)])
        with (jd / "trades.csv").open("w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["entry_time_ns", "exit_time_ns", "realized_pnl", "quantity", "entry_price", "exit_price", "win"])
            w.writerow([0, 30 * _MIN, 300.0, 1.0, 100.0, 97.0, True])
            w.writerow([_MIN, 5 * _MIN, -120.0, 1.0, 100.0, 101.2, False])
            w.writerow([2 * _MIN, 6 * _MIN, -50.0, 1.0, 100.0, 100.5, False])
    rd.mkdir(parents=True, exist_ok=True)
    (rd / "summary.json").write_text(json.dumps(summaries), encoding="utf-8")


def _matrix(tmp_path):
    root = tmp_path / "outputs" / "backtests"
    root.mkdir(parents=True)
    _write_run(root, "w90d", start="2024-06-01", end="2024-08-29",
               cells=[("BTCUSDT", "15m", 8640, 0.0884187), ("BTCUSDT", "1h", 2160, 0.0335),
                      ("BTCUSDT", "5m", 25920, -0.1295)])
    _write_run(root, "w30d", start="2024-06-01", end="2024-06-30",
               cells=[("BTCUSDT", "15m", 2880, -0.0016), ("BTCUSDT", "5m", 8640, -0.0553)])
    return root


def _args(tmp_path, root, **over):
    base_a = dict(matrix_root=str(root / "vwm_btcusdt_perpetual_matrix"),
                  single_root=str(root / "none"), out_dir=str(tmp_path / "batch"),
                  strategy="VWM", symbols="BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT",
                  preferred_bar_type="15m", preferred_window="90d", allow_missing_symbols=True)
    base_a.update(over)
    return SimpleNamespace(**base_a)


# --- pure helpers -----------------------------------------------------------

def test_payoff_expectancy_and_directions():
    assert bx.payoff_ratio(300.0, -120.0) == pytest.approx(2.5)
    assert bx.payoff_ratio(1.0, 0.0) is None
    # 0.4*300 + 0.6*(-120) = 120 - 72 = 48
    assert bx.expectancy(0.4, 300.0, -120.0) == pytest.approx(48.0)
    assert bx.benchmark_direction(0.02) == "up" and bx.benchmark_direction(-0.02) == "down"
    assert bx.strategy_direction_bias(0.0, 0.45) == "short"


def test_trade_pnl_and_consecutive():
    trades = [{"realized_pnl": "300"}, {"realized_pnl": "-120"}, {"realized_pnl": "-50"},
              {"realized_pnl": "10"}, {"realized_pnl": "20"}]
    st = bx.trade_pnl_stats(trades)
    assert st["best"] == 300.0 and st["worst"] == -120.0
    assert st["median"] == 10.0
    assert st["max_consecutive_losses"] if False else st["max_consec_losses"] == 2
    assert st["max_consec_wins"] == 2


def test_daily_stats_resamples_by_day():
    rows = [{"event_time_ns": 0, "equity": 100.0},
            {"event_time_ns": bx._DAY_NS, "equity": 110.0},
            {"event_time_ns": 2 * bx._DAY_NS, "equity": 99.0}]
    ds = bx.daily_stats(rows)
    assert ds["best_day"] == pytest.approx(0.10)
    assert ds["worst_day"] == pytest.approx(-0.10)


def test_calmar_and_fee_drag_in_cell(tmp_path):
    root = _matrix(tmp_path)
    cells = bx.discover_cells(root / "vwm_btcusdt_perpetual_matrix", None)
    pref = next(c for c in cells if c["Bar Type"] == "15m" and c["Window"] == "90d")
    # Fee Drag = zero-fee return - total return ; both finite, drag > 0 (fees hurt)
    assert pref["Fee Drag"] != "NA" and float(pref["Fee Drag"]) > 0
    assert pref["Calmar Ratio"] != "NA"               # annualized/maxdd
    assert pref["Payoff Ratio"] == pytest.approx(2.5)
    assert pref["Net / Gross Ratio"] != "NA"


# --- pivot assembly ---------------------------------------------------------

def test_pivot_rows_are_metrics_cols_are_symbols(tmp_path):
    root = _matrix(tmp_path)
    per = bx.run(_args(tmp_path, root))
    out = tmp_path / "batch"
    bx.write_pivot_csv(per, out / "p.csv", ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"])
    with (out / "p.csv").open() as fh:
        rows = list(csv.reader(fh))
    assert rows[0] == ["Metric", "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]   # cols = symbols
    metrics = [r[0] for r in rows[1:]]
    assert metrics == bx.METRIC_ROWS                                          # rows = metrics
    assert "Total Return" in metrics and "Sharpe" in metrics


def test_missing_symbols_are_na_with_reason(tmp_path):
    root = _matrix(tmp_path)
    per = bx.run(_args(tmp_path, root))
    assert per["BTCUSDT"]["Status"] == "success"
    assert per["BTCUSDT"]["Total Return"] != "NA"
    for sym in ("ETHUSDT", "SOLUSDT", "BNBUSDT"):
        assert per[sym]["Status"] == "missing_comparable_backtest"
        assert per[sym]["Failure Reason"] == "no 15mx90d result yet"
        assert per[sym]["Total Return"] == "NA"
        assert per[sym]["Funding Modeled"] == "No"          # structural still filled
        assert per[sym]["Strategy"] == "VWM"


def test_matrix_stability_metrics(tmp_path):
    root = _matrix(tmp_path)
    per = bx.run(_args(tmp_path, root))
    btc = per["BTCUSDT"]
    # 5 BTC cells: 15m90d+, 1h90d+, 5m90d-, 15m30d-, 5m30d-  -> 2 positive returns
    assert int(btc["Positive Return Windows"]) == 2
    assert btc["Best Bar Type"] == "15m" and btc["Best Window"] == "90d"
    assert btc["Positive Excess Ratio"] != "NA"


def test_allow_missing_guard(tmp_path):
    root = _matrix(tmp_path)
    with pytest.raises(ValueError, match="missing comparable"):
        bx.run(_args(tmp_path, root, allow_missing_symbols=False))


def test_coverage_audit_outputs(tmp_path):
    root = _matrix(tmp_path)
    per = bx.run(_args(tmp_path, root))
    out = tmp_path / "batch"
    bx.write_coverage(per, ["BTCUSDT", "ETHUSDT"], "BTCUSDT", out / "cov.csv", out / "cov.md")
    with (out / "cov.csv").open() as fh:
        rows = list(csv.DictReader(fh))
    by = {r["Metric"]: r for r in rows}
    assert by["Calmar Ratio"]["Status"] == "added"
    assert by["Funding-adjusted Return"]["Status"] == "planned"
    assert by["Sharpe"]["Status"] == "covered"
    assert by["Calmar Ratio"]["Available(BTCUSDT)"] == "yes"
    assert (out / "cov.md").read_text().startswith("| Metric |")


def test_full_main_writes_all_outputs(tmp_path):
    root = _matrix(tmp_path)
    rc = bx.main(["--matrix-root", str(root / "vwm_btcusdt_perpetual_matrix"),
                  "--single-root", str(root / "none"), "--out-dir", str(tmp_path / "b"),
                  "--symbols", "BTCUSDT,ETHUSDT", "--preferred-bar-type", "15m",
                  "--preferred-window", "90d", "--allow-missing-symbols"])
    assert rc == 0
    for f in ("batch_evaluation_long.csv", "batch_evaluation_pivot.csv",
              "batch_evaluation_pivot.md", "metric_coverage_audit.csv", "metric_coverage_audit.md"):
        assert (tmp_path / "b" / f).is_file()
    with (tmp_path / "b" / "batch_evaluation_long.csv").open() as fh:
        long = list(csv.DictReader(fh))
    assert set(r["Symbol"] for r in long) == {"BTCUSDT", "ETHUSDT"}
    assert {"Symbol", "Metric", "Value"} == set(long[0].keys())


# --- reuse / safety ---------------------------------------------------------

def test_single_and_matrix_builders_still_work():
    s = _summary()
    row = base.build_eval_row(s, benchmark_closes=(100.0, 103.0))
    assert list(row.keys()) == base.FULL_COLUMNS
    assert mx._window_label(90) == "90d"
    assert callable(base.main) and callable(mx.main)


def test_source_no_network_or_private():
    src = inspect.getsource(bx)
    for banned in ("requests", "urllib", "http://", "https://", "api_key", "apiKey",
                   "secret", "/account", "/order", "leverage", "websocket", "cancel"):
        assert banned not in src, banned
