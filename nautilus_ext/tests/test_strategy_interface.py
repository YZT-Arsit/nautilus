from __future__ import annotations

import pandas as pd
import pytest

from nautilus_ext.ccxt_live.paper_live_runner import CcxtPaperLiveRunner
from nautilus_ext.ccxt_live.polling_config import CcxtPollingLiveConfig
from nautilus_ext.ccxt_live.signal_recorder import SignalRecorder
from nautilus_ext.ccxt_live.dry_run_execution import DryRunExecutionRecorder
from nautilus_ext.strategies.interfaces import FeatureVectorInput
from nautilus_ext.strategies.interfaces import FundingRateInput
from nautilus_ext.strategies.interfaces import OrderBookInput
from nautilus_ext.strategies.interfaces import OrderIntent
from nautilus_ext.strategies.interfaces import QuoteTickInput
from nautilus_ext.strategies.interfaces import StrategyInputSchema
from nautilus_ext.strategies.interfaces import StrategySpecV2
from nautilus_ext.strategies.interfaces import TradeTickInput
from nautilus_ext.strategies.registry import available_signal_engines
from nautilus_ext.strategies.registry import build_signal_engine
from nautilus_ext.strategies.registry import register_signal_engine
from nautilus_ext.strategies.signal_types import BarInput
from nautilus_ext.strategies.signal_types import SignalResult


def _row():
    return pd.Series(
        {
            "timestamp_ms": 1_704_067_200_000,
            "datetime": pd.Timestamp("2024-01-01T00:00:00Z"),
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.5,
            "volume": 10.0,
        },
    )


def _config(**kwargs):
    values = {
        "exchange_id": "binance",
        "market_type": "swap",
        "symbol": "BTC/USDT:USDT",
        "timeframe": "1m",
        "venue": "BINANCE",
        "poll_interval_seconds": 0.01,
        "max_bars": 1,
        "output_dir": None,
    }
    values.update(kwargs)
    return CcxtPollingLiveConfig(**values)


class _InstrumentId:
    def __str__(self):
        return "BTCUSDT-PERP.BINANCE"


class _Instrument:
    id = _InstrumentId()


class _Feed:
    _initialized = True

    def initialize(self):
        pass

    def warmup(self):
        return pd.DataFrame(columns=["timestamp_ms", "open", "high", "low", "close", "volume"])

    def poll_once(self):
        if getattr(self, "_used", False):
            return pd.DataFrame(columns=["timestamp_ms", "open", "high", "low", "close", "volume"])
        self._used = True
        return pd.DataFrame([_row()])

    @property
    def instrument(self):
        return _Instrument()

    @property
    def bar_type_str(self):
        return "BTCUSDT-PERP.BINANCE-1-MINUTE-LAST-EXTERNAL"


class _NoopEngine:
    name = "noop_for_tests"
    input_schema = StrategyInputSchema(input_types=["bar"], symbols=[])

    def __init__(self, **params):
        self.params = params
        self.calls = 0

    def reset(self):
        self.calls = 0

    def update(self, event, context=None, position=None, bars_since_entry=None):
        self.calls += 1
        return SignalResult()


def test_legacy_signal_types_imports_still_work():
    bar = BarInput(open=1.0, high=2.0, low=0.5, close=1.5, volume=10.0)
    assert bar.event_type == "bar"
    result = SignalResult(entry_side="SELL", entry_order_type="stop_market", entry_price=1.0)
    assert result.entry_side == "SELL"
    assert result.order_intents[0].side == "SELL"


def test_order_intent_and_non_bar_inputs_are_constructible():
    assert OrderIntent(side="BUY", order_type="market").side == "BUY"
    assert TradeTickInput(price=1.0, size=2.0).event_type == "trade_tick"
    assert QuoteTickInput(bid_price=1.0, ask_price=1.1).event_type == "quote_tick"
    assert OrderBookInput(bids=[(1.0, 2.0)], asks=[(1.1, 3.0)], depth=1).event_type == "orderbook"
    assert FundingRateInput(funding_rate=0.0001).event_type == "funding_rate"
    assert FeatureVectorInput(features={"x": 1}).event_type == "feature_vector"


def test_register_signal_engine_decorator_and_dict_build():
    @register_signal_engine("noop_for_tests")
    class DecoratedNoop(_NoopEngine):
        pass

    engine = build_signal_engine({"name": "noop_for_tests", "params": {"alpha": 1}})
    assert isinstance(engine, DecoratedNoop)
    assert engine.params == {"alpha": 1}
    assert "noop_for_tests" in available_signal_engines()


def test_build_vwm_short_from_dict_when_nautilus_runtime_available():
    try:
        engine = build_signal_engine(
            {
                "name": "vwm_short",
                "input_schema": {"input_types": ["bar"], "symbols": ["BTC/USDT:USDT"]},
                "params": {"mom_len": 5, "avg_len": 20, "atr_len": 5},
                "execution": {"trade_size": 1},
            },
        )
    except ModuleNotFoundError as exc:
        if "nautilus_trader.core.data" in str(exc):
            pytest.skip("Nautilus native module is not built.")
        raise
    assert engine.name == "vwm_short"


def test_paper_live_runner_old_mode_uses_engine_instance():
    engine = _NoopEngine()
    runner = CcxtPaperLiveRunner(_config(), engine, _feed=_Feed())
    summary = runner.run(max_bars=1)
    assert summary["total_bars"] == 1
    assert engine.calls == 1


def test_paper_live_runner_strategy_spec_mode_builds_engine():
    register_signal_engine("noop_spec_for_tests", _NoopEngine)
    runner = CcxtPaperLiveRunner(
        _config(),
        {
            "name": "noop_spec_for_tests",
            "params": {"beta": 2},
            "input_schema": {"input_types": ["bar"], "symbols": ["BTC/USDT:USDT"]},
            "execution": {"trade_size": 1},
        },
        _feed=_Feed(),
    )
    summary = runner.run(max_bars=1)
    assert summary["total_bars"] == 1
    assert runner.signal_engine.params == {"beta": 2}


def test_signal_and_execution_recorders_handle_new_signal_result():
    result = SignalResult(
        signal_name="test_signal",
        order_intents=[
            OrderIntent(side="BUY", order_type="market", quantity=3, reduce_only=True, reason="cover"),
        ],
        debug={"custom": {"nested": True}},
        state={"active": True},
        reason="cover",
    )
    recorder = SignalRecorder("BTCUSDT-PERP.BINANCE", "bar-type")
    recorder.append(_row(), result, position=0)
    df = recorder.to_dataframe()
    assert df.loc[0, "event_type"] == "bar"
    assert df.loc[0, "signal_name"] == "test_signal"
    assert df.loc[0, "order_intents_count"] == 1
    assert "nested" in df.loc[0, "debug_json"]

    dry_run = DryRunExecutionRecorder("BTCUSDT-PERP.BINANCE", trade_size=1)
    dry_run.append(_row(), result)
    orders = dry_run.to_dataframe()
    assert orders.loc[0, "side"] == "BUY"
    assert orders.loc[0, "quantity"] == 3
