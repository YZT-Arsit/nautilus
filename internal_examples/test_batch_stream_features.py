from datetime import datetime
from pathlib import Path
import sys

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from nautilus_ext.aggregation import TickToBarAggregator
from nautilus_ext.data import CatalogQuoteTickSource
from nautilus_ext.data import QuoteTickEvent
from nautilus_ext.pipelines import BatchFeaturePipeline
from nautilus_ext.pipelines import StreamFeaturePipeline
from nautilus_ext.state import JsonFeatureStateStore


class FakeSource:
    def __init__(self, events):
        self.events = events

    def iter_events(self):
        yield from self.events


class CloseFeature:
    def update(self, bar):
        return {"close": bar.close}


def tick(timestamp: str, bid: float, ask: float) -> QuoteTickEvent:
    return QuoteTickEvent(
        instrument_id="IH2303.CFFEX",
        bid_price=bid,
        ask_price=ask,
        bid_size=1.0,
        ask_size=1.0,
        ts_event=datetime.fromisoformat(timestamp),
    )


def fake_events():
    return [
        tick("2023-01-03T09:30:01+00:00", 10, 12),
        tick("2023-01-03T09:30:25+00:00", 12, 14),
        tick("2023-01-03T09:31:00+00:00", 9, 11),
    ]


def test_batch_and_stream_emit_identical_bars(tmp_path):
    batch = BatchFeaturePipeline(FakeSource(fake_events()), TickToBarAggregator(), CloseFeature())
    stream = StreamFeaturePipeline(
        FakeSource(fake_events()),
        TickToBarAggregator(),
        CloseFeature(),
        state_store=JsonFeatureStateStore(str(tmp_path)),
        state_key="../IH2303 replay",
        save_every_bars=1,
    )

    batch_records = batch.run()
    stream_records = stream.run()
    assert [record.bar for record in batch_records] == [record.bar for record in stream_records]
    assert batch.processed_events == stream.processed_events == 3
    assert batch.emitted_bars == stream.emitted_bars == 2
    assert stream.last_state_path is not None
    assert stream.last_state_path.parent == tmp_path
    assert ".." not in stream.last_state_path.name


def test_stream_max_events_limits_incremental_processing():
    stream = StreamFeaturePipeline(FakeSource(fake_events()), TickToBarAggregator(), CloseFeature())
    records = stream.run(max_events=2)
    assert stream.processed_events == 2
    assert len(records) == 1
    assert records[0].bar.volume == 2.0


def test_json_state_store_save_load_and_safe_key(tmp_path):
    store = JsonFeatureStateStore(str(tmp_path))
    path = store.save("../unsafe key", {"emitted_bars": 2})
    assert path.parent == tmp_path
    assert store.exists("../unsafe key")
    assert store.load("../unsafe key") == {"emitted_bars": 2}


def test_catalog_quote_source_decodes_orders_and_limits_rows(tmp_path):
    path = tmp_path / "feed" / "data" / "quote_tick" / "IH2303.CFFEX" / "ticks.parquet"
    path.parent.mkdir(parents=True)
    values = [1_000_000_000, 2_000_000_000]
    frame = pd.DataFrame(
        {
            "bid_price": [value.to_bytes(8, "little", signed=True) for value in values],
            "ask_price": [(value + 200_000_000).to_bytes(8, "little", signed=True) for value in values],
            "bid_size": [(1_000_000_000).to_bytes(8, "little", signed=True)] * 2,
            "ask_size": [(2_000_000_000).to_bytes(8, "little", signed=True)] * 2,
            "ts_event": [2_000_000_000, 1_000_000_000],
            "ts_init": [2_000_000_000, 1_000_000_000],
        },
    )
    frame.to_parquet(path)

    source = CatalogQuoteTickSource(str(tmp_path), "IH2303.CFFEX", limit=1)
    events = list(source.iter_events())
    assert len(events) == 1
    assert events[0].ts_event.timestamp() == 1
    assert events[0].bid_price == 2.0
    assert events[0].bid_size == 1.0


def test_catalog_quote_source_lists_missing_columns(tmp_path):
    path = tmp_path / "data" / "quote_tick" / "IH2303.CFFEX" / "bad.parquet"
    path.parent.mkdir(parents=True)
    pd.DataFrame({"bid_price": [1]}).to_parquet(path)
    try:
        list(CatalogQuoteTickSource(str(tmp_path), "IH2303.CFFEX").iter_events())
    except ValueError as exc:
        assert "missing columns" in str(exc)
        assert "actual columns" in str(exc)
    else:
        raise AssertionError("Missing QuoteTick fields must fail clearly.")
