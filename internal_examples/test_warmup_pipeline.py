from datetime import datetime
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from nautilus_ext.aggregation import TickToBarAggregator
from nautilus_ext.data import QuoteTickEvent
from nautilus_ext.pipelines import FeatureWarmupPipeline


class FakeSource:
    def iter_events(self):
        for timestamp in [
            "2023-01-03T09:30:01+00:00",
            "2023-01-03T09:30:10+00:00",
            "2023-01-03T09:31:01+00:00",
        ]:
            yield QuoteTickEvent(
                instrument_id="IH2303.CFFEX",
                bid_price=10.0,
                ask_price=12.0,
                bid_size=1.0,
                ask_size=1.0,
                ts_event=datetime.fromisoformat(timestamp),
            )


class StatefulCloseFeature:
    def __init__(self):
        self.values = []

    def update(self, bar):
        self.values.append(bar.close)
        return {"close": bar.close}

    def state_dict(self):
        return {"values": list(self.values)}


def test_warmup_updates_features_without_strategy_or_orders():
    engine = StatefulCloseFeature()
    summary = FeatureWarmupPipeline(FakeSource(), TickToBarAggregator(), engine).run(
        emit_records=True,
    )
    assert summary.processed_events == 3
    assert summary.emitted_bars == 2
    assert summary.feature_state == {"values": [11.0, 11.0]}
    assert summary.records is not None
    assert summary.aggregator_state["window"] is None


def test_warmup_can_keep_open_bucket_for_live_continuation():
    summary = FeatureWarmupPipeline(FakeSource(), TickToBarAggregator(), StatefulCloseFeature()).run(
        flush_final_bar=False,
    )
    assert summary.emitted_bars == 1
    assert summary.aggregator_state["window"] is not None
