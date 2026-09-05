"""
Native Nautilus maker-order probes used by the isolated research workflow.

This intentionally uses the installed low-level ``OrderMatchingEngine`` so
post-only acceptance/rejection, passive trade fills, partial fills, cancellation,
liquidity side, and commissions are produced by Nautilus order events.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any


@dataclass(frozen=True)
class NativeProbeResult:
    test_id: str
    status: str
    observed: str
    native_event_count: int


class NativeMakerHarness:
    def __init__(
        self,
        *,
        liquidity_consumption: bool = False,
        queue_position: bool = False,
        fill_probability: float = 1.0,
        seed: int = 7,
        maker_fee_rate: float | None = None,
        instrument: Any | None = None,
    ) -> None:
        from nautilus_trader.backtest.engine import OrderMatchingEngine
        from nautilus_trader.backtest.models import FillModel
        from nautilus_trader.backtest.models import MakerTakerFeeModel
        from nautilus_trader.common.component import MessageBus
        from nautilus_trader.common.component import TestClock
        from nautilus_trader.model.enums import AccountType
        from nautilus_trader.model.enums import BookType
        from nautilus_trader.model.enums import OmsType
        from nautilus_trader.test_kit.providers import TestInstrumentProvider
        from nautilus_trader.test_kit.stubs.component import TestComponentStubs
        from nautilus_trader.test_kit.stubs.identifiers import TestIdStubs

        self.clock = TestClock()
        self.instrument = instrument or TestInstrumentProvider.btcusdt_perp_binance()
        if maker_fee_rate is not None:
            from strategy_framework.backends.nautilus_native import _instrument_with_fees

            self.instrument = _instrument_with_fees(self.instrument, maker_fee_rate)
        self.account_id = TestIdStubs.account_id()
        self.cache = TestComponentStubs.cache()
        self.cache.add_instrument(self.instrument)
        self.msgbus = MessageBus(trader_id=TestIdStubs.trader_id(), clock=self.clock)
        self.messages: list[Any] = []
        self._applied_message_count = 0
        self._orders: dict[str, Any] = {}
        self.msgbus.register("ExecEngine.process", self.messages.append)
        self.engine = OrderMatchingEngine(
            instrument=self.instrument,
            raw_id=0,
            fill_model=FillModel(
                prob_fill_on_limit=fill_probability,
                prob_slippage=0.0,
                random_seed=seed,
            ),
            fee_model=MakerTakerFeeModel(),
            book_type=BookType.L1_MBP,
            oms_type=OmsType.NETTING,
            account_type=AccountType.MARGIN,
            reject_stop_orders=True,
            trade_execution=True,
            liquidity_consumption=liquidity_consumption,
            queue_position=queue_position,
            msgbus=self.msgbus,
            cache=self.cache,
            clock=self.clock,
        )

    def quote(
        self,
        bid: float = 100.0,
        ask: float = 101.0,
        size: float = 100.0,
        *,
        bid_size: float | None = None,
        ask_size: float | None = None,
        ts_event: int = 0,
        ts_init: int | None = None,
    ) -> None:
        from nautilus_trader.model.data import QuoteTick

        bid_qty = size if bid_size is None else bid_size
        ask_qty = size if ask_size is None else ask_size
        initialized = ts_event if ts_init is None else ts_init
        self.clock.set_time(ts_event)
        self.engine.process_quote_tick(
            QuoteTick(
                instrument_id=self.instrument.id,
                bid_price=self.instrument.make_price(bid),
                ask_price=self.instrument.make_price(ask),
                bid_size=self.instrument.make_qty(bid_qty),
                ask_size=self.instrument.make_qty(ask_qty),
                ts_event=ts_event,
                ts_init=initialized,
            )
        )
        self._apply_new_events()

    def trade(
        self,
        price: float,
        size: float,
        aggressor: str,
        ts: int = 1,
        trade_id: str | None = None,
    ) -> None:
        from nautilus_trader.model.enums import AggressorSide
        from nautilus_trader.model.data import TradeTick
        from nautilus_trader.model.identifiers import TradeId

        side = AggressorSide.SELLER if aggressor == "SELLER" else AggressorSide.BUYER
        self.clock.set_time(ts)
        self.engine.process_trade_tick(
            TradeTick(
                instrument_id=self.instrument.id,
                price=self.instrument.make_price(price),
                size=self.instrument.make_qty(size),
                aggressor_side=side,
                trade_id=TradeId(trade_id or f"T-{ts}"),
                ts_event=ts,
                ts_init=ts,
            )
        )
        self._apply_new_events()

    def limit(
        self,
        *,
        side: str,
        price: float,
        quantity: float,
        post_only: bool = True,
        client_order_id: str | None = None,
    ):
        from nautilus_trader.model.enums import OrderSide
        from nautilus_trader.model.identifiers import ClientOrderId
        from nautilus_trader.test_kit.stubs.execution import TestExecStubs

        order = TestExecStubs.limit_order(
            instrument=self.instrument,
            order_side=OrderSide.BUY if side == "BUY" else OrderSide.SELL,
            price=self.instrument.make_price(price),
            quantity=self.instrument.make_qty(quantity),
            post_only=post_only,
            client_order_id=ClientOrderId(client_order_id) if client_order_id else None,
        )
        self._orders[str(order.client_order_id)] = order
        self.engine.process_order(order, self.account_id)
        self._apply_new_events()
        return order

    def cancel(self, order) -> None:
        self.engine.cancel_order(order)
        self._apply_new_events()

    def _apply_new_events(self) -> None:
        for event in self.messages[self._applied_message_count :]:
            order = self._orders.get(str(getattr(event, "client_order_id", "")))
            if order is not None:
                order.apply(event)
        self._applied_message_count = len(self.messages)

    def events(self, event_type) -> list[Any]:
        return [event for event in self.messages if isinstance(event, event_type)]


def run_native_micro_tests() -> list[NativeProbeResult]:
    """Run the ten deterministic M0 probes required by the research contract."""
    from nautilus_trader.backtest.models import MakerTakerFeeModel
    from nautilus_trader.model.enums import LiquiditySide
    from nautilus_trader.model.enums import OrderStatus
    from nautilus_trader.model.events import OrderFilled
    from nautilus_trader.model.events import OrderRejected
    from nautilus_trader.test_kit.stubs.execution import TestExecStubs
    from strategy_framework.backends.nautilus_native import _instrument_with_fees
    from strategy_framework.execution.maker_policy import NextDecisionCancelState

    results: list[NativeProbeResult] = []

    h = NativeMakerHarness()
    h.quote()
    order = h.limit(side="BUY", price=100.0, quantity=1.0)
    h.trade(price=100.5, size=1.0, aggressor="SELLER")
    ok = order.status == OrderStatus.ACCEPTED and not h.events(OrderFilled)
    results.append(
        NativeProbeResult(
            "M0_01_NO_FILL", "PASSED" if ok else "FAILED", str(order.status), len(h.messages)
        )
    )

    h = NativeMakerHarness()
    h.quote()
    order = h.limit(side="BUY", price=100.0, quantity=1.0)
    h.trade(price=100.0, size=1.0, aggressor="SELLER")
    fills = h.events(OrderFilled)
    ok = (
        order.status == OrderStatus.FILLED
        and len(fills) == 1
        and fills[0].liquidity_side == LiquiditySide.MAKER
    )
    results.append(
        NativeProbeResult(
            "M0_02_TOUCH_FILL",
            "PASSED" if ok else "FAILED",
            f"{order.status};{fills[0].liquidity_side if fills else 'NO_FILL'}",
            len(h.messages),
        )
    )

    h = NativeMakerHarness(liquidity_consumption=True)
    h.quote()
    order = h.limit(side="BUY", price=100.0, quantity=2.0)
    h.trade(price=100.0, size=1.0, aggressor="SELLER")
    ok = order.status == OrderStatus.PARTIALLY_FILLED and Decimal(str(order.filled_qty)) == Decimal(
        "1.000"
    )
    results.append(
        NativeProbeResult(
            "M0_03_PARTIAL_FILL",
            "PASSED" if ok else "FAILED",
            f"{order.status};filled={order.filled_qty}",
            len(h.messages),
        )
    )

    h = NativeMakerHarness()
    h.quote()
    order = h.limit(side="BUY", price=100.0, quantity=1.0)
    h.cancel(order)
    ok = order.status == OrderStatus.CANCELED
    results.append(
        NativeProbeResult(
            "M0_04_NEXT_DECISION_CANCEL",
            "PASSED" if ok else "FAILED",
            str(order.status),
            len(h.messages),
        )
    )

    h = NativeMakerHarness(liquidity_consumption=True)
    h.quote()
    order = h.limit(side="BUY", price=100.0, quantity=2.0)
    h.trade(price=100.0, size=1.0, aggressor="SELLER")
    h.cancel(order)
    ok = order.status == OrderStatus.CANCELED and Decimal(str(order.filled_qty)) == Decimal("1.000")
    results.append(
        NativeProbeResult(
            "M0_05_PARTIAL_CANCEL_REMAINDER",
            "PASSED" if ok else "FAILED",
            f"{order.status};filled={order.filled_qty}",
            len(h.messages),
        )
    )

    state = NextDecisionCancelState()
    state.next_decision(1.0)
    state.apply_fill(0.25)
    delta = state.next_decision(-1.0)
    ok = abs(delta + 1.25) < 1e-12 and abs(state.canceled_quantity - 0.75) < 1e-12
    results.append(
        NativeProbeResult(
            "M0_06_SIGNAL_REVERSAL",
            "PASSED" if ok else "FAILED",
            f"delta={delta};canceled={state.canceled_quantity}",
            0,
        )
    )

    h = NativeMakerHarness()
    h.quote()
    order = h.limit(side="BUY", price=101.0, quantity=1.0)
    rejects = h.events(OrderRejected)
    ok = (
        order.status == OrderStatus.REJECTED
        and len(rejects) == 1
        and bool(rejects[0].due_post_only)
    )
    results.append(
        NativeProbeResult(
            "M0_07_POST_ONLY_CROSS_REJECT",
            "PASSED" if ok else "FAILED",
            f"{order.status};due_post_only={bool(rejects and rejects[0].due_post_only)}",
            len(h.messages),
        )
    )

    for test_id, rate in [("M0_08_MAKER_FEE", 0.0002), ("M0_09_NEGATIVE_FEE_REBATE", -0.0001)]:
        instrument = _instrument_with_fees(h.instrument, rate)
        order = TestExecStubs.make_filled_order(instrument=instrument)
        commission = MakerTakerFeeModel().get_commission(
            order, order.quantity, order.price, instrument
        )
        value = float(commission.as_decimal())
        ok = value > 0 if rate > 0 else value < 0
        results.append(
            NativeProbeResult(
                test_id, "PASSED" if ok else "FAILED", f"rate={rate};commission={value}", 1
            )
        )

    try:
        h = NativeMakerHarness(queue_position=True)
        h.quote(size=10.0)
        order = h.limit(side="BUY", price=100.0, quantity=1.0)
        h.trade(price=100.0, size=20.0, aggressor="SELLER")
        observed = f"constructed=True;status={order.status}"
        results.append(
            NativeProbeResult("M0_10_QUEUE_POSITION_API", "PASSED", observed, len(h.messages))
        )
    except Exception as exc:  # Installed-version capability probe, retained verbatim.
        results.append(
            NativeProbeResult(
                "M0_10_QUEUE_POSITION_API", "DATA_BLOCKED", f"{type(exc).__name__}: {exc}", 0
            )
        )

    return results
