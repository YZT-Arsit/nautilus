"""Minimal contract checks for the Turtle complex-state migration prototype."""
from __future__ import annotations

from dataclasses import dataclass

from data_engine.events import BarEvent, FundingRateEvent
from strategies.turtle_trader.config import TurtleTraderConfig
from strategies.turtle_trader.execution_adapter import TurtleTraderExecutionAdapter
from strategies.turtle_trader.strategy import (
    _CLOSE,
    _HIGH,
    _LOW,
    _OPEN,
    TurtleTraderStrategy,
)
from strategy_framework.backends.nautilus_backtest import NautilusBacktestBackend
from strategy_framework.execution.backtest_report import write_backtest_report
from strategy_framework.execution.intents import PlannedSignal, TradeAction
from strategy_framework.execution.reports import ExecutionReport, FillRecord


@dataclass
class _Snapshot:
    open: float
    high: float
    low: float
    close: float

    def value(self, name: str):
        return {
            _OPEN: self.open,
            _HIGH: self.high,
            _LOW: self.low,
            _CLOSE: self.close,
        }.get(name)


def _cfg() -> TurtleTraderConfig:
    return TurtleTraderConfig(
        breakout_len=5,
        failsafe_len=50,
        trailing_exit_len=1000,
        atr_length=5,
        n_entries=3,
        last_profitable_trade_filter=False,
        account_equity=1_000.0,
        risk_ratio=100.0,
        max_units_per_entry=2,
        min_point=0.0,
    )


def _path() -> list[_Snapshot]:
    bars = [_Snapshot(100.0, 101.0, 99.0, 100.0) for _ in range(12)]
    bars.append(_Snapshot(102.0, 110.0, 102.0, 109.0))
    bars.append(_Snapshot(112.0, 120.0, 111.0, 119.0))
    bars.append(_Snapshot(108.0, 109.0, 80.0, 90.0))
    return bars


def _report(fills: list[FillRecord]) -> ExecutionReport:
    return ExecutionReport("test", len(fills), len(fills), fills, [], 0.0, 0.0)


def test_signal_parity_with_immediate_fill_feedback():
    cfg = _cfg()
    before = TurtleTraderStrategy(cfg)
    after = TurtleTraderExecutionAdapter(cfg)
    fills: list[FillRecord] = []
    before_rows = []
    after_rows = []

    for ts, snapshot in enumerate(_path(), start=1):
        old = before.on_snapshot(snapshot)
        new = after.on_snapshot(snapshot)
        before_rows.append((str(old), [(a.side, a.quantity, a.reason, a.close_all, a.fill_price) for a in old.actions]))
        after_rows.append((str(new), [(a.side, a.quantity, a.reason, a.close_all, a.fill_price) for a in new.actions]))
        for action in new.actions:
            fills.append(FillRecord(
                cfg.instrument_id,
                action.side,
                action.quantity if action.quantity > 0 else abs(after.execution_state.filled_quantity),
                float(action.fill_price if action.fill_price is not None else snapshot.open),
                ts,
                metadata={"reason": action.reason},
            ))
        after.on_execution_report(_report(fills))

    assert after_rows == before_rows


def test_pyramid_is_pending_until_fill_and_reconciles_fill_anchor():
    cfg = _cfg()
    adapter = TurtleTraderExecutionAdapter(cfg)
    entry = PlannedSignal("BUY", (TradeAction("BUY", 2.0, "turtle_long_breakout", fill_price=100.0),))
    adapter.execution_state.observe_actions(entry.actions, decision_position=1)

    assert adapter.position == 0
    assert adapter.execution_state.pending_add_quantity == 2.0

    fills = [FillRecord(cfg.instrument_id, "BUY", 2.0, 101.0, 2)]
    adapter.on_execution_report(_report(fills))
    assert adapter.position == 1
    assert adapter.execution_state.confirmed_entries == 1
    assert adapter.execution_state.last_increase_fill_price == 101.0
    assert adapter._signals._engine.pre_entry_price == 101.0

    add = PlannedSignal("BUY", (TradeAction("BUY", 2.0, "turtle_long_add", fill_price=102.0),))
    adapter.execution_state.observe_actions(add.actions, decision_position=1)
    assert adapter.execution_state.filled_quantity == 2.0
    fills.append(FillRecord(cfg.instrument_id, "BUY", 2.0, 103.0, 3))
    adapter.on_execution_report(_report(fills))
    assert adapter.execution_state.filled_quantity == 4.0
    assert adapter.execution_state.confirmed_entries == 2
    assert adapter.execution_state.last_increase_fill_price == 103.0
    assert adapter._signals._engine.pre_entry_price == 103.0


def test_backend_latency_keeps_position_unfilled_until_next_bar():
    cfg = _cfg()
    backend = NautilusBacktestBackend([], {
        "mode": "simulated",
        "allow_short": False,
        "price_field": "open",
        "fill_timing": "next_bar",
        "latency_bars": 1,
    })
    adapter = TurtleTraderExecutionAdapter(cfg)
    signal = PlannedSignal("BUY", (TradeAction("BUY", 2.0, "turtle_long_breakout", fill_price=100.0),))
    first = BarEvent(
        close=100.0, open=100.0, high=101.0, low=99.0, volume=1.0,
        instrument_id=cfg.instrument_id, event_time_ns=1,
    )
    backend.on_signal(first, _Snapshot(100.0, 101.0, 99.0, 100.0), signal)
    adapter.on_execution_report(backend.report())
    assert adapter.position == 0

    second = BarEvent(
        close=101.0, open=101.0, high=102.0, low=100.0, volume=1.0,
        instrument_id=cfg.instrument_id, event_time_ns=2,
    )
    backend.on_signal(second, _Snapshot(101.0, 102.0, 100.0, 101.0), "HOLD")
    adapter.on_execution_report(backend.report())
    assert adapter.position == 1
    assert backend.report().fills[0].price == 101.0
    assert backend.report().fills[0].event_time_ns == 2


def test_existing_accounting_applies_fee_and_funding(tmp_path):
    iid = "BTCUSDT-PERP.BINANCE"
    result = write_backtest_report(
        output_dir=tmp_path,
        run_name="turtle_execution_contract",
        mode="simulated",
        backend="nautilus_backtest",
        initial_cash=1_000.0,
        bars=[
            {"event_time_ns": 1, "instrument_id": iid, "open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 1.0},
            {"event_time_ns": 2, "instrument_id": iid, "open": 101.0, "high": 101.0, "low": 101.0, "close": 101.0, "volume": 1.0},
            {"event_time_ns": 3, "instrument_id": iid, "open": 110.0, "high": 110.0, "low": 110.0, "close": 110.0, "volume": 1.0},
        ],
        signals=[],
        intents=[],
        fills=[
            FillRecord(iid, "BUY", 1.0, 101.0, 2),
            FillRecord(iid, "SELL", 1.0, 110.0, 3),
        ],
        fee_rate=0.001,
        fill_timing="next_bar",
        execution_stats={"latency_bars": 1},
        funding_events=[FundingRateEvent(2, iid, 0.001, mark_price=101.0)],
    )
    assert result.metrics["total_commission"] == 0.211
    assert result.metrics["funding_pnl"] == -0.101
    assert result.metrics["latency_bars"] == 1
    assert result.metrics["net_pnl"] == 8.688
