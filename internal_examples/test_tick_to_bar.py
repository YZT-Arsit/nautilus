from datetime import datetime
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from nautilus_ext.aggregation import BarAggregationConfig
from nautilus_ext.aggregation import TickToBarAggregator
from nautilus_ext.data import QuoteTickEvent


def tick(timestamp: str, bid: float, ask: float) -> QuoteTickEvent:
    return QuoteTickEvent(
        instrument_id="IH2303.CFFEX",
        bid_price=bid,
        ask_price=ask,
        bid_size=1.0,
        ask_size=1.0,
        ts_event=datetime.fromisoformat(timestamp),
    )


def test_tick_to_bar_aggregates_mid_prices_and_tick_volume():
    aggregator = TickToBarAggregator(BarAggregationConfig(interval="1min"))
    assert aggregator.update(tick("2023-01-03T09:30:01+00:00", 10, 12)) is None
    assert aggregator.update(tick("2023-01-03T09:30:25+00:00", 12, 14)) is None
    first = aggregator.update(tick("2023-01-03T09:31:00+00:00", 9, 11))

    assert first is not None
    assert first.open == 11.0
    assert first.high == 13.0
    assert first.low == 11.0
    assert first.close == 13.0
    assert first.volume == 2.0
    assert first.volume_type == "synthetic_tick_count"

    final = aggregator.flush()
    assert final is not None
    assert final.open == 10.0
    assert final.close == 10.0
    assert final.volume == 1.0


def test_tick_to_bar_rejects_out_of_order_ticks():
    aggregator = TickToBarAggregator()
    aggregator.update(tick("2023-01-03T09:31:00+00:00", 10, 12))
    try:
        aggregator.update(tick("2023-01-03T09:30:00+00:00", 10, 12))
    except ValueError as exc:
        assert "ordered" in str(exc)
    else:
        raise AssertionError("Out-of-order QuoteTicks must be rejected.")


def test_tick_to_bar_restores_open_window():
    initial = TickToBarAggregator()
    initial.update(tick("2023-01-03T09:30:01+00:00", 10, 12))
    restored = TickToBarAggregator()
    restored.load_state_dict(initial.state_dict())
    restored.update(tick("2023-01-03T09:30:25+00:00", 12, 14))
    bar = restored.flush()
    assert bar is not None
    assert bar.open == 11.0
    assert bar.close == 13.0
    assert bar.volume == 2.0
