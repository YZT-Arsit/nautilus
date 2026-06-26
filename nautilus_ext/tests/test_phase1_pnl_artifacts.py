"""Tests for scripts/build_phase1_pnl_artifacts.py.

Synthetic backtest dir + evaluation table on tmp_path. PnL math is pure stdlib;
chart generation is exercised only if matplotlib is importable. No network, no
backtest, no strategy import.
"""
from __future__ import annotations

import csv
import inspect
import json
from types import SimpleNamespace

import pytest

import scripts.build_phase1_pnl_artifacts as bp
from research.backtest_artifacts import build_identity

_DAY = 86_400_000_000_000


def _equity_rows():
    base = [(0, 100.0, 0.0, 100000.0), (_DAY, 101.0, -1.0, 110000.0),
            (2 * _DAY, 102.0, -1.0, 105000.0), (3 * _DAY, 103.0, 0.0, 108000.0),
            (4 * _DAY, 104.0, -1.0, 102000.0)]
    return [{"event_time_ns": ns, "event_time": f"t{ns}", "close": c, "position": p, "equity": e}
            for (ns, c, p, e) in base]


def _identity():
    # Identity fields MUST match _args() so the derived run_uid equals the one the
    # script produces end-to-end (data_version / engine included in the hash).
    summ = {"symbol": "BTCUSDT", "exchange": "BINANCE", "venue_type": "futures_um",
            "params_hash": "31d14fddb045"}
    return build_identity(summ, strategy="VWM", sizing_mode="vol_targeted",
                          bar_type="15m", start="2026-03-01", end="2026-05-31",
                          strategy_version="v1", data_version="binance_vision_2026q2",
                          backtest_engine="nautilus_backtest")


# --- pure PnL math ----------------------------------------------------------

def test_pnl_timeseries_rows():
    ident = _identity()
    rows = bp.pnl_timeseries_rows(_equity_rows(), ident,
                                  equity_curve_path="eq.csv", positions_path="pos.csv")
    assert len(rows) == 5
    assert all(r["run_uid"] == ident.run_uid for r in rows)
    assert rows[0]["cumulative_pnl"] == 0.0
    assert rows[1]["cumulative_pnl"] == pytest.approx(10000.0)
    assert rows[1]["pnl"] == pytest.approx(10000.0)
    assert rows[2]["drawdown"] == pytest.approx(-5000.0)
    assert rows[2]["drawdown_pct"] == pytest.approx(-5000.0 / 110000.0)
    assert rows[4]["benchmark_return"] == pytest.approx(0.04)
    assert rows[1]["position"] == -1.0
    assert rows[0]["source_equity_curve_path"] == "eq.csv"


def test_pnl_missing_position_is_na():
    rows = [{"event_time": "t0", "close": 100.0, "equity": 100000.0},  # no position col
            {"event_time": "t1", "close": 101.0, "equity": 99000.0}]
    out = bp.pnl_timeseries_rows(rows, _identity(),
                                 equity_curve_path="eq.csv", positions_path="NA")
    assert all(r["position"] == "NA" for r in out)


def test_pnl_missing_close_benchmark_na():
    rows = [{"event_time": "t0", "equity": 100000.0}, {"event_time": "t1", "equity": 99000.0}]
    out = bp.pnl_timeseries_rows(rows, _identity(),
                                 equity_curve_path="eq.csv", positions_path="NA")
    assert all(r["benchmark_equity"] == "NA" and r["benchmark_return"] == "NA" for r in out)


# --- end-to-end run ---------------------------------------------------------

def _backtest_root(tmp_path):
    root = tmp_path / "bt"
    root.mkdir()
    summaries = [
        {"symbol": "BTCUSDT", "exchange": "BINANCE", "venue_type": "futures_um", "status": "success",
         "params_hash": "31d14fddb045",
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
    (jd / "config_resolved.yaml").write_text("strategy: vwm_short\nparams:\n  mom_len: 5\n")
    (jd / "trades.csv").write_text("realized_pnl\n10\n")
    (jd / "fills.csv").write_text("price\n100\n")
    (jd / "report.json").write_text("{}")
    (jd / "run_metadata.json").write_text("{}")
    (jd / "positions.csv").write_text("instrument_id\n")
    return root, jd.name


def _eval_table(tmp_path):
    p = tmp_path / "batch_evaluation_table.csv"
    with p.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["Strategy", "Symbol", "Sizing Method", "Total Return", "Excess Return",
                    "Max Drawdown %", "Trade Count", "Backtest Status"])
        w.writerow(["VWM", "BTCUSDT", "realized_vol", "-0.07", "-0.17", "0.075", "130", "success"])
        w.writerow(["VWM", "ETHUSDT", "realized_vol", "NA", "NA", "NA", "NA", "failed"])
    return p


def _args(tmp_path):
    root, _ = _backtest_root(tmp_path)
    return SimpleNamespace(
        backtest_root=str(root), evaluation_table=str(_eval_table(tmp_path)),
        deliverable_root=str(tmp_path / "deliver"), strategy="VWM", strategy_version="v1",
        sizing_mode="vol_targeted", bar_type="15m", start="2026-03-01", end="2026-05-31",
        data_version="binance_vision_2026q2", backtest_engine="nautilus_backtest",
        now="2026-06-26T00:00:00+00:00")


def test_run_builds_all_artifacts(tmp_path):
    args = _args(tmp_path)
    res = bp.run(args)
    deliver = tmp_path / "deliver"
    ident = _identity()
    run_uid = ident.run_uid

    # per-run pnl + combined timeseries
    assert (deliver / "pnl" / f"{run_uid}_pnl.csv").is_file()
    assert (deliver / "pnl" / "pnl_timeseries.csv").is_file()
    with (deliver / "pnl" / "pnl_timeseries.csv").open() as fh:
        ts = list(csv.DictReader(fh))
    assert ts and all(r["run_uid"] == run_uid for r in ts)

    # eval table with uid: success row has run_uid + pnl + chart dir + raw run dir
    with (deliver / "tables" / "batch_evaluation_table_with_uid.csv").open() as fh:
        wu = {r["Symbol"]: r for r in csv.DictReader(fh)}
    assert wu["BTCUSDT"]["run_uid"] == run_uid
    assert wu["BTCUSDT"]["pnl_single_path"].endswith(f"{run_uid}_pnl.csv")
    assert wu["BTCUSDT"]["chart_dir"].endswith("charts")
    assert wu["BTCUSDT"]["raw_run_dir"].endswith("BINANCE_futures_um_BTCUSDT_15m_20260301_20260531")
    assert wu["BTCUSDT"]["artifact_status"] in ("complete", "partial")
    # failed row -> no run_uid, status failed, no fabricated path
    assert wu["ETHUSDT"]["run_uid"] == "NA"
    assert wu["ETHUSDT"]["artifact_status"] == "failed"
    assert wu["ETHUSDT"]["pnl_single_path"] == "NA"
    assert "ETHUSDT" in res["missing"]

    # artifact manifest exists + references the run_uid
    with (deliver / "tables" / "artifact_manifest.csv").open() as fh:
        man = list(csv.DictReader(fh))
    assert man and all(m["created_at"] == "2026-06-26T00:00:00+00:00" for m in man)
    assert {"pnl_timeseries", "trades", "fills", "report_json", "run_metadata"} <= {m["artifact_type"] for m in man}
    assert all(m["run_uid"] == run_uid for m in man)

    # manifest.json records identity sources
    mj = json.loads((deliver / "manifest.json").read_text())
    assert mj["runs"][0]["run_uid"] == run_uid
    assert mj["runs"][0]["params_hash_source"] == "summary"
    assert "data_version" in mj["run_uid_fields"]

    # docs + dashboard
    assert (deliver / "README.md").is_file() and (deliver / "boss_summary.md").is_file()
    assert (deliver / "dashboard.html").is_file()
    html = (deliver / "dashboard.html").read_text()
    assert run_uid in html and "BTCUSDT" in html


def test_chart_paths_exist_or_partial(tmp_path):
    args = _args(tmp_path)
    res = bp.run(args)
    deliver = tmp_path / "deliver"
    row = next(r for r in res["with_uid"] if r["Symbol"] == "BTCUSDT")
    have_mpl = True
    try:
        import matplotlib  # noqa: F401,PLC0415
    except Exception:
        have_mpl = False
    if have_mpl:
        assert row["artifact_status"] == "complete"
        for col in ("equity_curve_chart_path", "drawdown_chart_path", "pnl_chart_path"):
            from pathlib import Path
            assert Path(row[col]).is_file()
    else:
        assert row["artifact_status"] == "partial"
        assert row["equity_curve_chart_path"] == "NA"


# --- safety -----------------------------------------------------------------

def test_no_network_or_strategy_import():
    src = inspect.getsource(bp)
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
    for forbidden in ("strategy", "feature_engine", "data_engine", "strategies"):
        assert forbidden not in roots, forbidden
