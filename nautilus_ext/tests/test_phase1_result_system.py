"""Tests for scripts/build_phase1_result_system.py.

Synthetic backtest dir + evaluation table on tmp_path. Verifies the full result
system: run_registry, evaluation_table_with_uid, core table, pnl timeseries,
per-run pnl, charts, artifact_manifest, dashboard_data, dashboard, minimal README,
and report-file archival. No network, no backtest, no strategy import.
"""
from __future__ import annotations

import csv
import inspect
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import scripts.build_phase1_result_system as rs
from research.backtest_artifacts import build_identity

_DAY = 86_400_000_000_000


def _equity_rows():
    base = [(0, 100.0, 0.0, 100000.0), (_DAY, 101.0, -1.0, 110000.0),
            (2 * _DAY, 102.0, -1.0, 105000.0), (3 * _DAY, 103.0, 0.0, 108000.0),
            (4 * _DAY, 104.0, -1.0, 102000.0)]
    return [{"event_time_ns": ns, "event_time": f"t{ns}", "close": c, "position": p, "equity": e}
            for (ns, c, p, e) in base]


def _expected_uid():
    summ = {"symbol": "BTCUSDT", "exchange": "BINANCE", "venue_type": "futures_um",
            "params_hash": "31d14fddb045"}
    return build_identity(summ, strategy="VWM", sizing_mode="vol_targeted", bar_type="15m",
                          start="2026-03-01", end="2026-05-31", strategy_version="v1",
                          data_version="binance_vision_2026q2",
                          backtest_engine="nautilus_backtest").run_uid


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
    (root / "position_sizing.csv").write_text("symbol,order_quantity\nBTCUSDT,0.15\n")
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
    return root


def _eval_table(tmp_path):
    p = tmp_path / "batch_evaluation_table.csv"
    with p.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["Strategy", "Symbol", "Sizing Method", "Bar Type", "Start", "End",
                    "Total Return", "Excess Return", "Max Drawdown %", "Profit Factor",
                    "Trade Count", "Win Rate", "Backtest Status"])
        w.writerow(["VWM", "BTCUSDT", "realized_vol", "15m", "2026-03-01", "2026-05-31",
                    "-0.07", "-0.17", "0.075", "0.87", "130", "0.31", "success"])
        w.writerow(["VWM", "ETHUSDT", "realized_vol", "15m", "2026-03-01", "2026-05-31",
                    "NA", "NA", "NA", "NA", "NA", "NA", "failed"])
    return p


def _args(tmp_path):
    return SimpleNamespace(
        backtest_root=str(_backtest_root(tmp_path)), evaluation_table=str(_eval_table(tmp_path)),
        deliverable_root=str(tmp_path / "deliver"), strategy="VWM", strategy_version="v1",
        sizing_mode="vol_targeted", bar_type="15m", start="2026-03-01", end="2026-05-31",
        data_version="binance_vision_2026q2", backtest_engine="nautilus_backtest",
        sizing_comparison_dir=str(tmp_path / "nope"),
        reports_archive_root=str(tmp_path / "archive" / "phase1_reports_removed"),
        now="2026-06-26T00:00:00+00:00")


def _read(path: Path):
    with path.open() as fh:
        return list(csv.DictReader(fh))


def test_run_registry_generated(tmp_path):
    rs.run(_args(tmp_path))
    reg = _read(tmp_path / "deliver" / "tables" / "run_registry.csv")
    assert len(reg) == 1                                   # only the success run
    r = reg[0]
    assert r["run_uid"] == _expected_uid()
    assert r["params_hash"] == "31d14fddb045" and r["params_hash_source"] == "summary"
    assert r["raw_run_dir"].endswith("BINANCE_futures_um_BTCUSDT_15m_20260301_20260531")
    assert r["status"] == "success" and r["created_at"] == "2026-06-26T00:00:00+00:00"


def test_evaluation_table_with_uid(tmp_path):
    rs.run(_args(tmp_path))
    rows = {x["Symbol"]: x for x in _read(tmp_path / "deliver" / "tables" / "evaluation_table_with_uid.csv")}
    # original metric columns preserved
    assert rows["BTCUSDT"]["Total Return"] == "-0.07"
    # every row has run_uid; success row has pnl + raw dir
    assert rows["BTCUSDT"]["run_uid"] == _expected_uid()
    assert rows["BTCUSDT"]["pnl_single_path"].endswith("_pnl.csv")
    assert rows["BTCUSDT"]["raw_run_dir"] != "NA"
    assert rows["BTCUSDT"]["artifact_status"] in ("complete", "partial")
    # failed row: no run_uid, status failed, no fabricated path
    assert rows["ETHUSDT"]["run_uid"] == "NA" and rows["ETHUSDT"]["artifact_status"] == "failed"
    assert rows["ETHUSDT"]["pnl_single_path"] == "NA"


def test_core_evaluation_table(tmp_path):
    rs.run(_args(tmp_path))
    rows = _read(tmp_path / "deliver" / "tables" / "evaluation_table.csv")
    cols = set(rows[0].keys())
    assert "run_uid" in cols and "Symbol" in cols and "Total Return" in cols


def test_pnl_timeseries_and_per_run(tmp_path):
    rs.run(_args(tmp_path))
    uid = _expected_uid()
    ts = _read(tmp_path / "deliver" / "tables" / "pnl_timeseries.csv")
    assert ts and all(r["run_uid"] == uid for r in ts)
    per = tmp_path / "deliver" / "pnl" / f"{uid}_pnl.csv"
    assert per.is_file()
    rows = _read(per)
    # drawdown correctness: peak 110k at bar1; bar2 105k -> dd -5000
    assert float(rows[2]["drawdown"]) == pytest.approx(-5000.0)
    assert float(rows[2]["drawdown_pct"]) == pytest.approx(-5000.0 / 110000.0)


def test_artifact_manifest(tmp_path):
    res = rs.run(_args(tmp_path))
    man = _read(tmp_path / "deliver" / "tables" / "artifact_manifest.csv")
    uid = _expected_uid()
    types = {m["artifact_type"] for m in man}
    assert {"pnl_timeseries", "pnl_single_csv", "raw_run_dir"} <= types
    assert all(m["run_uid"] == uid for m in man)
    assert all(m["created_at"] == "2026-06-26T00:00:00+00:00" for m in man)
    # success run has at least pnl_single + equity chart
    have_mpl = _has_matplotlib()
    pnl_single = [m for m in man if m["artifact_type"] == "pnl_single_csv"]
    assert pnl_single and pnl_single[0]["status"] == "ok"
    eq = [m for m in man if m["artifact_type"] == "equity_curve_chart"]
    assert eq and eq[0]["status"] == ("ok" if have_mpl else "partial")


def test_dashboard_data_generated(tmp_path):
    rs.run(_args(tmp_path))
    ddir = tmp_path / "deliver" / "dashboard_data"
    filters = json.loads((ddir / "filters.json").read_text())
    assert "BTCUSDT" in filters["symbols"] and filters["symbols"]
    schema = json.loads((ddir / "metrics_schema.json").read_text())
    assert any(m["metric_name"] == "Total Return" for m in schema)
    idx = json.loads((ddir / "dashboard_index.json").read_text())
    uid = _expected_uid()
    assert uid in idx["runs"]
    assert idx["runs"][uid]["pnl_single_path"].endswith("_pnl.csv")
    assert idx["runs"][uid]["manifest_records"]


def test_dashboard_html_generated(tmp_path):
    rs.run(_args(tmp_path))
    idx = tmp_path / "deliver" / "dashboard" / "index.html"
    assert idx.is_file()
    html = idx.read_text()
    assert _expected_uid() in html and "BTCUSDT" in html
    assert "http://" not in html and "https://" not in html       # no CDN / network


def test_readme_minimal_no_conclusion(tmp_path):
    rs.run(_args(tmp_path))
    txt = (tmp_path / "deliver" / "README.md").read_text().lower()
    for banned in ("recommend", "verdict", "outperform", "underperform", "alpha",
                   "好", "坏", "跑输", "结论", "boss", "老板"):
        assert banned not in txt, banned
    assert "run_uid" in txt and "dashboard/index.html" in txt


def test_no_boss_summary_or_report_in_deliverable_root(tmp_path):
    rs.run(_args(tmp_path))
    deliver = tmp_path / "deliver"
    names = {p.name.lower() for p in deliver.iterdir() if p.is_file()}
    assert "boss_summary.md" not in names
    assert not any(n.endswith("_report.md") for n in names)


def test_missing_position_handled_as_na(tmp_path):
    # build with an equity_curve that has NO position column -> position NA in pnl
    args = _args(tmp_path)
    jd = Path(args.backtest_root) / "BINANCE_futures_um_BTCUSDT_15m_20260301_20260531"
    with (jd / "equity_curve.csv").open("w", newline="") as fh:
        w = csv.writer(fh); w.writerow(["event_time_ns", "event_time", "close", "equity"])
        for r in _equity_rows():
            w.writerow([r["event_time_ns"], r["event_time"], r["close"], r["equity"]])
    rs.run(args)
    per = Path(args.deliverable_root) / "pnl" / f"{_expected_uid()}_pnl.csv"
    rows = _read(per)
    assert all(r["position"] == "NA" for r in rows)


def test_archive_reports_moves_not_deletes(tmp_path):
    args = _args(tmp_path)
    deliver = Path(args.deliverable_root)
    deliver.mkdir(parents=True, exist_ok=True)
    # pre-seed a stale report + a conclusion file in the deliverable root
    (deliver / "boss_summary.md").write_text("stale boss summary")
    (deliver / "vwm_xyz_report.md").write_text("stale report")
    res = rs.run(args)
    # both moved out, archive manifest written, originals gone (but present in archive)
    assert not (deliver / "boss_summary.md").exists()
    assert not (deliver / "vwm_xyz_report.md").exists()
    arch = Path(args.reports_archive_root)
    assert (arch / "boss_summary.md").is_file() and (arch / "vwm_xyz_report.md").is_file()
    assert (arch / "archive_manifest.csv").is_file()
    assert len(res["archived"]) == 2
    # README kept (it is usage, not a report)
    assert (deliver / "README.md").is_file()


# --- helpers / safety -------------------------------------------------------

def _has_matplotlib():
    try:
        import matplotlib  # noqa: F401,PLC0415
        return True
    except Exception:
        return False


def test_no_network_or_strategy_import():
    src = inspect.getsource(rs)
    for banned in ("requests", "urllib", "http://", "https://", "api_key", "apiKey",
                   "secret", "/account", "/order", "leverage", "websocket",
                   "os.remove", "rmtree", "shell=True"):
        assert banned not in src, banned
    import ast
    roots = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            roots.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".")[0])
    for forbidden in ("strategies", "feature_engine", "data_engine"):
        assert forbidden not in roots, forbidden
