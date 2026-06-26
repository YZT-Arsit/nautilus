"""Tests for scripts/build_crypto_perpetual_matrix_eval_table.py.

Synthetic summary/equity/trades on tmp_path; no network, no backtest, no private
endpoint. Also asserts the single-experiment builder it reuses still works.
"""
from __future__ import annotations

import csv
import inspect
import json

import pytest

import scripts.build_crypto_perpetual_matrix_eval_table as mx
import scripts.build_crypto_perpetual_eval_table as base


def _summary(bar_type="5m", days_window=("2024-06-01", "2024-06-07"), **over):
    s, e = days_window
    base_s = {
        "exchange": "BINANCE", "venue_type": "futures_um", "symbol": "BTCUSDT",
        "bar_type": bar_type, "start": s, "end": e, "num_bars": 2016,
        "initial_cash": 100000.0, "final_equity": 95937.4, "total_return": -0.040626,
        "net_pnl": -4062.6, "gross_realized_pnl": -1767.7, "max_drawdown_pct": 0.0618,
        "trade_count": 33, "fill_count": 66, "long_trade_count": 0, "short_trade_count": 33,
        "win_rate": 0.1515, "profit_factor": 0.499, "total_commission": 2294.9,
        "commission_to_gross_pnl": 1.298, "avg_commission_per_trade": 69.54,
        "status": "success", "job_id": f"BINANCE_futures_um_BTCUSDT_{bar_type}_X",
        "sharpe": float("nan"), "sortino": float("nan"), "volatility": float("nan"),
        "turnover": float("nan"),
    }
    base_s.update(over)
    return base_s


# --- window labelling -------------------------------------------------------

def test_window_label():
    assert mx._window_label(7) == "7d"
    assert mx._window_label(30) == "30d"
    assert mx._window_label(90) == "90d"
    assert mx._window_label(45) == "45d"
    assert mx._window_label("x") == "NA"


def test_matrix_columns_have_window_after_bar_type():
    i = mx.MATRIX_FULL_COLUMNS.index("Bar Type")
    assert mx.MATRIX_FULL_COLUMNS[i + 1] == "Window"
    assert "Window" in mx.MATRIX_CORE_COLUMNS


# --- ranking ----------------------------------------------------------------

def test_normalize_direction():
    assert mx._normalize([0.0, 1.0, 2.0], higher_better=True) == [0.0, 0.5, 1.0]
    assert mx._normalize([0.0, 1.0, 2.0], higher_better=False) == [1.0, 0.5, 0.0]
    assert mx._normalize([None, None], higher_better=True) == [0.0, 0.0]


def test_rank_orders_by_net_score_and_failed_last():
    rows = [
        base.build_eval_row(_summary(bar_type="5m", total_return=-0.04), benchmark_closes=(100.0, 102.0)),
        base.build_eval_row(_summary(bar_type="1h", total_return=0.03,
                                     max_drawdown_pct=0.02, profit_factor=1.5,
                                     commission_to_gross_pnl=0.3), benchmark_closes=(100.0, 102.0)),
    ]
    for r, w in zip(rows, ("7d", "90d")):
        r["Window"] = w
    rows.append({"Symbol": "BTCUSDT", "Bar Type": "15m", "Window": "30d", "Status": "failed"})
    ranked = mx.rank_rows([dict(r) for r in rows])
    assert ranked[0]["Bar Type"] == "1h"                  # best excess/dd/pf
    assert ranked[-1]["Status"] == "failed"               # failed sorts last
    assert ranked[-1]["Net Score"] in (None, "NA")
    assert [r["Rank"] for r in ranked] == [1, 2, 3]


def test_failed_job_kept_as_na_not_dropped():
    rows = [base.build_eval_row(_summary()), {"Symbol": "BTCUSDT", "Bar Type": "1h",
                                              "Window": "7d", "Status": "failed"}]
    ranked = mx.rank_rows([dict(r) for r in rows])
    assert len(ranked) == 2
    failed = [r for r in ranked if r["Status"] == "failed"][0]
    assert failed["Net Score"] is None


# --- disk discovery (multi bar_type x window) -------------------------------

def _write_run(root, suffix, *, bars, start, end, bar_types):
    rd = root / f"vwm_btcusdt_perpetual_matrix_{suffix}"
    summaries = []
    for bt in bar_types:
        jid = f"BINANCE_futures_um_BTCUSDT_{bt}_{start.replace('-','')}_{end.replace('-','')}"
        summaries.append(_summary(bar_type=bt, days_window=(start, end), num_bars=bars, job_id=jid))
        jd = rd / jid
        jd.mkdir(parents=True)
        with (jd / "equity_curve.csv").open("w", newline="") as fh:
            w = csv.writer(fh); w.writerow(["event_time_ns", "close", "position", "equity"])
            w.writerow([0, 100.0, 0.0, 100000.0]); w.writerow([300, 101.0, -1.0, 99900.0])
        with (jd / "trades.csv").open("w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["entry_time_ns", "exit_time_ns", "realized_pnl", "quantity", "entry_price", "exit_price"])
            w.writerow([0, 600_000_000_000, -50.0, 1.0, 100.0, 101.0])
    rd.mkdir(parents=True, exist_ok=True)
    (rd / "summary.json").write_text(json.dumps(summaries), encoding="utf-8")


def test_build_matrix_rows_multi_bar_and_window(tmp_path):
    root = tmp_path / "outputs" / "backtests"
    root.mkdir(parents=True)
    _write_run(root, "7d", bars=2016, start="2024-06-01", end="2024-06-07", bar_types=["5m", "15m", "1h"])
    _write_run(root, "30d", bars=8640, start="2024-06-01", end="2024-06-30", bar_types=["5m", "15m", "1h"])
    rows = mx.build_matrix_rows(root, run_prefix="vwm_btcusdt_perpetual_matrix_",
                                data_root=None)               # data_root None -> bench from equity close
    assert len(rows) == 6
    windows = {r["Window"] for r in rows}
    assert windows == {"7d", "30d"}
    bars = {(r["Bar Type"], r["Window"]) for r in rows}
    assert ("5m", "7d") in bars and ("1h", "30d") in bars
    # benchmark from equity close (100 -> 101) = +1% ; excess present
    for r in rows:
        assert r["Benchmark Return"] == pytest.approx(0.01)
        assert r["Excess Return"] != "NA"
        assert r["Short Exposure %"] == pytest.approx(0.5)   # 1 of 2 bars short


def test_full_outputs_written(tmp_path):
    root = tmp_path / "outputs" / "backtests"
    root.mkdir(parents=True)
    _write_run(root, "7d", bars=2016, start="2024-06-01", end="2024-06-07", bar_types=["5m"])
    out = tmp_path / "matrix"
    rc = mx.main(["--backtest-root", str(root), "--out-dir", str(out)])
    assert rc == 0
    with (out / "matrix_evaluation_table.csv").open() as fh:
        ev = list(csv.DictReader(fh))
    assert list(ev[0].keys()) == mx.MATRIX_FULL_COLUMNS
    assert ev[0]["Window"] == "7d"
    with (out / "matrix_ranking.csv").open() as fh:
        rk = list(csv.DictReader(fh))
    assert rk[0]["Rank"] == "1"
    assert (out / "matrix_evaluation_table.md").read_text().startswith("| Symbol |")
    assert (out / "matrix_ranking.md").read_text().startswith("| Rank |")


# --- reuse / safety ---------------------------------------------------------

def test_single_experiment_builder_still_works():
    # the matrix reuses base; the original single CLI surface must remain intact
    row = base.build_eval_row(_summary(), benchmark_closes=(100.0, 103.0))
    assert row["Benchmark Return"] == pytest.approx(0.03)
    assert list(row.keys()) == base.FULL_COLUMNS
    assert callable(base.main)


def test_source_no_network_or_private():
    src = inspect.getsource(mx)
    for banned in ("requests", "urllib", "http://", "https://", "api_key", "apiKey",
                   "secret", "/account", "/order", "leverage", "websocket", "cancel"):
        assert banned not in src, banned
