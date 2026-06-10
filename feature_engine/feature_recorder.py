"""
FeatureRecorder — session-scoped wrapper around OfflineFeatureStore.

Mirrors the interface pattern of SignalRecorder for consistency.
Used by the paper live runner to record features during a session.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from feature_engine.feature_event import FeatureEvent
from feature_engine.feature_store import OfflineFeatureStore

if TYPE_CHECKING:
    pass

log = logging.getLogger(__name__)


class FeatureRecorder:
    """Records FeatureEvents to OfflineFeatureStore during a live session.

    Parameters
    ----------
    offline_store : OfflineFeatureStore
        Target store for persistence.
    instrument_id : str | None
        Optional filter; events not matching this instrument_id are still
        accepted (recorder does not filter).
    """

    def __init__(
        self,
        offline_store: OfflineFeatureStore,
        instrument_id: str | None = None,
    ) -> None:
        self._store = offline_store
        self._instrument_id = instrument_id
        self._count = 0

    def append(self, event: FeatureEvent) -> None:
        """Buffer one FeatureEvent.  Auto-flushes at store threshold."""
        self._store.append(event)
        self._count += 1

    def write(self, events: list[FeatureEvent]) -> None:
        """Buffer a list of FeatureEvents."""
        self._store.write(events)
        self._count += len(events)

    def flush(self) -> int:
        """Flush buffer to Parquet.  Returns rows written."""
        n = self._store.flush()
        log.info("FeatureRecorder: flushed %d rows", n)
        return n

    def total_appended(self) -> int:
        """Total events appended since construction (including unflushed)."""
        return self._count

    def __len__(self) -> int:
        return self._store.pending_count()
