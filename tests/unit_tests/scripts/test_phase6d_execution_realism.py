from __future__ import annotations

from decimal import Decimal

import numpy as np
import pandas as pd

from scripts.internal.run_phase6d_execution_realism import InstrumentRule
from scripts.internal.run_phase6d_execution_realism import order_legality
from scripts.internal.run_phase6d_execution_realism import price_is_legal
from scripts.internal.run_phase6d_execution_realism import round_quantity_toward_zero
from scripts.internal.run_phase6d_execution_realism import simulate_exchange_mechanics


def rule() -> InstrumentRule:
    return InstrumentRule("X", Decimal("0.1"), Decimal("0.01"), Decimal("0.01"), Decimal("100"), Decimal("5"), 1, 2, "TRADING")


def test_quantity_rounding_is_step_legal_and_sign_safe() -> None:
    assert round_quantity_toward_zero(1.239, Decimal("0.01")) == 1.23
    assert round_quantity_toward_zero(-1.239, Decimal("0.01")) == -1.23
    assert abs(round_quantity_toward_zero(-1.239, Decimal("0.01"))) <= 1.239


def test_subminimum_orders_are_rejected_not_rounded_up() -> None:
    assert order_legality(.009, 100.0, rule()) == (False, "ORDER_REJECTED_MIN_QTY")
    assert order_legality(.01, 100.0, rule()) == (False, "ORDER_REJECTED_MIN_NOTIONAL")
    assert order_legality(.05, 100.0, rule()) == (True, "EXECUTABLE")


def test_price_tick_is_validation_only() -> None:
    assert price_is_legal(100.1, .1)
    assert not price_is_legal(100.15, .1)


def test_mechanics_preserves_signal_and_fee_funding_are_separate() -> None:
    times = np.arange(4, dtype=np.int64) * 60_000_000_000
    direction = np.array([1, 1, -1, 0], dtype=float)
    opens = np.array([100, 101, 102, 103], dtype=float)
    closes = np.array([101, 102, 103, 103], dtype=float)
    # Zero mark mirrors the canonical Binance Vision archive and must fall
    # back to the most recent market close rather than zeroing funding.
    funding = pd.DataFrame({"event_time_ns": [times[1]], "mark_price": [0.0], "funding_rate": [.001]})
    frame, metrics, _traces, _exceptions = simulate_exchange_mechanics(
        event_time_ns=times, direction=direction, market_open=opens, close=closes,
        quote_volume=np.full(4, 1_000_000.0), funding=funding, capital=1_000.0,
        rule=rule(),
    )
    assert np.array_equal(frame.desired_direction, direction.astype(np.int8))
    assert metrics["quantity_legality_violations"] == 0
    assert metrics["executed_order_count"] > 0
    assert np.isclose(frame.gross_return.sum(), frame.price_return.sum() + frame.funding_return.sum())
    assert frame.funding_return.sum() != 0.0
    assert np.all(np.abs(frame.executed_quantity.to_numpy() / .01 - np.rint(frame.executed_quantity.to_numpy() / .01)) < 1e-7)


def test_dust_is_not_silently_forced_flat() -> None:
    tiny = InstrumentRule("X", Decimal("0.1"), Decimal("0.01"), Decimal("0.01"), Decimal("100"), Decimal("1000"), 1, 2, "TRADING")
    times = np.arange(2, dtype=np.int64) * 60_000_000_000
    frame, metrics, _traces, _exceptions = simulate_exchange_mechanics(
        event_time_ns=times, direction=np.array([1, 0]), market_open=np.array([200., 100.]),
        close=np.array([200., 100.]), quote_volume=np.full(2, 1e6), funding=pd.DataFrame(),
        capital=1_000., rule=tiny,
    )
    assert frame.executed_quantity.iloc[-1] != 0
    assert metrics["dust_events"] == 1


def test_immediate_execution_is_taker_and_reversal_pays_full_turnover() -> None:
    times = np.arange(2, dtype=np.int64) * 60_000_000_000
    frame, metrics, traces, _ = simulate_exchange_mechanics(
        event_time_ns=times, direction=np.array([1, -1]), market_open=np.array([100., 100.]),
        close=np.array([100., 100.]), quote_volume=np.full(2, 1e6), funding=pd.DataFrame(),
        capital=1_000., rule=rule(), trace_limit=20,
    )
    assert all(item["liquidity_role"] == "TAKER" for item in traces)
    assert frame.turnover.sum() >= 3.0 - 1e-12  # entry 1x + reversal 2x
    assert metrics["reversals"] >= 1


def test_forward_boundary_accepts_pre_cutoff_position() -> None:
    times = np.array([60_000_000_000], dtype=np.int64)
    frame, metrics, _traces, _ = simulate_exchange_mechanics(
        event_time_ns=times,
        direction=np.array([1.0]),
        market_open=np.array([101.0]),
        close=np.array([102.0]),
        quote_volume=np.array([1e6]),
        funding=pd.DataFrame(),
        capital=1_000.0,
        rule=rule(),
        initial_quantity=10.0,
        previous_close_price=100.0,
    )
    assert metrics["price_Return"] > 0.0
    assert frame.executed_quantity.iloc[0] > 0.0
