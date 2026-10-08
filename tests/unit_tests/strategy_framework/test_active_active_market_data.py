from __future__ import annotations

from data_engine.events import QuoteEvent, TradeEvent
from strategy_framework.paper_trading.active_active import CanonicalMarketDataMerger, canonical_health


class Clock:
    def __init__(self) -> None: self.value = 0.0
    def __call__(self) -> float: return self.value


def trade(trade_id: int, ts: int = 1_000) -> TradeEvent:
    return TradeEvent(ts, "BTCUSDT-PERP.BINANCE", 100.0, 1.0, trade_id=trade_id)


def quote(update_id: int, ts: int = 1_000) -> QuoteEvent:
    return QuoteEvent(ts, "BTCUSDT-PERP.BINANCE", 99.0, 101.0, 2.0, 3.0, update_id=update_id)


def test_overlapping_routes_emit_trade_once() -> None:
    clock = Clock()
    merger = CanonicalMarketDataMerger(reorder_delay_ms=100, clock=clock)
    merger.push("ROUTE_A", trade(7), 10)
    merger.push("ROUTE_B", trade(7), 20)
    clock.value = 0.2
    rows = merger.release_ready()
    assert len(rows) == 1
    assert rows[0].routes == {"ROUTE_A", "ROUTE_B"}
    assert merger.duplicate_count == 1


def test_one_silent_route_does_not_block_canonical_stream() -> None:
    clock = Clock()
    merger = CanonicalMarketDataMerger(reorder_delay_ms=100, clock=clock)
    merger.push("ROUTE_B", trade(8), 20)
    clock.value = 0.2
    assert [row.event.trade_id for row in merger.release_ready()] == [8]


def test_latency_does_not_replay_late_duplicate() -> None:
    clock = Clock()
    merger = CanonicalMarketDataMerger(reorder_delay_ms=100, clock=clock)
    merger.push("ROUTE_A", trade(9), 10)
    clock.value = 0.2
    assert len(merger.release_ready()) == 1
    merger.push("ROUTE_B", trade(9), 500)
    assert merger.release_ready(force=True) == []
    assert merger.late_duplicate_count == 1


def test_quote_update_id_deduplicates() -> None:
    clock = Clock()
    merger = CanonicalMarketDataMerger(reorder_delay_ms=0, clock=clock)
    merger.push("ROUTE_A", quote(11), 10)
    merger.push("ROUTE_B", quote(11), 20)
    assert len(merger.release_ready()) == 1


def test_canonical_health_uses_any_fresh_route() -> None:
    assert canonical_health({"ROUTE_A": "HEALTHY", "ROUTE_B": "HEALTHY"}) == "HEALTHY_REDUNDANT"
    assert canonical_health({"ROUTE_A": "STALE", "ROUTE_B": "HEALTHY"}) == "HEALTHY_DEGRADED"
    assert canonical_health({"ROUTE_A": "RECOVERING", "ROUTE_B": "STALE"}) == "UNAVAILABLE"
