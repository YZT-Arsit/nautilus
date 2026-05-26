from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from nautilus_ext.data.events import BarEvent
from nautilus_ext.data.events import bar_event_to_bar_input


@dataclass(frozen=True)
class FeatureRecord:
    instrument_id: str
    ts_event: datetime
    bar: BarEvent
    snapshot: object


class BatchFeaturePipeline:
    """Apply the same incremental feature engine across an historical event source."""

    def __init__(self, event_source, bar_aggregator, feature_engine) -> None:
        self.event_source = event_source
        self.bar_aggregator = bar_aggregator
        self.feature_engine = feature_engine
        self.processed_events = 0
        self.emitted_bars = 0

    def run(self) -> list[FeatureRecord]:
        records: list[FeatureRecord] = []
        self.processed_events = 0
        self.emitted_bars = 0
        for event in self.event_source.iter_events():
            self.processed_events += 1
            bar = self.bar_aggregator.update(event)
            if bar is not None:
                records.append(self._feature_record(bar))
        final_bar = self.bar_aggregator.flush()
        if final_bar is not None:
            records.append(self._feature_record(final_bar))
        return records

    def _feature_record(self, bar: BarEvent) -> FeatureRecord:
        snapshot = self.feature_engine.update(bar_event_to_bar_input(bar))
        if hasattr(self.feature_engine, "set_last_ts_event"):
            self.feature_engine.set_last_ts_event(bar.ts_event)
        self.emitted_bars += 1
        return FeatureRecord(
            instrument_id=bar.instrument_id,
            ts_event=bar.ts_event,
            bar=bar,
            snapshot=snapshot,
        )
