"""Workflow tests for the Binance USD-M 2-year 1m VWM pipeline (Phase 1.8).

Self-contained / synthetic: no network, no download, no backtest, no strategy
import. Covers the 1m-specific surface (1m bar_type accepted end-to-end, 2-year /
3-symbol plan, sizing field generalized off 15m, result system produces a 1m
run_uid + traceable artifacts) without touching the real 2-year data.
"""
from __future__ import annotations

import csv
import inspect
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import scripts.ingest_crypto_perpetual_bars as ing
import scripts.build_vwm_sizing_config as sz
import scripts.build_phase1_result_system as rs

_DAY = 86_400_000_000_000
REQ_START, REQ_END = "2024-07-01", "2026-06-30"
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]


# --- Step 1: ingest plan accepts 1m, 2-year, 3 symbols ----------------------

def test_ingest_plan_1m_2year_3symbols(tmp_path):
    plans = ing.build_plan(symbols=SYMBOLS, bar_type="1m", start=REQ_START, end=REQ_END,
                           out_root=tmp_path / "md", max_days=740)
    assert len(plans) == 2190                                   # 730 days x 3 symbols
    assert len({p.date for p in plans}) == 730
    assert all(p.bar_type == "1m" for p in plans)
    assert all("/1m/" in p.url for p in plans)
    assert all(p.url.startswith("https://data.binance.vision/") for p in plans)  # public read-only
    assert {p.symbol for p in plans} == set(SYMBOLS)


def test_ingest_max_days_guard_blocks_unbounded():
    # 2-year window without raising the guard must be refused (protects against
    # accidental full pulls); MAX_DAYS default is 1.
    with pytest.raises(ValueError):
        ing.build_plan(symbols=["BTCUSDT"], bar_type="1m", start=REQ_START, end=REQ_END,
                       out_root=Path("."))


# --- Step 4: sizing config supports 1m, field generalized off 15m -----------

def _closes_reader(mapping):
    def _r(_root, *, exchange, venue_type, symbol, bar_type, start, end):
        return list(mapping.get(symbol, []))
    return _r


def test_sizing_1m_config_generalized(tmp_path, monkeypatch):
    monkeypatch.setattr(sz, "read_window_closes",
                        _closes_reader({s: [100.0, 101.0, 100.5] * 40 for s in SYMBOLS}))
    cfg_path = tmp_path / "vwm_binance_um_2y_1m_vol_targeted.yaml"
    sizing_path = tmp_path / "bt" / "position_sizing.csv"
    rc = sz.main(["--out-config", str(cfg_path), "--out-sizing", str(sizing_path),
                  "--symbols", ",".join(SYMBOLS), "--start", REQ_START, "--end", REQ_END,
                  "--bar-type", "1m", "--sizing-mode", "realized_vol",
                  "--target-risk-usdt-per-bar", "50", "--min-notional-usdt", "1000",
                  "--max-notional-usdt", "20000"])
    assert rc == 0 and sizing_path.is_file()
    with sizing_path.open() as fh:
        rdr = csv.DictReader(fh)
        cols = set(rdr.fieldnames)
    # generalized field name; NOT hardcoded to 15m
    assert "realized_vol_bar" in cols and "realized_vol_15m" not in cols
    import yaml
    cfg = yaml.safe_load(cfg_path.read_text())
    assert cfg["data"]["bar_type"] == "1m"
    assert {i["symbol"] for i in cfg["universe"]["include"]} == set(SYMBOLS)
    assert all(i["bar_type"] == "1m" for i in cfg["universe"]["include"])


# --- Step 7: result system produces a 1m run_uid + traceable artifacts ------

def _equity_rows():
    base = [(0, 100.0, 0.0, 100000.0), (_DAY, 101.0, -1.0, 110000.0),
            (2 * _DAY, 102.0, -1.0, 105000.0), (3 * _DAY, 103.0, 0.0, 108000.0)]
    return [{"event_time_ns": ns, "event_time": f"2024-07-0{i+1}T00:00:00+00:00",
             "close": c, "position": p, "equity": e}
            for i, (ns, c, p, e) in enumerate(base)]


def _one_symbol_backtest(tmp_path):
    root = tmp_path / "bt"
    root.mkdir()
    summ = [{"symbol": "BTCUSDT", "exchange": "BINANCE", "venue_type": "futures_um",
             "status": "success", "bar_type": "1m", "params_hash": "abc123",
             "job_id": "BINANCE_futures_um_BTCUSDT_1m_20240701_20260630"}]
    (root / "summary.json").write_text(json.dumps(summ))
    jd = root / summ[0]["job_id"]
    jd.mkdir()
    with (jd / "equity_curve.csv").open("w", newline="") as fh:
        w = csv.writer(fh); w.writerow(["event_time_ns", "event_time", "close", "position", "equity"])
        for r in _equity_rows():
            w.writerow([r["event_time_ns"], r["event_time"], r["close"], r["position"], r["equity"]])
    (jd / "config_resolved.yaml").write_text("strategy: vwm_short\n")
    (jd / "report.json").write_text("{}")
    (jd / "positions.csv").write_text("instrument_id\n")
    et = tmp_path / "batch_evaluation_table.csv"
    with et.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["Strategy", "Symbol", "Bar Type", "Total Return", "Excess Return",
                    "Max Drawdown %", "Trade Count", "Backtest Status"])
        w.writerow(["VWM", "BTCUSDT", "1m", "-0.02", "-0.05", "0.03", "500", "success"])
    return root, et


def _args(tmp_path, root, et):
    # NOTE: --bar-type intentionally left at the 15m default to prove the result
    # system derives 1m from the per-job summary, not the CLI default.
    return SimpleNamespace(
        backtest_root=str(root), evaluation_table=str(et),
        deliverable_root=str(tmp_path / "deliver"), strategy="VWM", strategy_version="v1",
        sizing_mode="vol_targeted", bar_type="15m", start=REQ_START, end=REQ_END,
        data_version="binance_vision_2y_1m", backtest_engine="nautilus_backtest",
        sizing_comparison_dir=str(tmp_path / "nope"),
        reports_archive_root=str(tmp_path / "arch" / "reports"),
        archive_superseded=True, superseded_archive_root=str(tmp_path / "arch" / "superseded"),
        now="2026-06-30T00:00:00+00:00")


def test_result_system_1m_end_to_end(tmp_path):
    root, et = _one_symbol_backtest(tmp_path)
    res = rs.run(_args(tmp_path, root, et))
    deliver = tmp_path / "deliver"
    uid = res["registry"][0]["run_uid"]
    # run_uid reflects the real 1m bar type (from summary), not the 15m CLI default
    assert "_1m_" in uid and "_15m_" not in uid
    assert (deliver / "pnl" / f"{uid}_pnl.csv").is_file()
    with (deliver / "tables" / "evaluation_table_with_uid.csv").open() as fh:
        wu = list(csv.DictReader(fh))
    assert wu[0]["run_uid"] == uid and wu[0]["pnl_single_path"].endswith("_pnl.csv")
    ts_uids = {r["run_uid"] for r in csv.DictReader((deliver / "tables" / "pnl_timeseries.csv").open())}
    man_uids = {r["run_uid"] for r in csv.DictReader((deliver / "tables" / "artifact_manifest.csv").open())}
    assert uid in ts_uids and uid in man_uids
    # dashboard local + no CDN
    html = (deliver / "dashboard" / "index.html").read_text()
    assert (deliver / "dashboard" / "index.html").is_file()
    assert "http://" not in html and "https://" not in html and "cdn" not in html.lower()
    # README minimal, no conclusion / boss words
    readme = (deliver / "README.md").read_text().lower()
    for banned in ("recommend", "verdict", "outperform", "underperform", "alpha",
                   "跑赢", "跑输", "结论", "boss", "老板"):
        assert banned not in readme, banned
    # no report files leak into deliverable root
    root_files = {p.name.lower() for p in deliver.iterdir() if p.is_file()}
    assert "boss_summary.md" not in root_files
    assert not any(n.endswith("_report.md") for n in root_files)


# --- safety -----------------------------------------------------------------

def test_no_network_or_strategy_import_in_test():
    # This test never fetches: it only asserts on plan objects / synthetic files.
    # Forbid real network clients + private/trading tokens in the test source.
    src = inspect.getsource(inspect.getmodule(test_result_system_1m_end_to_end))
    for banned in ("import requests", "urllib.request", "api_key", "apiKey", "secret",
                   "/account", "/order", "leverage", "websocket", "private_key"):
        assert banned not in src, banned
