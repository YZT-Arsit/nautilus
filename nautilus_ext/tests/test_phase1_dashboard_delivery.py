"""Acceptance tests for the Phase-1 result delivery (dashboard + traceability).

Builds a synthetic deliverable via scripts.build_phase1_result_system.run() on
tmp_path and asserts the boss-facing acceptance criteria: the static dashboard
opens locally (no CDN), every success row maps to run_uid / PnL / chart, the
artifact manifest covers each run_uid, the README is a minimal usage doc, and no
report/boss files leak into the deliverable root. No network, no backtest.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import scripts.build_phase1_result_system as rs

_DAY = 86_400_000_000_000


def _equity_rows():
    base = [(0, 100.0, 0.0, 100000.0), (_DAY, 101.0, -1.0, 110000.0),
            (2 * _DAY, 102.0, -1.0, 105000.0), (3 * _DAY, 103.0, 0.0, 108000.0),
            (4 * _DAY, 104.0, -1.0, 102000.0)]
    # ISO ts so the chart time-axis path is exercised
    return [{"event_time_ns": ns, "event_time": f"2026-03-0{i+1}T00:00:00+00:00",
             "close": c, "position": p, "equity": e}
            for i, (ns, c, p, e) in enumerate(base)]


def _has_matplotlib():
    try:
        import matplotlib  # noqa: F401,PLC0415
        return True
    except Exception:
        return False


@pytest.fixture
def deliver(tmp_path):
    root = tmp_path / "bt"
    root.mkdir()
    summaries = [
        {"symbol": "BTCUSDT", "exchange": "BINANCE", "venue_type": "futures_um", "status": "success",
         "params_hash": "31d14fddb045", "job_id": "BINANCE_futures_um_BTCUSDT_15m_20260301_20260531"},
        {"symbol": "ETHUSDT", "exchange": "BINANCE", "venue_type": "futures_um", "status": "success",
         "params_hash": "aa11bb22cc33", "job_id": "BINANCE_futures_um_ETHUSDT_15m_20260301_20260531"},
    ]
    (root / "summary.json").write_text(json.dumps(summaries))
    for s in summaries:
        jd = root / s["job_id"]
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
    et = tmp_path / "batch_evaluation_table.csv"
    with et.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["Strategy", "Symbol", "Sizing Method", "Bar Type", "Total Return",
                    "Excess Return", "Max Drawdown %", "Profit Factor", "Trade Count",
                    "Win Rate", "Sharpe", "Backtest Status"])
        w.writerow(["VWM", "BTCUSDT", "realized_vol", "15m", "-0.07", "-0.17", "0.075",
                    "0.87", "130", "0.31", "NA", "success"])
        w.writerow(["VWM", "ETHUSDT", "realized_vol", "15m", "-0.04", "-0.06", "0.05",
                    "0.91", "120", "0.33", "NA", "success"])
    d = tmp_path / "deliver"
    args = SimpleNamespace(
        backtest_root=str(root), evaluation_table=str(et), deliverable_root=str(d),
        strategy="VWM", strategy_version="v1", sizing_mode="vol_targeted", bar_type="15m",
        start="2026-03-01", end="2026-05-31", data_version="binance_vision_2026q2",
        backtest_engine="nautilus_backtest", sizing_comparison_dir=str(tmp_path / "nope"),
        reports_archive_root=str(tmp_path / "archive"), archive_superseded=True,
        superseded_archive_root=str(tmp_path / "archive" / "superseded"),
        now="2026-06-26T00:00:00+00:00")
    rs.run(args)
    return d


def _read(path: Path):
    with path.open() as fh:
        return list(csv.DictReader(fh))


# --- dashboard --------------------------------------------------------------

def test_dashboard_exists_and_local(deliver):
    idx = deliver / "dashboard" / "index.html"
    assert idx.is_file()
    html = idx.read_text()
    # no CDN / network
    assert "http://" not in html and "https://" not in html
    assert "cdn" not in html.lower() and "<script src=" not in html.lower()


def test_dashboard_embeds_local_data_and_filters(deliver):
    html = (deliver / "dashboard" / "index.html").read_text()
    # embedded local data (inline table + panels) and both filters present
    assert "BTCUSDT" in html and "ETHUSDT" in html
    assert 'id="sym"' in html and 'id="siz"' in html          # symbol + sizing filters
    assert "data-symbol" in html and "data-sizing" in html
    assert "run_uid" in html
    assert ".png" in html and "_pnl.csv" in html              # chart + pnl references


# --- artifact mapping -------------------------------------------------------

def test_every_success_row_maps_to_artifacts(deliver):
    rows = _read(deliver / "tables" / "evaluation_table_with_uid.csv")
    manifest = _read(deliver / "tables" / "artifact_manifest.csv")
    man_uids = {m["run_uid"] for m in manifest}
    ts_uids = {r["run_uid"] for r in _read(deliver / "tables" / "pnl_timeseries.csv")}
    succ = [r for r in rows if r.get("run_uid", "NA") not in ("NA", "")]
    assert len(succ) == 2
    have_mpl = _has_matplotlib()
    for r in succ:
        uid = r["run_uid"]
        assert uid and uid != "NA"                            # run_uid present
        assert r["pnl_single_path"].endswith("_pnl.csv")      # pnl path
        assert (deliver / "pnl" / f"{uid}_pnl.csv").is_file()  # per-run pnl exists
        assert r["raw_run_dir"] != "NA"
        assert uid in man_uids and uid in ts_uids             # manifest + timeseries
        if have_mpl:
            assert r["equity_curve_chart_path"] != "NA"
            assert Path(r["equity_curve_chart_path"]).is_file()
            assert r["artifact_status"] == "complete"
        else:
            assert r["artifact_status"] == "partial"


# --- README + cleanliness ---------------------------------------------------

def test_readme_minimal(deliver):
    txt = (deliver / "README.md").read_text().lower()
    for banned in ("recommend", "verdict", "outperform", "underperform", "alpha",
                   "跑赢", "跑输", "结论", "boss", "老板"):
        assert banned not in txt, banned
    assert "run_uid" in txt and "dashboard/index.html" in txt


def test_no_report_files_in_deliverable_root(deliver):
    names = {p.name.lower() for p in deliver.iterdir() if p.is_file()}
    assert "boss_summary.md" not in names
    assert not any(n.endswith("_report.md") for n in names)
