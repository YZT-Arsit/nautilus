from __future__ import annotations

from strategy_framework.backends.nautilus_maker import NativeMakerHarness
from strategy_framework.backends.nautilus_maker import run_native_micro_tests
from scripts.internal.run_l1_maker_pilot import accepted_order_quantity
from scripts.internal.run_l1_maker_policy_study import PolicyRunner
from strategy_framework.execution.maker_policy import MakerLifecyclePolicy

import numpy as np


def test_required_native_maker_micro_probes_are_terminal() -> None:
    results = run_native_micro_tests()
    assert len(results) == 10
    assert not [result for result in results if result.status == "FAILED"]


def test_deterministic_fill_model_seed() -> None:
    def once():
        harness = NativeMakerHarness(fill_probability=0.5, seed=19)
        harness.quote()
        order = harness.limit(side="BUY", price=100.0, quantity=1.0)
        harness.trade(price=100.0, size=1.0, aggressor="SELLER")
        return order.status

    assert once() == once()


def test_historical_l1_quote_and_trade_use_native_events() -> None:
    from nautilus_trader.model.events import OrderFilled

    harness = NativeMakerHarness(fill_probability=1.0, seed=23, liquidity_consumption=True)
    timestamp = 1_709_251_200_006_000_000
    harness.quote(
        bid=61_203.3,
        ask=61_203.4,
        bid_size=2.651,
        ask_size=2.559,
        ts_event=timestamp,
        ts_init=timestamp + 6_000_000,
    )
    order = harness.limit(side="BUY", price=61_203.3, quantity=1.0, post_only=True)
    harness.trade(
        price=61_203.3,
        size=0.4,
        aggressor="SELLER",
        ts=timestamp + 100_000_000,
        trade_id="HISTORICAL-1",
    )
    fills = harness.events(OrderFilled)
    assert str(order.status.name) == "PARTIALLY_FILLED"
    assert len(fills) == 1
    assert int(fills[0].ts_event) == timestamp + 100_000_000
    assert float(str(fills[0].last_qty)) == 0.4


def test_unorderable_sub_increment_quantity_is_explicitly_zero() -> None:
    class Instrument:
        @staticmethod
        def make_qty(value: float):
            if value < 0.01:
                raise ValueError("rounded to zero due to size increment")
            return value

    assert accepted_order_quantity(Instrument(), 0.0049) == 0.0
    assert accepted_order_quantity(Instrument(), 0.02) == 0.02


def test_gtc_invalidated_remainder_cannot_fill_after_cancel() -> None:
    harness = NativeMakerHarness(fill_probability=1.0, seed=31, liquidity_consumption=True)
    runner = PolicyRunner(
        strategy_id="TEST",
        symbol="BTCUSDT",
        probability=1.0,
        harness=harness,
        target=np.array([1.0, -1.0]),
        policy=MakerLifecyclePolicy.GTC_UNTIL_SIGNAL_INVALID,
    )
    quote = (1, 100.0, 10.0, 101.0, 10.0, 1, 1)
    runner.on_decision(1.0, 1, quote)
    old_order = runner.order
    runner.process_trade((1, 100.0, 0.4, 2, True), 1)
    assert runner.state.actual_position == 0.4
    assert runner.fills[-1]["liquidity_side"] == "MAKER"
    runner.on_decision(-1.0, 60_000_000_001, quote)
    assert not old_order.is_open
    before = runner.state.actual_position
    harness.trade(price=100.0, size=10.0, aggressor="SELLER", ts=60_000_000_002)
    assert runner.state.actual_position == before
    assert runner.stale_order_cancellations == 1


def test_requote_orders_remain_passive_and_post_only() -> None:
    harness = NativeMakerHarness(fill_probability=0.0, seed=37, liquidity_consumption=True)
    runner = PolicyRunner(
        strategy_id="TEST",
        symbol="BTCUSDT",
        probability=0.0,
        harness=harness,
        target=np.array([1.0]),
        policy=MakerLifecyclePolicy.PASSIVE_CANCEL_REQUOTE_15S,
    )
    first = (1, 100.0, 10.0, 101.0, 10.0, 1, 1)
    second = (2, 100.5, 10.0, 101.5, 10.0, 15_000_000_001, 15_000_000_001)
    runner.on_decision(1.0, 1, first)
    runner.on_requote(15_000_000_001, second, 1)
    assert runner.requote_count == 1
    assert runner.orders[-1]["limit_price"] == 100.5
    assert runner.orders[-1]["limit_price"] <= runner.orders[-1]["contemporaneous_bid"]
    assert runner.order is not None and runner.order.is_open
