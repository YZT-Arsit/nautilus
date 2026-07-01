"""Tests for trade-frequency + fee-before/after metrics (Phase 1.8 extension).

Unit tests for research/fee_frequency_metrics.py formulas + an end-to-end run of
scripts/build_phase1_result_system.py on a synthetic backtest dir (with fills.csv
+ trades.csv) asserting the fee_impact_table, PnL fee columns, fee artifacts, and
dashboard fee/frequency sections. No network, no backtest, no strategy import.
"""
from __future__ import annotations

import csv
import inspect
import json
from types import SimpleNamespace

import pytest

import research.fee_frequency_metrics as fm
import scripts.build_phase1_result_system as rs

MIN = 60_000_000_000


# --- unit: formulas ---------------------------------------------------------

def test_bar_minutes():
    assert fm.bar_minutes("1m") == 1.0
    assert fm.bar_minutes("15m") == 15.0
    assert fm.bar_minutes("1h") == 60.0
    assert fm.bar_minutes("1d") == 1440.0
    assert fm.bar_minutes("weird") is None


def test_trade_frequency_formulas():
    summary = {"num_bars": 2880, "trade_count": 20, "fill_count": 40,
               "start": "2024-07-01", "end": "2024-07-02"}          # 2 calendar days
    trades = [{"entry_time_ns": 0, "exit_time_ns": 5 * MIN, "exit_time": "2024-07-01T00:05:00"},
              {"entry_time_ns": 10 * MIN, "exit_time_ns": 25 * MIN, "exit_time": "2024-07-02T00:00:00"}]
    f = fm.trade_frequency(summary=summary, eval_row={}, bar_type="1m", trades_rows=trades)
    assert f["trading_days"] == 2 and f["bars_count"] == 2880
    assert f["trades_per_day"] == pytest.approx(10.0)               # 20 / 2
    assert f["fills_per_day"] == pytest.approx(20.0)                # 40 / 2
    assert f["trades_per_month"] == pytest.approx(20 / (2 / 30.4375))
    assert f["avg_bars_between_trades"] == pytest.approx(2880 / 20)
    assert f["avg_minutes_between_trades"] == pytest.approx(2880 * 1.0 / 20)  # 1m bars
    assert f["entry_count"] == 2 and f["exit_count"] == 2          # round-trips
    assert f["avg_holding_minutes"] == pytest.approx((5 + 15) / 2)  # (5m + 15m)/2
    assert f["max_holding_minutes"] == pytest.approx(15.0)


def test_fee_impact_fee_drag():
    summary = {"net_pnl": -12.0, "gross_realized_pnl": 100.0, "initial_cash": 100000.0}
    eval_row = {"Total Return": "-0.05", "Zero Fee Return": "0.02", "Benchmark Return": "0.10",
                "Half Fee Return": "-0.01", "VIP Fee 20% Return": "0.005",
                "Total Commission": "1200", "Avg Commission / Trade": "6",
                "Avg Commission / Fill": "3"}
    fee = fm.fee_impact(summary=summary, eval_row=eval_row)
    assert fee["net_return"] == pytest.approx(-0.05)
    assert fee["gross_return"] == pytest.approx(0.02)               # == zero-fee
    assert fee["fee_drag_return"] == pytest.approx(0.02 - (-0.05))  # zero_fee - net
    assert fee["net_excess_return"] == pytest.approx(-0.05 - 0.10)
    assert fee["zero_fee_excess_return"] == pytest.approx(0.02 - 0.10)
    assert fee["gross_pnl"] == 100.0 and fee["gross_pnl_source"] == "gross_realized_pnl"
    assert fee["avg_commission_per_trade"] == "6" and fee["avg_commission_per_fill"] == "3"


def test_fee_impact_gross_from_zero_fee_when_missing():
    summary = {"net_pnl": -12.0, "initial_cash": 100000.0}          # no gross_realized_pnl
    fee = fm.fee_impact(summary=summary, eval_row={"Zero Fee Return": "0.001", "Total Return": "-0.0001"})
    assert fee["gross_pnl"] == pytest.approx(0.001 * 100000.0)
    assert fee["gross_pnl_source"] == "zero_fee_simulation"


def test_cumulative_commission_two_pointer():
    fills = [{"event_time_ns": 1 * MIN, "commission": "3"},
             {"event_time_ns": 3 * MIN, "commission": "3"}]
    bar_ns = [0, 1 * MIN, 2 * MIN, 3 * MIN, 4 * MIN]
    cc = fm.cumulative_commission_by_ns(fills, bar_ns)
    assert cc == [0.0, 3.0, 3.0, 6.0, 6.0]
    assert fm.cumulative_commission_by_ns([], bar_ns) is None       # no fills -> None


def test_monthly_trade_counts():
    trades = [{"exit_time": "2024-07-15T00:00:00"}, {"exit_time": "2024-07-20T00:00:00"},
              {"exit_time": "2024-08-01T00:00:00"}]
    assert fm.monthly_trade_counts(trades) == [("2024-07", 2), ("2024-08", 1)]


# --- end-to-end via result system -------------------------------------------

def _backtest(tmp_path):
    root = tmp_path / "bt"; root.mkdir()
    summ = [{"symbol": "BTCUSDT", "exchange": "BINANCE", "venue_type": "futures_um",
             "status": "success", "bar_type": "1m", "params_hash": "abc", "num_bars": 5,
             "trade_count": 2, "fill_count": 4, "total_commission": 12.0,
             "gross_realized_pnl": 100.0, "net_pnl": 88.0, "initial_cash": 100000.0,
             "job_id": "BINANCE_futures_um_BTCUSDT_1m_20240701_20240702",
             "start": "2024-07-01", "end": "2024-07-02"}]
    (root / "summary.json").write_text(json.dumps(summ))
    jd = root / summ[0]["job_id"]; jd.mkdir()
    with (jd / "equity_curve.csv").open("w", newline="") as fh:
        w = csv.writer(fh); w.writerow(["event_time_ns", "event_time", "close", "position", "equity"])
        for i, (c, p, e) in enumerate([(100.0, 0.0, 100000.0), (101.0, -1.0, 100050.0),
                                       (102.0, -1.0, 99980.0), (103.0, 0.0, 100030.0), (104.0, -1.0, 99988.0)]):
            w.writerow([i * MIN, f"2024-07-01T00:0{i}:00+00:00", c, p, e])
    with (jd / "fills.csv").open("w", newline="") as fh:
        w = csv.writer(fh); w.writerow(["event_time_ns", "event_time", "instrument_id", "side",
                                        "quantity", "fill_price", "commission", "source"])
        for i in range(1, 5):
            w.writerow([i * MIN, f"t{i}", "BTCUSDT", "SELL", 1, 100 + i, 3.0, "x"])
    with (jd / "trades.csv").open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["instrument_id", "side", "quantity", "entry_time_ns", "entry_time",
                    "exit_time_ns", "exit_time", "entry_price", "exit_price", "realized_pnl", "win"])
        w.writerow(["BTCUSDT", "SELL", 1, MIN, "2024-07-01T00:01:00", 2 * MIN, "2024-07-01T00:02:00", 101, 102, -1, 0])
        w.writerow(["BTCUSDT", "SELL", 1, 3 * MIN, "2024-07-01T00:03:00", 4 * MIN, "2024-07-01T00:04:00", 103, 104, -1, 0])
    (jd / "config_resolved.yaml").write_text("strategy: vwm_short\n")
    (jd / "report.json").write_text("{}"); (jd / "positions.csv").write_text("instrument_id\n")
    et = tmp_path / "eval.csv"
    hdr = ["Strategy", "Symbol", "Bar Type", "Start", "End", "Total Return", "Zero Fee Return",
           "Fee Drag", "Benchmark Return", "Excess Return", "Zero Fee Excess Return", "Net PnL",
           "Gross PnL", "Total Commission", "Commission / Initial Cash", "Commission / |Gross PnL|",
           "Avg Commission / Trade", "Avg Commission / Fill", "Turnover", "Trade Count", "Fill Count",
           "Exposure %", "Avg Holding Bars", "Max Holding Bars", "Max Drawdown %", "Profit Factor",
           "Win Rate", "Initial Cash", "Backtest Status"]
    val = ["VWM", "BTCUSDT", "1m", "2024-07-01", "2024-07-02", "-0.00012", "0.001", "0.00112",
           "0.02", "-0.02012", "-0.019", "-12", "100", "12", "0.00012", "0.12", "6", "3", "0.04",
           "2", "4", "60", "1", "1", "0.0002", "0.5", "0", "100000", "success"]
    with et.open("w", newline="") as fh:
        w = csv.writer(fh); w.writerow(hdr); w.writerow(val)
    return root, et


def _args(tmp_path, root, et):
    return SimpleNamespace(
        backtest_root=str(root), evaluation_table=str(et), deliverable_root=str(tmp_path / "deliver"),
        strategy="VWM", strategy_version="v1", sizing_mode="vol_targeted", bar_type="1m",
        start="2024-07-01", end="2024-07-02", data_version="dv", backtest_engine="nb",
        sizing_comparison_dir=str(tmp_path / "x"), reports_archive_root=str(tmp_path / "ar"),
        archive_superseded=False, superseded_archive_root=str(tmp_path / "sup"),
        data_note="", requested_start=None, requested_end=None, now="2026-07-01T00:00:00+00:00")


@pytest.fixture
def built(tmp_path):
    root, et = _backtest(tmp_path)
    rs.run(_args(tmp_path, root, et))
    return tmp_path / "deliver"


def _read(p):
    with p.open() as fh:
        return list(csv.DictReader(fh))


def test_fee_impact_table_generated(built):
    fi = _read(built / "tables" / "fee_impact_table.csv")
    assert len(fi) == 1
    r = fi[0]
    assert r["run_uid"] and "_1m_" in r["run_uid"]
    # trade frequency fields present
    for k in ("bars_count", "trading_days", "trade_count", "trades_per_day", "trades_per_month"):
        assert k in r
    # net / zero-fee / fee-drag present
    for k in ("gross_return", "net_return", "zero_fee_return", "fee_drag_return"):
        assert k in r
    assert float(r["fee_drag_return"]) == pytest.approx(0.001 - (-0.00012))
    assert float(r["trades_per_day"]) == pytest.approx(1.0)         # 2 trades / 2 days


def test_pnl_timeseries_has_fee_fields(built):
    per = next((built / "pnl").glob("VWM_*_pnl.csv"))
    rows = _read(per)
    for k in ("equity_net", "equity_gross", "cumulative_commission", "per_bar_commission",
              "drawdown_net", "drawdown_gross", "source"):
        assert k in rows[0]
    assert float(rows[-1]["cumulative_commission"]) == pytest.approx(12.0)
    assert rows[-1]["source"] == "gross_equity_reconstructed_from_commission"


def test_pnl_gross_na_when_no_commission_timestamps(tmp_path):
    root, et = _backtest(tmp_path)
    # blank out fills so commission cannot be time-reconstructed
    jd = root / "BINANCE_futures_um_BTCUSDT_1m_20240701_20240702"
    (jd / "fills.csv").write_text("event_time_ns,commission\n")
    rs.run(_args(tmp_path, root, et))
    per = next((tmp_path / "deliver" / "pnl").glob("VWM_*_pnl.csv"))
    rows = _read(per)
    assert all(r["equity_gross"] == "NA" for r in rows)
    assert all(r["source"] == "net_only_commission_timestamps_unavailable" for r in rows)


def test_manifest_has_fee_artifacts(built):
    man = _read(built / "tables" / "artifact_manifest.csv")
    types = {m["artifact_type"] for m in man}
    assert {"fee_impact_table", "net_vs_zero_fee_equity_chart", "cumulative_commission_chart",
            "monthly_trade_count_chart"} <= types
    assert any(m["run_uid"] == "GLOBAL" and m["artifact_type"] == "fee_impact_table" for m in man)


def test_metrics_schema_has_fee_fields(built):
    sch = json.loads((built / "dashboard_data" / "metrics_schema.json").read_text())
    names = {x["metric_name"] for x in sch}
    assert {"trades_per_day", "fee_drag_return", "total_commission", "net_excess_return"} <= names
    assert all({"availability", "notes", "source_table"} <= set(x) for x in sch)


def test_dashboard_has_fee_and_frequency_sections(built):
    html = (built / "dashboard" / "index.html").read_text()
    assert "Trade frequency" in html and "Fee impact" in html
    assert "http://" not in html and "https://" not in html
    for banned in ("recommend", "outperform", "underperform", "alpha", "跑赢", "跑输"):
        assert banned not in html.lower()


def test_no_boss_or_report_in_root(built):
    names = {p.name.lower() for p in built.iterdir() if p.is_file()}
    assert "boss_summary.md" not in names
    assert not any(n.endswith("_report.md") for n in names)


# --- safety -----------------------------------------------------------------

def test_module_no_network_or_strategy_import():
    import ast
    src = inspect.getsource(fm)
    roots = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            roots.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".")[0])
    for banned in ("requests", "urllib", "http", "socket", "strategies", "feature_engine", "data_engine"):
        assert banned not in roots, banned
    for tok in ("api_key", "/order", "leverage", "os.remove", "rmtree"):
        assert tok not in src, tok
