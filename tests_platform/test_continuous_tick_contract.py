from __future__ import annotations

from data_engine.events import TradeEvent
from feature_engine.api import SpecFeatureEngine, trade_price_mean_spec
from strategy_framework.backends.nautilus_simulation import IntentFillSimulator
from strategy_framework.execution.duration_lag import DurationLagTargetAdapter
from strategies.continuous_tick_ma.strategy import BUY, SELL, crossover_signal


def _trade(ts_ns: int, price: float) -> TradeEvent:
    return TradeEvent(
        event_time_ns=ts_ns,
        instrument_id="BTCUSDT.BINANCE",
        price=price,
        quantity=1.0,
    )


def test_trade_price_mean_uses_event_time_and_full_window() -> None:
    engine = SpecFeatureEngine(
        [trade_price_mean_spec("mean_1m", window=1)],
        stamp_process_time=False,
        is_live=False,
    )
    assert engine.on_event(_trade(0, 1.0)).value("mean_1m") is None
    assert engine.on_event(_trade(30_000_000_000, 3.0)).value("mean_1m") is None
    snapshot = engine.on_event(_trade(60_000_000_000, 5.0))
    # Window semantics are (t-window, t]: the event exactly at the cutoff is evicted.
    assert snapshot.value("mean_1m") == 4.0
    assert snapshot.is_ready("mean_1m")


def test_continuous_signal_rule_is_pure_crossover() -> None:
    assert crossover_signal(10.0, 10.0, 11.0, 10.0) == BUY
    assert crossover_signal(11.0, 10.0, 9.0, 10.0) == SELL
    assert crossover_signal(9.0, 10.0, 9.5, 10.0) == "HOLD"


def test_duration_lag_is_time_based_fill_synchronised_and_reversible() -> None:
    simulator = IntentFillSimulator(default_price_field="price", allow_short=True)
    adapter = DurationLagTargetAdapter(lag_ns=60_000_000_000, notional=100_000)
    signal_event = _trade(0, 100.0)
    adapter.schedule(signal_event, BUY)

    assert adapter.on_market_event(_trade(59_000_000_000, 100.0), simulator.on_intent) == []
    attempts = adapter.on_market_event(_trade(61_000_000_000, 100.0), simulator.on_intent)
    assert len(attempts) == 1
    assert attempts[0].observed_lag_ns == 61_000_000_000
    assert attempts[0].fill is not None
    assert adapter.position_qty == 1_000.0

    adapter.schedule(_trade(70_000_000_000, 100.0), SELL)
    attempts = adapter.on_market_event(_trade(131_000_000_000, 50.0), simulator.on_intent)
    assert attempts[0].intent is not None
    assert attempts[0].intent.side == "SELL"
    assert attempts[0].intent.quantity == 3_000.0
    assert adapter.position_qty == -2_000.0

    reversed_adapter = DurationLagTargetAdapter(
        lag_ns=0, notional=100_000, reverse=True,
    )
    reversed_simulator = IntentFillSimulator(default_price_field="price", allow_short=True)
    reversed_adapter.on_market_event(_trade(0, 100.0), reversed_simulator.on_intent)
    reversed_adapter.schedule(_trade(0, 100.0), BUY)
    attempts = reversed_adapter.on_market_event(_trade(1, 100.0), reversed_simulator.on_intent)
    assert attempts[0].intent is not None
    assert attempts[0].intent.side == "SELL"
    assert reversed_adapter.position_qty == -1_000.0
