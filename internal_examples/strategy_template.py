from __future__ import annotations
from decimal import Decimal
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.enums import TimeInForce
from nautilus_trader.model.enums import TriggerType
from nautilus_trader.model.objects import Quantity
from nautilus_trader.trading.strategy import Strategy
from nautilus_ext.strategies.vwm_short_signals import VwmShortBarInput
from nautilus_ext.strategies.vwm_short_signals import VwmShortSignalConfig
from nautilus_ext.strategies.vwm_short_signals import VolumeWeightedMomentumShortSignalEngine
class StrategyTemplate(Strategy):
    def __init__(self, bar_type, **params):
        super().__init__()
        self.bar_type = bar_type
        self.params = params
        self.strategy_kind = params.get("strategy_kind", "vwm_short")
        if self.strategy_kind != "vwm_short":
            raise ValueError(
                f"Unsupported strategy_kind={self.strategy_kind!r}. "
                "This template currently implements 'vwm_short'.",
            )
        self.instrument = None
        self.entry_order = None
        self._entry_trigger_price = None
        self.bar_count = 0
        self._bars_since_short_entry = 0
        self.signal_engine = VolumeWeightedMomentumShortSignalEngine(
            VwmShortSignalConfig(
                mom_len=int(params.get("mom_len", 5)),
                avg_len=int(params.get("avg_len", 20)),
                atr_len=int(params.get("atr_len", 5)),
                atr_pcnt=float(params.get("atr_pcnt", 0.5)),
                setup_len=int(params.get("setup_len", 5)),
            ),
        )
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
        self._execute_signal(result)

    def on_stop(self):
        self.cancel_all_orders(self.bar_type.instrument_id)
        self.unsubscribe_bars(self.bar_type)

    def _valid_bar(self, bar) -> bool:
        if bar.bar_type != self.bar_type:
            return False
        if bar.is_single_price():
            self.log.warning("VWM short requires OHLCV bars; received single-price bar.")
            return False
        return True

    @staticmethod
    def _to_signal_input(bar) -> VwmShortBarInput:
        return VwmShortBarInput(
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

    def _execute_signal(self, result) -> None:
        if (
            result.entry_setup_active
            and result.entry_trigger_price is not None
            and self.portfolio.is_flat(self.bar_type.instrument_id)
        ):
            if self._entry_trigger_price != result.entry_trigger_price:
                self._replace_short_stop(result.entry_trigger_price)

        if result.cancel_entry and self.entry_order is not None:
            self._cancel_entry_order()

        if result.exit_signal:
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
        trade_size = Decimal(str(self.params.get("trade_size", 1)))
        qty_precision = self.params.get("order_quantity_precision")
        if qty_precision is not None:
            return Quantity(trade_size, int(qty_precision))
        return self.instrument.make_qty(trade_size)