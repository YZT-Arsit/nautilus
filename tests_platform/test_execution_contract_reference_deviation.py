"""Minimal execution-contract tests for the migrated Reference Deviation long."""
from __future__ import annotations

from dataclasses import dataclass

from data_engine.events import BarEvent, FundingRateEvent
from strategies.reference_deviation_long.config import ReferenceDeviationLongConfig
from strategies.reference_deviation_long.execution_adapter import (
    ReferenceDeviationLongExecutionAdapter,
)
from strategies.reference_deviation_long.strategy import (
    _CLOSE,
    _HIGH,
    _LOW,
    _OPEN,
    _VOLUME,
    ReferenceDeviationLongStrategy,
)
from strategy_framework.backends.nautilus_backtest import NautilusBacktestBackend
from strategy_framework.execution.backtest_report import write_backtest_report
from strategy_framework.execution.legacy_adapter import LegacyExecutionState
from strategy_framework.execution.reports import ExecutionReport, FillRecord


@dataclass
class _Snapshot:
    price: float
    volume: float = 1.0

    def value(self, name: str):
        if name in (_OPEN, _HIGH, _LOW, _CLOSE):
            return self.price
        if name == _VOLUME:
            return self.volume
        return None


def _prices() -> list[float]:
    return (
        [100.0] * 20
        + [100.0 + i for i in range(1, 21)]
        + [120.0 - 2.0 * i for i in range(1, 25)]
    )


def _report(fills: list[FillRecord]) -> ExecutionReport:
    return ExecutionReport(
        backend="test",
        total_intents=len(fills),
        total_fills=len(fills),
        fills=fills,
        positions=[],
        realized_pnl=0.0,
        unrealized_pnl=0.0,
    )


def test_signal_parity_with_immediate_fill_feedback():
    cfg = ReferenceDeviationLongConfig()
    before = ReferenceDeviationLongStrategy(cfg)
    after = ReferenceDeviationLongExecutionAdapter(cfg)
    before_signals: list[str] = []
    after_signals: list[str] = []
    fills: list[FillRecord] = []

    for ts, price in enumerate(_prices(), start=1):
        snapshot = _Snapshot(price)
        before_signal = before.on_snapshot(snapshot)
        after_signal = after.on_snapshot(snapshot)
        before_signals.append(before_signal)
        after_signals.append(after_signal)
        if after_signal in ("BUY", "SELL"):
            fills.append(
                FillRecord(
                    instrument_id=cfg.instrument_id,
                    side=after_signal,
                    quantity=1.0,
                    price=price,
                    event_time_ns=ts,
                )
            )
        after.on_execution_report(_report(fills))

    assert after_signals == before_signals


def test_position_changes_only_after_delayed_fill():
    cfg = ReferenceDeviationLongConfig()
    strategy = ReferenceDeviationLongExecutionAdapter(cfg)
    signal = "HOLD"
    ts = 0
    for ts, price in enumerate(_prices(), start=1):
        signal = strategy.on_snapshot(_Snapshot(price))
        if signal == "BUY":
            break

    assert signal == "BUY"
    assert strategy.position == 0
    assert strategy.decision_position == 1

    backend = NautilusBacktestBackend(
        [],
        {
            "mode": "simulated",
            "quantity": 1.0,
            "allow_short": False,
            "price_field": "open",
            "fill_timing": "next_bar",
            "latency_bars": 1,
        },
    )
    event = BarEvent(100.0, 100.0, 100.0, 100.0, 1.0, cfg.instrument_id, ts)
    backend.on_signal(event, _Snapshot(100.0), "BUY")
    strategy.on_execution_report(backend.report())
    assert strategy.position == 0

    next_event = BarEvent(101.0, 101.0, 101.0, 101.0, 1.0, cfg.instrument_id, ts + 1)
    backend.on_signal(next_event, _Snapshot(101.0), "HOLD")
    strategy.on_execution_report(backend.report())
    assert strategy.position == 1
    assert backend.report().fills[0].price == 101.0
    assert backend.report().fills[0].event_time_ns == ts + 1


def test_shared_state_supports_short_and_tracks_real_fill_prices():
    iid = "BTCUSDT-PERP.BINANCE"
    state = LegacyExecutionState(iid, {"SELL": -1, "BUY": 0})
    state.observe_signal("SELL")
    assert state.pending_target_position == -1
    assert state.position == 0

    state.on_fill(FillRecord(iid, "SELL", 0.001, 100.5, 1))
    assert state.position == -1
    assert state.entry_fill_price == 100.5
    state.observe_signal("BUY")
    assert state.pending_target_position == 0
    assert state.position == -1

    state.on_fill(FillRecord(iid, "BUY", 0.001, 99.5, 2))
    assert state.position == 0
    assert state.exit_fill_price == 99.5


def test_existing_accounting_applies_fee_and_funding(tmp_path):
    iid = "BTCUSDT-PERP.BINANCE"
    fills = [
        FillRecord(iid, "BUY", 1.0, 100.0, 1),
        FillRecord(iid, "SELL", 1.0, 110.0, 3),
    ]
    result = write_backtest_report(
        output_dir=tmp_path,
        run_name="reference_deviation_contract",
        mode="simulated",
        backend="nautilus_backtest",
        initial_cash=1_000.0,
        bars=[
            {"event_time_ns": 1, "instrument_id": iid, "open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 1.0},
            {"event_time_ns": 2, "instrument_id": iid, "open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 1.0},
            {"event_time_ns": 3, "instrument_id": iid, "open": 110.0, "high": 110.0, "low": 110.0, "close": 110.0, "volume": 1.0},
        ],
        signals=[],
        intents=[],
        fills=fills,
        fee_rate=0.001,
        fill_timing="next_bar",
        execution_stats={"latency_bars": 1},
        funding_events=[FundingRateEvent(2, iid, 0.001, mark_price=100.0)],
    )

    assert result.metrics["total_commission"] == 0.21
    assert result.metrics["funding_pnl"] == -0.1
    assert result.metrics["latency_bars"] == 1
    assert result.metrics["net_pnl"] == 9.69
