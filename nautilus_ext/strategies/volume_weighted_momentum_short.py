"""Nautilus Strategy migration of TradeBlazer ``VolumeWeightedMomentumSys_S``.

This is a bar-only short strategy. It requires OHLCV ``Bar`` data because the
signal depends on ``open``, ``high``, ``low``, ``close``, and especially
``volume``:

``VWM = EMA(volume * momentum(close, mom_len), avg_len)``.

TradeBlazer's original code checks the current bar ``low`` and enters at
``min(open, trigger_price)``. This Nautilus version uses an event-driven,
order-based implementation: it submits a SELL stop-market order while the setup
is valid, and exits an existing short with a BUY market order after a bull setup.
That is more suitable for live trading and rigorous backtesting, but bar-internal
fills may differ from TradeBlazer's historical bar model.
"""

from __future__ import annotations

from decimal import Decimal

from nautilus_trader.config import StrategyConfig
from nautilus_trader.core.correctness import PyCondition
from nautilus_trader.model.data import Bar
from nautilus_trader.model.data import BarType
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.enums import TimeInForce
from nautilus_trader.model.enums import TriggerType
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.model.objects import Quantity
from nautilus_trader.model.orders import MarketOrder
from nautilus_trader.model.orders import StopMarketOrder
from nautilus_trader.trading.strategy import Strategy

from nautilus_ext.strategies.vwm_short_signals import VwmShortBarInput
from nautilus_ext.strategies.vwm_short_signals import VwmShortSignalConfig
from nautilus_ext.strategies.vwm_short_signals import VolumeWeightedMomentumShortSignalEngine


class VolumeWeightedMomentumShortConfig(StrategyConfig, frozen=True):
    """Configuration for ``VolumeWeightedMomentumShortStrategy``.

    Parameters
    ----------
    instrument_id : InstrumentId
        The instrument traded by the strategy.
    bar_type : BarType
        The OHLCV bar type consumed by the strategy. Non-bar data must be
        aggregated into bars before use.
    trade_size : Decimal
        Position size per short entry.
    mom_len : int, default 5
        Momentum lookback.
    avg_len : int, default 20
        EMA period for volume-weighted momentum.
    atr_len : int, default 5
        ATR lookback.
    atr_pcnt : Decimal, default Decimal("0.5")
        Entry trigger volatility multiplier.
    setup_len : int, default 5
        Number of bars after BearSetup for which the short stop remains valid.
    order_time_in_force : TimeInForce, optional
        Time in force for entry stop and exit market orders.
    emulation_trigger : str, default "NO_TRIGGER"
        Nautilus local emulation trigger for the stop-market entry order.
    order_quantity_precision : int, optional
        Optional explicit order quantity precision.
    close_positions_on_stop : bool, default False
        If true, close open positions when the strategy stops.
    """

    instrument_id: InstrumentId
    bar_type: BarType
    trade_size: Decimal
    mom_len: int = 5
    avg_len: int = 20
    atr_len: int = 5
    atr_pcnt: Decimal = Decimal("0.5")
    setup_len: int = 5
    order_time_in_force: TimeInForce | None = None
    emulation_trigger: str = "NO_TRIGGER"
    order_quantity_precision: int | None = None
    close_positions_on_stop: bool = False
    reduce_only_on_exit: bool = True


class VolumeWeightedMomentumShortStrategy(Strategy):
    """Volume weighted momentum short strategy for OHLCV bars."""

    def __init__(self, config: VolumeWeightedMomentumShortConfig) -> None:
        PyCondition.is_true(config.mom_len > 0, "mom_len must be > 0")
        PyCondition.is_true(config.avg_len > 0, "avg_len must be > 0")
        PyCondition.is_true(config.atr_len > 0, "atr_len must be > 0")
        PyCondition.is_true(config.atr_pcnt >= 0, "atr_pcnt must be >= 0")
        PyCondition.is_true(config.setup_len >= 1, "setup_len must be >= 1")
        PyCondition.is_true(config.trade_size > 0, "trade_size must be > 0")
        super().__init__(config)

        self.instrument: Instrument | None = None
        self.signal_engine = VolumeWeightedMomentumShortSignalEngine(
            VwmShortSignalConfig(
                mom_len=config.mom_len,
                avg_len=config.avg_len,
                atr_len=config.atr_len,
                atr_pcnt=float(config.atr_pcnt),
                setup_len=config.setup_len,
            ),
        )
        self.entry_order: StopMarketOrder | None = None
        self._bars_since_short_entry = 0

    def on_start(self) -> None:
        self.instrument = self.cache.instrument(self.config.instrument_id)
        if self.instrument is None:
            self.log.error(f"Could not find instrument for {self.config.instrument_id}")
            self.stop()
            return

        self.subscribe_bars(self.config.bar_type)

    def on_bar(self, bar: Bar) -> None:
        if bar.bar_type != self.config.bar_type:
            return

        if bar.is_single_price():
            self.log.warning("Bar OHLC is single price; VWM strategy requires OHLCV bars.")
            return

        position = self._current_position()
        if position == -1:
            self._bars_since_short_entry += 1
        else:
            self._bars_since_short_entry = 0

        result = self.signal_engine.update(
            self._to_signal_bar(bar),
            position=position,
            bars_since_entry=self._bars_since_short_entry,
        )

        if result.bear_setup and result.atr is not None and self.portfolio.is_flat(
            self.config.instrument_id,
        ):
            trigger_price = result.se_price - (float(self.config.atr_pcnt) * result.atr)
            self._replace_short_stop(trigger_price)

        if result.cancel_entry and self.entry_order is not None:
            self.cancel_order(self.entry_order)
            self.entry_order = None

        if result.exit_signal:
            self._cover_short()

    def on_stop(self) -> None:
        self.cancel_all_orders(self.config.instrument_id)
        if self.config.close_positions_on_stop:
            self.close_all_positions(
                instrument_id=self.config.instrument_id,
                reduce_only=self.config.reduce_only_on_exit,
            )
        self.unsubscribe_bars(self.config.bar_type)

    def on_reset(self) -> None:
        self.signal_engine.reset()
        self.entry_order = None
        self._bars_since_short_entry = 0

    def _replace_short_stop(self, trigger_price: float) -> None:
        if self.instrument is None:
            self.log.error("No instrument loaded; cannot submit short stop.")
            return

        if self.entry_order is not None:
            self.cancel_order(self.entry_order)

        order = self.order_factory.stop_market(
            instrument_id=self.config.instrument_id,
            order_side=OrderSide.SELL,
            quantity=self._order_qty(),
            trigger_price=self.instrument.make_price(trigger_price),
            trigger_type=TriggerType.DEFAULT,
            time_in_force=self.config.order_time_in_force or TimeInForce.GTC,
            emulation_trigger=TriggerType[self.config.emulation_trigger],
        )
        self.entry_order = order
        self.submit_order(order)

    def _cover_short(self) -> None:
        if self.instrument is None:
            self.log.error("No instrument loaded; cannot cover short.")
            return
        if not self.portfolio.is_net_short(self.config.instrument_id):
            return

        order: MarketOrder = self.order_factory.market(
            instrument_id=self.config.instrument_id,
            order_side=OrderSide.BUY,
            quantity=self._order_qty(),
            time_in_force=self.config.order_time_in_force or TimeInForce.GTC,
            reduce_only=self.config.reduce_only_on_exit,
        )
        self.submit_order(order)

    def _order_qty(self) -> Quantity:
        if self.instrument is None:
            raise RuntimeError("No instrument loaded.")
        if self.config.order_quantity_precision is not None:
            return Quantity(self.config.trade_size, self.config.order_quantity_precision)
        return self.instrument.make_qty(self.config.trade_size)

    def _current_position(self) -> int:
        if self.portfolio.is_net_short(self.config.instrument_id):
            return -1
        if self.portfolio.is_net_long(self.config.instrument_id):
            return 1
        return 0

    @staticmethod
    def _to_signal_bar(bar: Bar) -> VwmShortBarInput:
        return VwmShortBarInput(
            open=float(bar.open),
            high=float(bar.high),
            low=float(bar.low),
            close=float(bar.close),
            volume=float(bar.volume),
        )
