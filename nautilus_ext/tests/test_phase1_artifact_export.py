"""Tests for scripts/export_phase1_pnl_and_charts.py.

Synthetic backtest dir + evaluation table on tmp_path. PnL math is pure stdlib;
chart generation is tested only if matplotlib is importable. No network, no
backtest, no strategy import.
"""
from __future__ import annotations

import csv
import inspect
import json
from types import SimpleNamespace

import pytest

import scripts.export_phase1_pnl_and_charts as ex

_DAY = 86_400_000_000_000


def _equity_rows():
    # 5 bars: equity 100k -> 110k -> 105k -> 108k -> 102k ; price + position present
    base = [(0, 100.0, 0.0, 100000.0), (_DAY, 101.0, -1.0, 110000.0),
            (2 * _DAY, 102.0, -1.0, 105000.0), (3 * _DAY, 103.0, 0.0, 108000.0),
            (4 * _DAY, 104.0, -1.0, 102000.0)]
    return [{"event_time_ns": ns, "event_time": f"t{ns}", "close": c, "position": p, "equity": e}
            for (ns, c, p, e) in base]


# --- pure PnL math ----------------------------------------------------------

def test_compute_pnl_rows():
    rows = ex.compute_pnl_rows(_equity_rows())
    assert len(rows) == 5
    assert rows[0]["cumulative_pnl"] == 0.0
    assert rows[1]["cumulative_pnl"] == pytest.approx(10000.0)
    assert rows[1]["pnl"] == pytest.approx(10000.0)
    # peak after bar1 = 110k; bar2 equity 105k -> drawdown -5k, dd_pct ~ -4.5%
    assert rows[2]["drawdown"] == pytest.approx(-5000.0)
    assert rows[2]["drawdown_pct"] == pytest.approx(-5000.0 / 110000.0)
    # benchmark from close: close0=100 -> bar4 close 104 -> bench_return 0.04
    assert rows[4]["benchmark_return"] == pytest.approx(0.04)
    assert rows[1]["position"] == -1.0


def test_artifact_id():
    assert ex.artifact_id("VWM", "btcusdt", "15m", "2026Q2", "vol_targeted") == \
        "VWM_BTCUSDT_15m_2026Q2_vol_targeted"


# --- end-to-end run ---------------------------------------------------------

def _backtest_root(tmp_path):
    root = tmp_path / "bt"
    root.mkdir()
    summaries = [
        {"symbol": "BTCUSDT", "exchange": "BINANCE", "venue_type": "futures_um", "status": "success",
         "job_id": "BINANCE_futures_um_BTCUSDT_15m_20260301_20260531", "start": "2026-03-01", "end": "2026-05-31"},
        {"symbol": "ETHUSDT", "exchange": "BINANCE", "venue_type": "futures_um", "status": "failed",
         "job_id": "BINANCE_futures_um_ETHUSDT_15m_20260301_20260531", "error_message": "x"},
    ]
    (root / "summary.json").write_text(json.dumps(summaries))
    jd = root / summaries[0]["job_id"]
    jd.mkdir()
    with (jd / "equity_curve.csv").open("w", newline="") as fh:
        w = csv.writer(fh); w.writerow(["event_time_ns", "event_time", "close", "position", "equity"])
        for r in _equity_rows():
            w.writerow([r["event_time_ns"], r["event_time"], r["close"], r["position"], r["equity"]])
    (jd / "trades.csv").write_text("realized_pnl\n10\n")
    (jd / "report.json").write_text("{}")
    return root


def _eval_table(tmp_path):
    p = tmp_path / "batch_evaluation_table.csv"
    with p.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["Symbol", "Total Return", "Excess Return", "Max Drawdown %", "Trade Count", "Backtest Status"])
        w.writerow(["BTCUSDT", "-0.07", "-0.17", "0.075", "130", "success"])
        w.writerow(["ETHUSDT", "NA", "NA", "NA", "NA", "failed"])
    return p


def _args(tmp_path):
    return SimpleNamespace(
        backtest_root=str(_backtest_root(tmp_path)), evaluation_table=str(_eval_table(tmp_path)),
        deliverable_root=str(tmp_path / "deliver"), strategy="VWM", sizing_mode="vol_targeted",
        bar_type="15m", start="2026-03-01", end="2026-05-31",
        sizing_comparison_csv=None, sizing_comparison_md=None)


def test_run_builds_index_pnl_and_docs(tmp_path):
    res = ex.run(_args(tmp_path))
    deliver = tmp_path / "deliver"
    # pnl exported for the success symbol, not the failed one
    assert (deliver / "pnl" / "BTCUSDT_pnl.csv").is_file()
    assert not (deliver / "pnl" / "ETHUSDT_pnl.csv").is_file()
    # artifact_index maps rows
    with (deliver / "tables" / "artifact_index.csv").open() as fh:
        idx = {r["symbol"]: r for r in csv.DictReader(fh)}
    assert idx["BTCUSDT"]["artifact_id"] == "VWM_BTCUSDT_15m_2026Q2_vol_targeted"
    assert idx["BTCUSDT"]["pnl_path"].endswith("BTCUSDT_pnl.csv")
    assert idx["BTCUSDT"]["run_dir"].endswith("BINANCE_futures_um_BTCUSDT_15m_20260301_20260531")
    assert idx["ETHUSDT"]["evaluation_row_status"] == "failed"
    assert idx["ETHUSDT"]["pnl_path"] == "NA" and "ETHUSDT" in res["missing"]
    # delivery copy of eval table carries artifact_id
    with (deliver / "tables" / "batch_evaluation_table.csv").open() as fh:
        cols = next(csv.reader(fh))
    assert "artifact_id" in cols and "pnl_path" in cols
    # boss-facing docs
    assert (deliver / "README.md").is_file() and (deliver / "boss_summary.md").is_file()
    assert (deliver / "manifest.json").is_file() and (deliver / "raw_refs" / "run_paths.md").is_file()
    man = json.loads((deliver / "manifest.json").read_text())
    assert man["note"].startswith("no live trading")


def test_charts_generated_if_matplotlib(tmp_path):
    pytest.importorskip("matplotlib")
    rows = ex.compute_pnl_rows(_equity_rows())
    charts = ex.render_charts(rows, "BTCUSDT", tmp_path / "charts")
    assert charts["equity_curve"] and (tmp_path / "charts" / "BTCUSDT_equity_curve.png").is_file()
    assert charts["drawdown"] and charts["pnl_curve"] and charts["position"]


# --- safety -----------------------------------------------------------------

def test_no_network_or_strategy_import():
    src = inspect.getsource(ex)
    for banned in ("requests", "urllib", "http://", "https://", "api_key", "apiKey",
                   "secret", "/account", "/order", "leverage", "websocket", "cancel",
                   "os.remove", "rmtree"):
        assert banned not in src, banned
    import ast
    roots = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            roots.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".")[0])
    for forbidden in ("strategy", "feature_engine", "data_engine"):
        assert forbidden not in roots, forbidden
