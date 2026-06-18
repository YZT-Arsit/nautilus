"""Tests for optional next-bar execution timing (offline, no Nautilus).

The execution-timing shift lives entirely in the backtest backend
(``strategy_framework/backends/nautilus_backtest.py``); the strategy never sees
it. We drive ``NautilusBacktestBackend.on_signal`` (which populates the native
execution map WITHOUT invoking the engine) and assert on ``_execution_intents``
and the pure ``_shift_intents_to_next_bar`` helper. The report-layer plumbing is
covered via ``write_backtest_report`` directly. We never call the native engine
(``close()`` in native mode), so these run without ``nautilus_trader``.
"""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from strategy_framework.backends.nautilus_backtest import (
    NautilusBacktestBackend,
    _shift_intents_to_next_bar,
)
from strategy_framework.execution.backtest_report import write_backtest_report
from strategy_framework.execution.reports import FillRecord


class _Snap:
    def __init__(self, values=None):
        self._v = values or {}

    def value(self, name, default=None):
        return self._v.get(name, default)


class _Bar:
    def __init__(self, ts, close=100.0, instrument_id="BTCUSDT.BINANCE"):
        self.event_time_ns = ts
        self.instrument_id = instrument_id
        self.open = self.high = self.low = self.close = close
        self.volume = 1.0


def _native_backend(fill_timing=None, sell_means="short"):
    cfg = {"backend": "nautilus_backtest", "mode": "nautilus_native",
           "initial_cash": 100000.0, "quantity": 1.0, "sell_means": sell_means,
           "allow_short": True, "price_field": "close", "fee_rate": 0.0005,
           "slippage_bps": 1.0}
    if fill_timing is not None:
        cfg["fill_timing"] = fill_timing
    return NautilusBacktestBackend([], cfg, {"data": {"instrument_id": "BTCUSDT.BINANCE"}})


def _drive(be, seq):
    """Feed (ts, signal) pairs as bars; returns the backend."""
    for ts, sig in seq:
        be.on_signal(_Bar(ts, 100.0 + ts), _Snap(), sig)
    return be


# Signals: HOLD, BUY@1, HOLD, SELL@3, BUY@4 (final bar is actionable).
_SEQ = [(0, "HOLD"), (1, "BUY"), (2, "HOLD"), (3, "SELL"), (4, "BUY")]


# --- 1/2. default + explicit same_bar ---------------------------------------

def test_default_fill_timing_is_same_bar():
    be = _native_backend()  # no fill_timing key
    assert be._fill_timing == "same_bar"
    _drive(be, _SEQ)
    exec_map, stats = be._execution_intents()
    # identity: keys are the signal bars themselves.
    assert set(exec_map) == {1, 3, 4}
    assert stats == {"fill_timing": "same_bar", "original_intent_count": 3,
                     "executed_intent_count": 3, "dropped_tail_intents": 0}


def test_explicit_same_bar_keeps_timestamps_unchanged():
    be = _drive(_native_backend("same_bar"), _SEQ)
    exec_map, _ = be._execution_intents()
    assert exec_map == be._intents_by_ts  # unchanged structure + keys


# --- 3/4/5/6. next_bar shift + tail drop + recorded stats -------------------

def test_next_bar_shifts_intent_timestamps_t_to_t_plus_1():
    be = _drive(_native_backend("next_bar"), _SEQ)
    exec_map, _ = be._execution_intents()
    # signal@1 -> exec@2 (BUY); signal@3 -> exec@4 (SELL); signal@4 -> dropped.
    assert set(exec_map) == {2, 4}
    assert exec_map[2][0] == "BUY"
    assert exec_map[4][0] == "SELL"
    assert 1 not in exec_map and 3 not in exec_map  # nothing executes on the signal bar


def test_next_bar_drops_actionable_signal_on_final_bar():
    be = _drive(_native_backend("next_bar"), _SEQ)
    _, stats = be._execution_intents()
    assert stats["dropped_tail_intents"] == 1  # the BUY on the last bar (ts=4)


def test_next_bar_records_dropped_and_counts():
    be = _drive(_native_backend("next_bar"), _SEQ)
    _, stats = be._execution_intents()
    assert stats["fill_timing"] == "next_bar"
    assert stats["original_intent_count"] == 3
    assert stats["executed_intent_count"] == 2
    assert stats["dropped_tail_intents"] == 1


def test_next_bar_no_drop_when_final_bar_is_hold():
    # Final bar HOLD -> nothing to drop; the prior actionable still shifts.
    seq = [(0, "HOLD"), (1, "BUY"), (2, "SELL"), (3, "HOLD")]
    be = _drive(_native_backend("next_bar"), seq)
    exec_map, stats = be._execution_intents()
    assert set(exec_map) == {2, 3}  # BUY@1->2, SELL@2->3
    assert stats["dropped_tail_intents"] == 0
    assert stats["executed_intent_count"] == 2


# --- 7/8. signal_count + bar_count invariant under fill_timing --------------

def test_signal_and_bar_streams_independent_of_fill_timing():
    same = _drive(_native_backend("same_bar"), _SEQ)
    nxt = _drive(_native_backend("next_bar"), _SEQ)
    # Recorded signals and bars are identical; only execution timing differs.
    assert same._signal_rows == nxt._signal_rows
    assert same._bar_rows == nxt._bar_rows
    assert len(same._bar_rows) == len(nxt._bar_rows) == len(_SEQ)
    sigs_same = [r["signal"] for r in same._signal_rows]
    sigs_nxt = [r["signal"] for r in nxt._signal_rows]
    assert sigs_same == sigs_nxt == ["HOLD", "BUY", "HOLD", "SELL", "BUY"]


# --- 9. invalid value + simulated guard -------------------------------------

def test_invalid_fill_timing_raises_clear_value_error():
    with pytest.raises(ValueError, match="fill_timing"):
        _native_backend("bogus")


def test_next_bar_rejected_for_simulated_mode():
    with pytest.raises(ValueError, match="nautilus_native"):
        NautilusBacktestBackend(
            [], {"backend": "nautilus_backtest", "mode": "simulated",
                 "fill_timing": "next_bar"}, {})


# --- 10. pure helper: multiple intents preserved after shifting --------------

def test_shift_helper_preserves_all_non_tail_intents():
    intents = {10: ("BUY", 1.0), 20: ("SELL", 1.0), 30: ("BUY", 1.0)}
    ordered = [10, 20, 30, 40]
    shifted, dropped = _shift_intents_to_next_bar(intents, ordered)
    assert shifted == {20: ("BUY", 1.0), 30: ("SELL", 1.0), 40: ("BUY", 1.0)}
    assert dropped == 0  # all three have a next bar
    assert len(shifted) == len(intents)  # nothing collides / lost


def test_shift_helper_drops_only_final_bar_intent():
    intents = {10: ("BUY", 1.0), 20: ("SELL", 1.0), 30: ("BUY", 1.0)}
    ordered = [10, 20, 30]  # 30 is the last bar -> its intent drops
    shifted, dropped = _shift_intents_to_next_bar(intents, ordered)
    assert shifted == {20: ("BUY", 1.0), 30: ("SELL", 1.0)}
    assert dropped == 1


# --- 11/12. strategy-agnostic: both sell_means semantics shift the same ------

def test_shift_is_strategy_agnostic_short_and_flat():
    # sell_means='short' (VWM / trend_breakout_atr): SELL -> OrderIntent SELL.
    short = _drive(_native_backend("next_bar", sell_means="short"),
                   [(0, "HOLD"), (1, "SELL"), (2, "HOLD")])
    em_s, _ = short._execution_intents()
    assert em_s[2][0] == "SELL"  # short open shifted to next bar
    # sell_means='flat' (ma_crossover): SELL -> PositionIntent FLAT -> "FLAT".
    flat = _drive(_native_backend("next_bar", sell_means="flat"),
                  [(0, "HOLD"), (1, "SELL"), (2, "HOLD")])
    em_f, _ = flat._execution_intents()
    assert em_f[2][0] == "FLAT"  # flatten shifted to next bar


def test_intents_csv_rows_carry_signal_timestamp_not_execution():
    # Schema decision: intents.csv keeps the SIGNAL (generation) timestamp; the
    # execution shift is visible in fills + metrics, not by mutating intent rows.
    be = _drive(_native_backend("next_bar"), _SEQ)
    intent_ts = sorted(r["event_time_ns"] for r in be._intent_rows)
    assert intent_ts == [1, 3, 4]  # generation bars, unshifted


# --- 13/14. fee/slippage untouched ------------------------------------------

def test_fee_and_slippage_untouched_by_fill_timing():
    be = _native_backend("next_bar")
    assert be._fee_rate == 0.0005
    assert be._slippage_bps == 1.0


# --- report-layer plumbing + 16. same_bar legacy unchanged ------------------

def _bars():
    return [{"event_time_ns": i, "instrument_id": "X", "open": p, "high": p,
             "low": p, "close": p, "volume": 1.0}
            for i, p in enumerate([100.0, 110.0, 120.0, 90.0])]


def test_report_default_is_same_bar_and_no_execution_counts(tmp_path: Path):
    fills = [FillRecord("X", "BUY", 1.0, 100.0, 0, "simulated", {}),
             FillRecord("X", "SELL", 1.0, 120.0, 2, "simulated", {})]
    result = write_backtest_report(
        output_dir=tmp_path / "r", run_name="r", mode="simulated",
        backend="nautilus_backtest", initial_cash=1000.0, bars=_bars(),
        signals=[{"event_time_ns": 0, "instrument_id": "X", "signal": "BUY", "close": 100.0}],
        intents=[], fills=fills)
    m = result.metrics
    assert m["fill_timing"] == "same_bar"               # default
    assert "dropped_tail_intents" not in m              # absent without stats
    assert m["final_equity"] == pytest.approx(1020.0)   # legacy value unchanged
    assert m["realized_pnl"] == pytest.approx(20.0)


def test_report_emits_fill_timing_and_execution_stats(tmp_path: Path):
    result = write_backtest_report(
        output_dir=tmp_path / "r", run_name="r", mode="nautilus_native",
        backend="nautilus_backtest", initial_cash=1000.0, bars=_bars(),
        signals=[], intents=[], fills=[],
        fill_timing="next_bar",
        execution_stats={"fill_timing": "next_bar", "original_intent_count": 3,
                         "executed_intent_count": 2, "dropped_tail_intents": 1})
    m = result.metrics
    assert m["fill_timing"] == "next_bar"
    assert m["original_intent_count"] == 3
    assert m["executed_intent_count"] == 2
    assert m["dropped_tail_intents"] == 1


# --- 15. source scan: no network/live/account/order in modified modules -----

def test_modified_modules_have_no_network_or_order_api():
    from strategy_framework.backends import nautilus_backtest as be_mod
    from strategy_framework.execution import backtest_report as rep_mod

    for mod in (be_mod, rep_mod):
        src = inspect.getsource(mod)
        for net in ("import websocket", "import aiohttp", "import requests",
                    "import urllib", "import socket"):
            assert net not in src, f"{mod.__name__}: {net}"
        for order in ("api_key", "apiKey", "place_order", "new_order",
                      "cancel_order", "/api/v3/order", "/sapi/"):
            assert order not in src, f"{mod.__name__}: {order}"
    # nautilus stays a lazy import in the backtest backend (no top-level import).
    for line in inspect.getsource(be_mod).splitlines():
        assert not line.startswith(("import nautilus_trader", "from nautilus_trader"))
