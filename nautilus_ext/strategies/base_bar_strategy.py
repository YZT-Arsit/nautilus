from __future__ import annotations

from decimal import Decimal

from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.enums import TimeInForce
from nautilus_trader.model.enums import TriggerType
from nautilus_trader.model.objects import Quantity
from nautilus_trader.trading.strategy import Strategy

from nautilus_ext.strategies.signal_types import BarInput
from nautilus_ext.strategies.signal_types import SignalResult


class BaseBarStrategy(Strategy):
    """Reusable Nautilus glue for OHLCV bar-based signal engines."""

    def __init__(
        self,
        bar_type,
        signal_engine,
        trade_size,
        order_quantity_precision: int | None = None,
    ):
        super().__init__()
        self.bar_type = bar_type
        self.signal_engine = signal_engine
        self.trade_size = trade_size
        self.order_quantity_precision = order_quantity_precision
        self.instrument = None
        self.entry_order = None
        self._entry_trigger_price = None
        self.bar_count = 0
        self._bars_since_short_entry = 0

    def on_start(self):
        self.instrument = self.cache.instrument(self.bar_type.instrument_id)
        if self.instrument is None:
            self.log.error(f"Could not find instrument for {self.bar_type.instrument_id}")
            self.stop()
            return
        self.subscribe_bars(self.bar_type)

    def on_bar(self, bar):
        self.bar_count += 1
        if not self._valid_bar(bar):
            return

        position = self._position()
        result = self.signal_engine.update(
            self._to_signal_input(bar),
            position=position,
            bars_since_entry=self._bars_since_entry(position),
        )
        self.execute_signal(result)

    def on_stop(self):
        self.cancel_all_orders(self.bar_type.instrument_id)
        self.unsubscribe_bars(self.bar_type)

    def _valid_bar(self, bar) -> bool:
        if bar.bar_type != self.bar_type:
            return False
        if bar.is_single_price():
            self.log.warning("Bar strategy requires OHLCV bars; received single-price bar.")
            return False
        return True

    @staticmethod
    def _to_signal_input(bar) -> BarInput:
        return BarInput(
            open=float(bar.open),
            high=float(bar.high),
            low=float(bar.low),
            close=float(bar.close),
            volume=float(bar.volume),
        )

    def _position(self) -> int:
        instrument_id = self.bar_type.instrument_id
        if self.portfolio.is_net_short(instrument_id):
            return -1
        if self.portfolio.is_net_long(instrument_id):
            return 1
        return 0

    def _bars_since_entry(self, position: int) -> int:
        if position == -1:
            self._bars_since_short_entry += 1
        else:
            self._bars_since_short_entry = 0
        return self._bars_since_short_entry

    def execute_signal(self, result: SignalResult) -> None:
        if result.cancel_entry:
            self._cancel_entry_order()

        if (
            result.entry_side == "SELL"
            and result.entry_order_type == "stop_market"
            and result.entry_price is not None
            and self.portfolio.is_flat(self.bar_type.instrument_id)
        ):
            if self._entry_trigger_price != result.entry_price:
                self._replace_short_stop(result.entry_price)

        if result.exit_side == "BUY":
            self._cancel_entry_order()
            self._cover_short()

    def _cancel_entry_order(self) -> None:
        if self.entry_order is None:
            return
        self.cancel_order(self.entry_order)
        self.entry_order = None
        self._entry_trigger_price = None

    def _replace_short_stop(self, trigger_price: float) -> None:
        if self.instrument is None:
            self.log.error("No instrument loaded; cannot submit short stop.")
            return
        if self.entry_order is not None:
            self._cancel_entry_order()

        order = self.order_factory.stop_market(
            instrument_id=self.bar_type.instrument_id,
            order_side=OrderSide.SELL,
            quantity=self._order_qty(),
            trigger_price=self.instrument.make_price(trigger_price),
            trigger_type=TriggerType.DEFAULT,
            time_in_force=TimeInForce.GTC,
            emulation_trigger=TriggerType.NO_TRIGGER,
        )
        self.entry_order = order
        self._entry_trigger_price = trigger_price
        self.submit_order(order)

    def _cover_short(self) -> None:
        if not self.portfolio.is_net_short(self.bar_type.instrument_id):
            return
        order = self.order_factory.market(
            instrument_id=self.bar_type.instrument_id,
            order_side=OrderSide.BUY,
            quantity=self._order_qty(),
            time_in_force=TimeInForce.GTC,
            reduce_only=True,
        )
        self.submit_order(order)

    def _order_qty(self):
        if self.instrument is None:
            raise RuntimeError("No instrument loaded.")
        trade_size = Decimal(str(self.trade_size))
        if self.order_quantity_precision is not None:
            return Quantity(trade_size, int(self.order_quantity_precision))
        return self.instrument.make_qty(trade_size)
