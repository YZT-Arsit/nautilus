"""Tests for fee / commission / net-PnL reporting in backtest_report.

Stdlib-only (no Nautilus, no pandas): builds FillRecords directly and writes a
report to a tmp dir, asserting commission is charged once and the new gross/net
metrics + consistent win flag are correct.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from strategy_framework.execution.backtest_report import write_backtest_report
from strategy_framework.execution.reports import FillRecord

_NS = 1_000_000_000


def _bars(closes):
    return [
        {"event_time_ns": (i + 1) * _NS, "instrument_id": "BTCUSDT.BINANCE",
         "open": c, "high": c, "low": c, "close": c, "volume": 1.0}
        for i, c in enumerate(closes)
    ]


def _signals(closes):
    return [{"event_time_ns": (i + 1) * _NS, "instrument_id": "BTCUSDT.BINANCE",
             "signal": "HOLD", "close": c} for i, c in enumerate(closes)]


def _fill(ts, side, qty, price, commission=None):
    meta = {} if commission is None else {"commission": commission}
    return FillRecord(instrument_id="BTCUSDT.BINANCE", side=side, quantity=qty,
                      price=price, event_time_ns=ts * _NS, source="nautilus", metadata=meta)


def _run(tmp_path, *, closes, fills, fee_rate=0.0):
    return write_backtest_report(
        output_dir=tmp_path / "run",
        run_name="t", mode="nautilus_native", backend="nautilus_backtest",
        initial_cash=100_000.0, bars=_bars(closes), signals=_signals(closes),
        intents=[], fills=fills, fee_rate=fee_rate,
    )


# --- commission charged once + net/gross metrics ----------------------------

def test_commission_once_and_net_gross_fields(tmp_path):
    # BUY @100 then SELL @110, both with explicit engine commission.
    fills = [_fill(1, "BUY", 1.0, 100.0, commission=0.5),
             _fill(2, "SELL", 1.0, 110.0, commission=0.55)]
    m = _run(tmp_path, closes=[100.0, 110.0, 110.0], fills=fills).metrics
    assert m["gross_realized_pnl"] == 10.0          # price PnL, no fees
    assert m["total_commission"] == 1.05            # 0.5 + 0.55, once
    assert m["net_realized_pnl"] == round(10.0 - 1.05, 8)
    assert m["realized_pnl"] == 10.0                # back-compat alias = gross
    # final_equity == initial + gross_realized + unrealized - total_commission
    expect_eq = 100_000.0 + m["gross_realized_pnl"] + m["unrealized_pnl"] - m["total_commission"]
    assert abs(m["final_equity"] - expect_eq) < 1e-6
    assert abs(m["net_pnl"] - (m["final_equity"] - 100_000.0)) < 1e-9
    assert m["win_rate_basis"] == "gross" and m["gross_win_rate"] == m["win_rate"]


def test_engine_commission_not_double_counted_with_fee_rate(tmp_path):
    # metadata commission present -> fee_rate must NOT add a second charge.
    fills = [_fill(1, "BUY", 1.0, 100.0, commission=2.0),
             _fill(2, "SELL", 1.0, 100.0, commission=2.0)]
    m = _run(tmp_path, closes=[100.0, 100.0, 100.0], fills=fills, fee_rate=0.0005).metrics
    assert m["total_commission"] == 4.0             # 2+2 only, NOT + fee_rate model


def test_fee_rate_fallback_when_no_engine_commission(tmp_path):
    # no metadata commission -> fall back to qty*price*fee_rate.
    fills = [_fill(1, "BUY", 1.0, 100.0), _fill(2, "SELL", 1.0, 100.0)]
    m = _run(tmp_path, closes=[100.0, 100.0, 100.0], fills=fills, fee_rate=0.001).metrics
    assert abs(m["total_commission"] - (100.0 * 0.001 * 2)) < 1e-9


# --- win flag consistent with reported realized_pnl -------------------------

def test_win_flag_consistent_with_rounded_pnl(tmp_path):
    # trade1: clear win (+10); trade2: residual that rounds to 0 -> NOT a win.
    fills = [
        _fill(1, "BUY", 1.0, 100.0, commission=0.0),
        _fill(2, "SELL", 1.0, 110.0, commission=0.0),     # +10 -> win
        _fill(3, "BUY", 1.0, 100.0, commission=0.0),
        _fill(4, "SELL", 1.0, 100.0 + 1e-10, commission=0.0),  # ~0 -> not win
    ]
    res = _run(tmp_path, closes=[100.0, 110.0, 100.0, 100.0], fills=fills)
    trades = res.trades
    assert len(trades) == 2
    for t in trades:
        assert t.win == (round(t.realized_pnl, 8) > 0)   # flag matches column
    assert trades[0].win is True
    assert trades[1].win is False                         # residual win removed
    assert res.metrics["win_rate"] == round(1 / 2, 6)


def test_metrics_json_backward_compatible(tmp_path):
    fills = [_fill(1, "BUY", 1.0, 100.0, commission=0.5),
             _fill(2, "SELL", 1.0, 110.0, commission=0.5)]
    res = _run(tmp_path, closes=[100.0, 110.0, 110.0], fills=fills)
    m = json.load(open(res.files["metrics"], encoding="utf-8"))
    # old fields still present
    for k in ("realized_pnl", "unrealized_pnl", "final_equity", "win_rate",
              "trade_count", "fill_count"):
        assert k in m
    # new fields present
    for k in ("total_commission", "gross_realized_pnl", "net_realized_pnl",
              "net_pnl", "gross_win_rate", "win_rate_basis"):
        assert k in m
