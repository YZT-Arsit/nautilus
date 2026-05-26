from __future__ import annotations

from dataclasses import dataclass
from itertools import islice

from nautilus_ext.data.events import bar_event_to_bar_input
from nautilus_ext.pipelines.batch_feature_pipeline import FeatureRecord


@dataclass(frozen=True)
class WarmupSummary:
    processed_events: int
    emitted_bars: int
    first_bar_time: object | None
    last_bar_time: object | None
    latest_snapshot: object | None
    feature_state: dict | None
    aggregator_state: dict | None
    records: list[FeatureRecord] | None = None


class FeatureWarmupPipeline:
    """Initialize feature state from history without producing trading signals."""

    def __init__(self, event_source, bar_aggregator, feature_engine) -> None:
        self.event_source = event_source
        self.bar_aggregator = bar_aggregator
        self.feature_engine = feature_engine

    def run(
        self,
        max_events: int | None = None,
        emit_records: bool = False,
        flush_final_bar: bool = True,
    ) -> WarmupSummary:
        if max_events is not None and max_events < 1:
            raise ValueError("max_events must be >= 1 when provided.")
        records: list[FeatureRecord] = []
        processed_events = 0
        events = self.event_source.iter_events()
        if max_events is not None:
            events = islice(events, max_events)
        for event in events:
            processed_events += 1
            bar = self.bar_aggregator.update(event)
            if bar is not None:
                records.append(self._update_features(bar))
        if flush_final_bar:
            final_bar = self.bar_aggregator.flush()
            if final_bar is not None:
                records.append(self._update_features(final_bar))
        return WarmupSummary(
            processed_events=processed_events,
            emitted_bars=len(records),
            first_bar_time=records[0].ts_event if records else None,
            last_bar_time=records[-1].ts_event if records else None,
            latest_snapshot=records[-1].snapshot if records else None,
            feature_state=(
                self.feature_engine.state_dict()
                if hasattr(self.feature_engine, "state_dict")
                else None
            ),
            aggregator_state=(
                self.bar_aggregator.state_dict()
                if hasattr(self.bar_aggregator, "state_dict")
                else None
            ),
            records=records if emit_records else None,
        )

    def _update_features(self, bar) -> FeatureRecord:
        snapshot = self.feature_engine.update(bar_event_to_bar_input(bar))
        if hasattr(self.feature_engine, "set_last_ts_event"):
            self.feature_engine.set_last_ts_event(bar.ts_event)
        return FeatureRecord(
            instrument_id=bar.instrument_id,
            ts_event=bar.ts_event,
            bar=bar,
            snapshot=snapshot,
        )
