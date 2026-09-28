from __future__ import annotations

import json
from pathlib import Path

import pytest

from data_engine.events import FundingRateEvent
from data_engine.events import QuoteEvent
from data_engine.events import TradeEvent
from strategy_framework.backends.nautilus_maker import NativeMakerHarness
from strategy_framework.paper_trading import AppendOnlyMarketDataRecorder
from strategy_framework.paper_trading import AtomicPaperStateStore
from strategy_framework.paper_trading import CausalBarAggregator
from strategy_framework.paper_trading import ExchangeFilter
from strategy_framework.paper_trading import FirstTickShadowExecutor
from strategy_framework.paper_trading import LiveOrderSubmissionDisabled
from strategy_framework.paper_trading import MakerPaperExecutor
from strategy_framework.paper_trading import MarketDataGapMonitor
from strategy_framework.paper_trading import PaperAccount
from strategy_framework.paper_trading import daily_turnover_summary


NS = 1_000_000_000
IID = "BTCUSDT.BINANCE"


def trade(ts: int, price: float = 100.0, qty: float = 1.0, maker: bool = True, tid: int = 1):
    return TradeEvent(
        event_time_ns=ts, instrument_id=IID, price=price, quantity=qty,
        is_buyer_maker=maker, trade_id=tid, receive_time_ns=ts + 1_000,
    )


def quote(ts: int, bid: float = 100.0, ask: float = 101.0, update: int = 1):
    return QuoteEvent(
        event_time_ns=ts, instrument_id=IID, bid_price=bid, ask_price=ask,
        bid_size=10.0, ask_size=10.0, update_id=update, receive_time_ns=ts + 1_000,
    )


def exchange_filter() -> ExchangeFilter:
    return ExchangeFilter(0.1, 0.001, 0.001, 0.1, 0, "TEST")


def test_live_order_submission_is_hard_disabled() -> None:
    with pytest.raises(LiveOrderSubmissionDisabled):
        LiveOrderSubmissionDisabled.submit_order({"side": "BUY"})


@pytest.mark.parametrize("minutes", [1, 10, 15])
def test_causal_bar_aggregation_uses_only_completed_intervals(minutes: int) -> None:
    builder = CausalBarAggregator(IID, minutes)
    assert builder.on_trade(trade(5 * NS, 100.0)) == []
    assert builder.flush(minutes * 60 * NS - 1) == []
    bars = builder.flush(minutes * 60 * NS)
    assert len(bars) == 1
    assert bars[0].event_time_ns == minutes * 60 * NS
    assert bars[0].close == 100.0


def test_first_tick_does_not_fill_before_decision() -> None:
    account = PaperAccount(100_000, 100_000, 0.0)
    executor = FirstTickShadowExecutor(account, IID)
    executor.on_target(60 * NS, 1.0)
    assert executor.on_trade(trade(60 * NS - 1, tid=1)) is None
    fill = executor.on_trade(trade(60 * NS, tid=2))
    assert fill is not None
    assert fill.event_time_ns == 60 * NS


def test_maker_partial_fill_and_cancel_on_signal_invalidation() -> None:
    account = PaperAccount(100.0, 100.0, 0.0)
    maker = MakerPaperExecutor(
        account, exchange_filter(),
        NativeMakerHarness(fill_probability=1.0, liquidity_consumption=True),
    )
    maker.on_quote(quote(1))
    maker.on_target(2, 1.0)
    old = maker.order
    maker.on_trade(trade(3, price=100.0, qty=0.4, maker=True, tid=10))
    assert 0 < account.position_qty < 1.0
    maker.on_target(4, -1.0)
    assert old is not None
    assert not old.is_open
    assert maker.cancels == 1


def test_zero_fill_is_preserved() -> None:
    account = PaperAccount(100.0, 100.0, 0.0)
    maker = MakerPaperExecutor(
        account, exchange_filter(),
        NativeMakerHarness(fill_probability=0.0, liquidity_consumption=True),
    )
    maker.on_quote(quote(1))
    maker.on_target(2, 1.0)
    maker.on_trade(trade(3, price=100.0, qty=10.0, maker=True, tid=20))
    assert account.position_qty == 0.0
    assert not maker.fills


def test_exchange_rounding_and_min_notional() -> None:
    f = ExchangeFilter(0.1, 0.01, 0.01, 5.0, 0, "TEST")
    assert f.round_price(100.09, "BUY") == 100.0
    assert f.round_price(100.01, "SELL") == 100.1
    assert f.round_quantity(0.049, 100.0) == 0.0
    assert f.round_quantity(0.059, 100.0) == 0.05


def test_fee_funding_and_idempotency() -> None:
    account = PaperAccount(1_000.0, 1_000.0, 0.001)
    first = FirstTickShadowExecutor(account, IID)
    first.on_target(1, 1.0)
    fill = first.on_trade(trade(1, price=100.0, tid=1))
    assert fill is not None
    fees = account.fees
    assert fees == pytest.approx(1.0)
    event = FundingRateEvent(2, IID, 0.001, mark_price=100.0)
    assert account.apply_funding(event, 100.0) == pytest.approx(-1.0)
    assert account.apply_funding(event, 100.0) == 0.0


def test_daily_turnover_includes_zero_complete_days() -> None:
    summary = daily_turnover_summary(
        {"2026-01-01": 2.0, "2026-01-03": 1.0},
        complete_start="2026-01-01", complete_end_exclusive="2026-01-04",
    )
    assert summary["complete_turnover_days"] == 3
    assert summary["mean_daily_turnover_pct"] == pytest.approx(100.0)
    assert summary["total_turnover_raw"] == pytest.approx(3.0)


def test_recorder_replay_bytes_are_deterministic_and_deduplicated(tmp_path: Path) -> None:
    recorder = AppendOnlyMarketDataRecorder(tmp_path / "a", "BINANCE_PRODUCTION_PUBLIC")
    event = trade(1, tid=17)
    assert recorder.append(event)
    assert not recorder.append(event)
    manifest = recorder.manifest()
    assert len(manifest) == 1
    assert manifest[0]["rows"] == 1
    row = json.loads(next((tmp_path / "a").rglob("events.jsonl")).read_text().strip())
    assert row["ts_exchange"] == 1
    assert row["payload"]["receive_time_ns"] == 1001


def test_restart_state_is_atomic_and_fill_not_double_booked(tmp_path: Path) -> None:
    account = PaperAccount(1_000.0, 1_000.0, 0.0)
    first = FirstTickShadowExecutor(account, IID)
    first.on_target(1, 1.0)
    fill = first.on_trade(trade(1, price=100.0, tid=11))
    assert fill is not None
    store = AtomicPaperStateStore(tmp_path / "state.json")
    store.save(account.snapshot())
    restored = PaperAccount(1_000.0, 1_000.0, 0.0)
    restored.restore(store.load())
    before = restored.total_turnover_raw
    assert not restored.apply_fill(fill)
    assert restored.total_turnover_raw == before


def test_gap_monitor_blocks_out_of_order_or_stale_data() -> None:
    monitor = MarketDataGapMonitor(stale_after_ns=10, max_clock_drift_ns=100_000)
    monitor.observe(quote(10, update=1))
    with pytest.raises(RuntimeError):
        monitor.assert_fresh(IID, 21)


def test_replay_determinism_for_shadow_execution() -> None:
    def once() -> tuple:
        account = PaperAccount(100.0, 100.0, 0.0)
        executor = FirstTickShadowExecutor(account, IID)
        executor.on_target(10, 1.0)
        executor.on_trade(trade(10, 100.0, tid=1))
        executor.on_target(20, -1.0)
        executor.on_trade(trade(20, 101.0, tid=2))
        return account.snapshot(), [fill.fill_id for fill in executor.fills]

    assert once() == once()
