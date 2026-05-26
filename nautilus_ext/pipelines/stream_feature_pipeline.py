from __future__ import annotations

from itertools import islice

from nautilus_ext.data.events import BarEvent
from nautilus_ext.data.events import bar_event_to_bar_input
from nautilus_ext.pipelines.batch_feature_pipeline import FeatureRecord


class StreamFeaturePipeline:
    """Replay event-by-event without wall-clock sleeping, as a live-stream precursor."""

    def __init__(
        self,
        event_source,
        bar_aggregator,
        feature_engine,
        state_store=None,
        state_key: str | None = None,
        save_every_bars: int | None = None,
    ) -> None:
        if save_every_bars is not None and save_every_bars < 1:
            raise ValueError("save_every_bars must be >= 1 when provided.")
        self.event_source = event_source
        self.bar_aggregator = bar_aggregator
        self.feature_engine = feature_engine
        self.state_store = state_store
        self.state_key = state_key
        self.save_every_bars = save_every_bars
        self.processed_events = 0
        self.emitted_bars = 0
        self.last_state_path = None
        self._last_ts_event = None

    def run(self, max_events: int | None = None) -> list[FeatureRecord]:
        if max_events is not None and max_events < 1:
            raise ValueError("max_events must be >= 1 when provided.")
        records: list[FeatureRecord] = []
        self.processed_events = 0
        self.emitted_bars = 0
        events = self.event_source.iter_events()
        if max_events is not None:
            events = islice(events, max_events)
        for event in events:
            self.processed_events += 1
            bar = self.bar_aggregator.update(event)
            if bar is not None:
                records.append(self._feature_record(bar))
        final_bar = self.bar_aggregator.flush()
        if final_bar is not None:
            records.append(self._feature_record(final_bar))
        if self.state_store is not None:
            self._save_metadata()
        return records

    def _feature_record(self, bar: BarEvent) -> FeatureRecord:
        snapshot = self.feature_engine.update(bar_event_to_bar_input(bar))
        if hasattr(self.feature_engine, "set_last_ts_event"):
            self.feature_engine.set_last_ts_event(bar.ts_event)
        self.emitted_bars += 1
        self._last_ts_event = bar.ts_event
        if (
            self.state_store is not None
            and self.save_every_bars is not None
            and self.emitted_bars % self.save_every_bars == 0
        ):
            self._save_metadata()
        return FeatureRecord(
            instrument_id=bar.instrument_id,
            ts_event=bar.ts_event,
            bar=bar,
            snapshot=snapshot,
        )

    def _save_metadata(self) -> None:
        key = self.state_key or "stream_feature_pipeline"
        state = {
            "pipeline": {
                "processed_events": self.processed_events,
                "emitted_bars": self.emitted_bars,
                "last_ts_event": (
                    self._last_ts_event.isoformat() if self._last_ts_event is not None else None
                ),
                "feature_engine_class": type(self.feature_engine).__name__,
            },
            "feature_state": (
                self.feature_engine.state_dict()
                if hasattr(self.feature_engine, "state_dict")
                else None
            ),
            "aggregator_state": (
                self.bar_aggregator.state_dict()
                if hasattr(self.bar_aggregator, "state_dict")
                else None
            ),
            "note": (
                "Feature state restores native indicators by replaying checkpointed "
                "warmup bars; no private native state is accessed."
            ),
        }
        self.last_state_path = self.state_store.save(
            key,
            state,
        )
