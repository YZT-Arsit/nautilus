"""Tests for scripts/build_strategy_batch_eval_table.py (rows = symbol).

Synthetic batch run dir on tmp_path; no network, no backtest, no private
endpoint. Also asserts the single + matrix builders it sits beside still work and
that the evaluation modules never import strategy / feature_engine.
"""
from __future__ import annotations

import ast
import csv
import inspect
import json
from types import SimpleNamespace

import pytest

import scripts.build_strategy_batch_eval_table as bx
import scripts.build_crypto_perpetual_eval_table as base
import scripts.build_crypto_perpetual_matrix_eval_table as mx
from research import evaluation_tables as et

_MIN = 60_000_000_000
_DAY = 86_400_000_000_000


def _summary(symbol="BTCUSDT", *, status="success", **over):
    s = {
        "exchange": "BINANCE", "venue_type": "futures_um", "symbol": symbol, "bar_type": "15m",
        "start": "2026-03-01", "end": "2026-05-31", "num_bars": 8832, "initial_cash": 100000.0,
        "final_equity": 108841.87, "total_return": 0.0884187, "net_pnl": 8841.87,
        "gross_realized_pnl": 11841.87, "max_drawdown_pct": 0.0815634, "trade_count": 128,
        "fill_count": 256, "long_trade_count": 0, "short_trade_count": 128, "win_rate": 0.414062,
        "profit_factor": 1.62598, "avg_trade_pnl": 69.1, "avg_win": 300.0, "avg_loss": -120.0,
        "total_commission": 3000.0, "commission_to_gross_pnl": 0.2533,
        "avg_commission_per_trade": 23.4, "status": status,
        "job_id": f"BINANCE_futures_um_{symbol}_15m_20260301_20260531",
        "sharpe": 1.71295,
    }
    s.update(over)
    return s


def _write_job(root, summary):
    jd = root / summary["job_id"]
    jd.mkdir(parents=True, exist_ok=True)
    with (jd / "equity_curve.csv").open("w", newline="") as fh:
        w = csv.writer(fh); w.writerow(["event_time_ns", "close", "position", "equity"])
        for i in range(6):
            w.writerow([i * _DAY // 2, 100.0 + i, (-1.0 if i % 2 else 0.0),
                        100000.0 * (1 + 0.0884187 * i / 5)])
    with (jd / "trades.csv").open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["entry_time_ns", "exit_time_ns", "realized_pnl", "quantity", "entry_price", "exit_price", "win"])
        w.writerow([0, 30 * _MIN, 300.0, 1.0, 100.0, 97.0, True])
        w.writerow([_MIN, 5 * _MIN, -120.0, 1.0, 100.0, 101.2, False])
        w.writerow([2 * _MIN, 6 * _MIN, -50.0, 1.0, 100.0, 100.5, False])


def _batch_root(tmp_path):
    """BTC + ETH succeed; SOL failed; BNB absent entirely."""
    root = tmp_path / "outputs" / "backtests" / "vwm_crypto_perpetual_2026q2_15m_batch"
    root.mkdir(parents=True)
    summaries = [_summary("BTCUSDT"), _summary("ETHUSDT", total_return=-0.02, net_pnl=-2000.0,
                                               final_equity=98000.0, gross_realized_pnl=1000.0),
                 _summary("SOLUSDT", status="failed", error_message="no bars for window")]
    for s in summaries:
        if s["status"] == "success":
            _write_job(root, s)
    (root / "summary.json").write_text(json.dumps(summaries), encoding="utf-8")
    return root


def _args(tmp_path, root, **over):
    base_a = dict(backtest_root=str(root), data_root=str(root / "none"),
                  out_dir=str(tmp_path / "out"), strategy="VWM",
                  symbols="BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT", bar_type="15m",
                  start="2026-03-01", end="2026-05-31", vip_fee_ratio=0.2, half_fee_ratio=0.5,
                  no_overwrite=False)
    base_a.update(over)
    return SimpleNamespace(**base_a)


# --- orientation: rows = symbol, cols = metric ------------------------------

def test_rows_are_symbols_cols_are_metrics(tmp_path):
    root = _batch_root(tmp_path)
    rows, symbols = bx.run(_args(tmp_path, root))
    out = tmp_path / "out"
    et.write_table_csv(rows, out / "t.csv")
    with (out / "t.csv").open() as fh:
        data = list(csv.reader(fh))
    assert data[0] == et.SYMBOL_METRIC_COLUMNS                 # cols = metrics
    assert "Total Return" in data[0] and "Sharpe" in data[0]
    body = data[1:]
    assert [r[data[0].index("Symbol")] for r in body] == ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]
    assert len(body) == 4                                      # one row per symbol


def test_success_symbol_has_metrics(tmp_path):
    root = _batch_root(tmp_path)
    rows, _ = bx.run(_args(tmp_path, root))
    btc = next(r for r in rows if r["Symbol"] == "BTCUSDT")
    assert btc["Backtest Status"] == "success"
    assert btc["Total Return"] != "NA"
    assert btc["Calmar Ratio"] != "NA" and btc["Fee Drag"] != "NA"
    assert btc["Payoff Ratio"] == pytest.approx(2.5)
    assert btc["Expectancy"] != "NA" and btc["Net Direction Bias"] != "NA"
    assert btc["Max Consecutive Losses"] == 2
    # benchmark from the equity close column -> excess computable
    assert btc["Benchmark Return"] != "NA" and btc["Excess Return"] != "NA"


def test_failed_symbol_row(tmp_path):
    root = _batch_root(tmp_path)
    rows, _ = bx.run(_args(tmp_path, root))
    sol = next(r for r in rows if r["Symbol"] == "SOLUSDT")
    assert sol["Backtest Status"] == "failed"
    assert sol["Total Return"] == "NA"
    assert sol["Failure Reason"] == "no bars for window"
    assert sol["Funding Modeled"] == "No" and sol["Strategy"] == "VWM"


def test_missing_symbol_row(tmp_path):
    root = _batch_root(tmp_path)
    rows, _ = bx.run(_args(tmp_path, root))
    bnb = next(r for r in rows if r["Symbol"] == "BNBUSDT")
    assert bnb["Backtest Status"] == "missing_data"
    assert bnb["Data Quality Status"] == "missing"
    assert bnb["Total Return"] == "NA"
    assert bnb["Expected Bars"] == 92 * 96            # 2026-03-01..05-31 inclusive, 15m
    assert bnb["Funding Modeled"] == "No" and bnb["Strategy"] == "VWM"


# --- data-quality helpers ---------------------------------------------------

def test_data_quality_helpers():
    assert et.expected_bars(92, "15m") == 8832
    assert et.expected_bars(0, "15m") is None
    assert et.data_quality_status(8832, 8832) == "ok"
    assert et.data_quality_status(8832, 8000) == "partial"
    assert et.data_quality_status(8832, None) == "missing"
    assert et.data_quality_status(None, 10) == "unknown"


# --- coverage audit ---------------------------------------------------------

def test_coverage_audit_outputs(tmp_path):
    root = _batch_root(tmp_path)
    rows, symbols = bx.run(_args(tmp_path, root))
    cov = et.build_coverage_rows(rows, primary_symbol="BTCUSDT")
    by = {r["Metric"]: r for r in cov}
    assert by["Calmar Ratio"]["Status"] == "added"
    assert by["Funding-adjusted Return"]["Status"] == "planned"
    assert by["Sharpe"]["Status"] == "implemented"
    assert by["Total Return"]["Included In MD"] == "yes"
    assert by["Calmar Ratio"]["Available"] == "yes"
    out = tmp_path / "out"
    et.write_coverage_csv(cov, out / "c.csv")
    et.write_coverage_md(cov, out / "c.md")
    assert (out / "c.md").read_text().startswith("| Metric |")
    with (out / "c.csv").open() as fh:
        assert {"Category", "Reliability", "Fallback"} <= set(next(csv.reader(fh)))


def test_full_main_writes_all_outputs(tmp_path):
    root = _batch_root(tmp_path)
    rc = bx.main(["--backtest-root", str(root), "--data-root", str(root / "none"),
                  "--out-dir", str(tmp_path / "b"), "--symbols", "BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT",
                  "--bar-type", "15m", "--start", "2026-03-01", "--end", "2026-05-31"])
    assert rc == 0
    for f in ("batch_evaluation_table.csv", "batch_evaluation_table.md",
              "batch_metric_coverage_audit.csv", "batch_metric_coverage_audit.md"):
        assert (tmp_path / "b" / f).is_file()
    with (tmp_path / "b" / "batch_evaluation_table.csv").open() as fh:
        body = list(csv.DictReader(fh))
    assert [r["Symbol"] for r in body] == ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]


# --- reuse / safety ---------------------------------------------------------

def test_single_and_matrix_builders_still_work():
    s = _summary()
    row = base.build_eval_row(s, benchmark_closes=(100.0, 103.0))
    assert list(row.keys()) == base.FULL_COLUMNS
    assert mx._window_label(90) == "90d"
    assert callable(base.main) and callable(mx.main)


def _import_roots(mod) -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(ast.parse(inspect.getsource(mod))):
        if isinstance(node, ast.Import):
            roots.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def test_evaluation_modules_have_no_network_tokens():
    from research import evaluation_metrics as em
    for mod in (bx, et, em):
        src = inspect.getsource(mod)
        for banned in ("requests", "urllib", "http://", "https://", "api_key", "apiKey",
                       "secret", "/account", "/order", "leverage", "websocket", "cancel"):
            assert banned not in src, (mod.__name__, banned)


def test_evaluation_modules_do_not_import_strategy_or_feature_engine():
    from research import evaluation_metrics as em
    for mod in (bx, et, em):
        roots = _import_roots(mod)
        for forbidden in ("strategy", "feature_engine"):
            assert forbidden not in roots, (mod.__name__, forbidden)
