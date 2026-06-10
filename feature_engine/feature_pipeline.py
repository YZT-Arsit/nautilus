"""
FeaturePipeline — orchestrates multiple FeatureEngines over a MarketEvent stream.

Online path (per bar, hot path):
    feature_events = pipeline.update(bar_input)   # no DataFrame, O(engines)

Offline / batch path:
    feature_events = pipeline.update_many(bars)

Warmup:
    pipeline.warmup(historical_bars)    # marks outputs is_warmup=True

Persistence:
    pipeline.flush()                    # batch-write buffered events to Parquet

Relationship to signal engine:
    After pipeline.update(event), the caller builds a StrategyRuntimeContext
    with the returned FeatureEvents and passes it to the signal engine.
    The VWM engine (Mode A) ignores context and computes features internally;
    future Mode B engines read features from context instead.
"""
from __future__ import annotations

import logging
from dataclasses import replace
from typing import Iterable

from feature_engine.feature_event import FeatureEvent
from feature_engine.feature_store import OfflineFeatureStore, OnlineFeatureStore

log = logging.getLogger(__name__)


class FeaturePipeline:
    """Runs N feature engines over a MarketEvent stream.

    Parameters
    ----------
    feature_engines : list
        Engines implementing the ``BaseFeatureEngine`` protocol.  Each engine
        handles the event types it supports and returns None for others.
    online_store : OnlineFeatureStore | None
        If provided, every FeatureEvent is pushed here immediately after
        generation (on the hot path, no file I/O).
    offline_store : OfflineFeatureStore | None
        If provided, FeatureEvents are buffered here; call flush() to persist.
    """

    def __init__(
        self,
        feature_engines: list,
        online_store: OnlineFeatureStore | None = None,
        offline_store: OfflineFeatureStore | None = None,
    ) -> None:
        self._engines: list = list(feature_engines)
        self._online_store = online_store
        self._offline_store = offline_store
        self._warmup_mode = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def warmup(self, events: Iterable) -> list[FeatureEvent]:
        """Pre-heat all engines with historical events.

        Produced FeatureEvents are tagged ``is_warmup=True``.  They are pushed
        to OnlineFeatureStore so the latest snapshot is valid for the first
        live bar, but they are excluded from OfflineFeatureStore queries by
        default (``include_warmup=False``) to preserve point-in-time correctness
        for training pipelines.
        """
        self._warmup_mode = True
        produced: list[FeatureEvent] = []
        try:
            for event in events:
                produced.extend(self._process_event(event))
        finally:
            self._warmup_mode = False
        log.info(
            "FeaturePipeline.warmup: %d events produced by %d engines",
            len(produced), len(self._engines),
        )
        return produced

    def update(self, event) -> list[FeatureEvent]:
        """Process one live event (hot path).

        Returns all FeatureEvents produced by registered engines.
        No DataFrame is created on this path.
        """
        return self._process_event(event)

    def update_many(self, events: Iterable) -> list[FeatureEvent]:
        """Process a batch of events (offline / historical path)."""
        produced: list[FeatureEvent] = []
        for event in events:
            produced.extend(self._process_event(event))
        return produced

    def flush(self) -> int:
        """Flush offline write buffer to Parquet.  Returns rows written."""
        if self._offline_store is not None:
            return self._offline_store.flush()
        return 0

    def state_dict(self) -> dict:
        """Serialise the state of all engines for checkpoint / restore."""
        return {engine.name: engine.state_dict() for engine in self._engines}

    def load_state_dict(self, state: dict) -> None:
        """Restore engine states from a checkpoint dict."""
        for engine in self._engines:
            if engine.name in state:
                engine.load_state_dict(state[engine.name])

    def get_latest_features(self, instrument_id: str) -> dict[str, FeatureEvent]:
        """Return the latest FeatureEvent per feature_set_id for an instrument.

        Uses OnlineFeatureStore.get_all_latest() — O(1) dict lookup.
        Returns {} if no store is configured or instrument has no events.
        """
        if self._online_store is None:
            return {}
        return self._online_store.get_all_latest(instrument_id)

    def get_feature_window(
        self,
        instrument_id: str,
        feature_set_id: str,
        n: int | None = None,
    ) -> list[FeatureEvent]:
        """Return the last N FeatureEvents for an instrument/feature_set pair.

        Reads from OnlineFeatureStore's bounded ring buffer.
        Returns [] if no store is configured or no events are available.
        """
        if self._online_store is None:
            return []
        return self._online_store.get_window(instrument_id, feature_set_id, n=n)

    @property
    def engines(self) -> list:
        return list(self._engines)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _process_event(self, event) -> list[FeatureEvent]:
        produced: list[FeatureEvent] = []
        for engine in self._engines:
            fe = engine.update(event)
            if fe is None:
                continue
            # Stamp warmup flag without mutating the original object
            if self._warmup_mode and not fe.is_warmup:
                fe = replace(fe, is_warmup=True)
            produced.append(fe)
            if self._online_store is not None:
                self._online_store.put(fe)
            if self._offline_store is not None:
                self._offline_store.append(fe)
        return produced
