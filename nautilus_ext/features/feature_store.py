"""
Feature stores — online (in-memory ring buffer) and offline (Parquet-backed).

Two layers with separate responsibilities:

OnlineFeatureStore (real-time path)
    - Stores recent FeatureEvents per (instrument_id, feature_set_id) in a deque.
    - Signal engines call get_latest() on each bar — zero file I/O on this path.

OfflineFeatureStore (persistence path)
    - Buffers FeatureEvents in memory; flushes to Parquet in batches.
    - Never writes one file per event (DataHander anti-pattern avoided).
    - query() supports instrument_id / feature_set_id / time range filtering.

DataHander reference
    Adopted: partition-keyed LRU concept, two-level filter (directory + rows),
    incremental append pattern, schema file management.
    Not adopted: Ray dependency, Windows-only paths, UUID file names, MATLAB I/O.

Directory layout when using OfflineFeatureStore
    base_path/
        schemas/
            {feature_set_id}_{version}.json
        offline/
            {feature_set_id}/
                {safe_instrument_id}/
                    {start_ts}-{end_ts}.parquet
"""
from __future__ import annotations

import logging
from collections import deque
from pathlib import Path
from typing import TYPE_CHECKING

import pandas as pd

from nautilus_ext.features.feature_event import FeatureEvent
from nautilus_ext.features.feature_schema import FeatureSetSpec

if TYPE_CHECKING:
    pass

log = logging.getLogger(__name__)

_DEFAULT_WINDOW_SIZE = 500
_DEFAULT_FLUSH_THRESHOLD = 1_000


class OnlineFeatureStore:
    """In-memory ring buffer for real-time feature access.

    Parameters
    ----------
    window_size : int
        Number of recent FeatureEvents to retain per (instrument_id, feature_set_id).
    """

    def __init__(self, window_size: int = _DEFAULT_WINDOW_SIZE) -> None:
        self._window_size = window_size
        self._buffers: dict[tuple[str, str], deque[FeatureEvent]] = {}

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def put(self, event: FeatureEvent) -> None:
        """Add a FeatureEvent to the ring buffer (online hot path)."""
        key = (event.instrument_id, event.feature_set_id)
        if key not in self._buffers:
            self._buffers[key] = deque(maxlen=self._window_size)
        self._buffers[key].append(event)

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get_latest(
        self,
        instrument_id: str,
        feature_set_id: str,
    ) -> FeatureEvent | None:
        """Return the most recent FeatureEvent, or None if not available."""
        buf = self._buffers.get((instrument_id, feature_set_id))
        if not buf:
            return None
        return buf[-1]

    def get_window(
        self,
        instrument_id: str,
        feature_set_id: str,
        n: int | None = None,
        start: int | None = None,
        end: int | None = None,
    ) -> list[FeatureEvent]:
        """Return a slice of recent events, optionally filtered by time."""
        buf = self._buffers.get((instrument_id, feature_set_id))
        if not buf:
            return []
        events = list(buf)
        if start is not None:
            events = [e for e in events if e.ts_event >= start]
        if end is not None:
            events = [e for e in events if e.ts_event <= end]
        if n is not None:
            events = events[-n:]
        return events

    # ------------------------------------------------------------------
    # Management
    # ------------------------------------------------------------------

    def clear(
        self,
        instrument_id: str | None = None,
        feature_set_id: str | None = None,
    ) -> None:
        """Clear all or a subset of buffers."""
        if instrument_id is None and feature_set_id is None:
            self._buffers.clear()
            return
        to_remove = [
            k for k in self._buffers
            if (instrument_id is None or k[0] == instrument_id)
            and (feature_set_id is None or k[1] == feature_set_id)
        ]
        for k in to_remove:
            del self._buffers[k]

    def keys(self) -> list[tuple[str, str]]:
        """Return all (instrument_id, feature_set_id) pairs currently buffered."""
        return list(self._buffers.keys())

    def __len__(self) -> int:
        return sum(len(b) for b in self._buffers.values())


class OfflineFeatureStore:
    """Parquet-backed feature store for historical and offline persistence.

    Buffers events in memory; flushes in batches to avoid per-event small files.
    The DataHander incremental-compute pattern is adopted here: query() diffs
    existing data so callers can skip already-computed timestamps.

    Parameters
    ----------
    base_path : str | Path
        Root directory.  Will be created if it does not exist.
    flush_threshold : int
        Buffer size that triggers an automatic flush.
    """

    def __init__(
        self,
        base_path: str | Path,
        flush_threshold: int = _DEFAULT_FLUSH_THRESHOLD,
    ) -> None:
        self._base = Path(base_path)
        self._flush_threshold = flush_threshold
        self._buffer: list[FeatureEvent] = []
        self._schemas: dict[str, FeatureSetSpec] = {}

    # ------------------------------------------------------------------
    # Write path
    # ------------------------------------------------------------------

    def append(self, event: FeatureEvent) -> None:
        """Buffer one event; auto-flush when threshold is reached."""
        self._buffer.append(event)
        if len(self._buffer) >= self._flush_threshold:
            self.flush()

    def write(self, events: list[FeatureEvent]) -> None:
        """Buffer a list of events; auto-flush when threshold is reached."""
        self._buffer.extend(events)
        if len(self._buffer) >= self._flush_threshold:
            self.flush()

    def flush(self) -> int:
        """Flush all buffered events to Parquet.

        Returns the number of rows written.  Each (instrument_id, feature_set_id)
        group is written to its own file; the timestamp range is encoded in the
        filename so queries can skip irrelevant files before opening them.
        """
        if not self._buffer:
            return 0

        rows = [e.to_row() for e in self._buffer]
        df = pd.DataFrame(rows)
        n = len(df)

        for (instrument_id, feature_set_id), grp in df.groupby(
            ["instrument_id", "feature_set_id"]
        ):
            dest = self._parquet_path(instrument_id, feature_set_id, grp)
            dest.parent.mkdir(parents=True, exist_ok=True)
            grp.to_parquet(dest, index=False, engine="pyarrow")
            log.debug(
                "OfflineFeatureStore: flushed %d rows → %s", len(grp), dest
            )

        self._buffer.clear()
        return n

    # ------------------------------------------------------------------
    # Read path
    # ------------------------------------------------------------------

    def query(
        self,
        instrument_id: str | None = None,
        feature_set_id: str | None = None,
        start: int | None = None,
        end: int | None = None,
        include_warmup: bool = False,
    ) -> pd.DataFrame:
        """Read and filter persisted feature data from Parquet.

        Parameters
        ----------
        instrument_id : str | None
            Filter to one instrument; None returns all instruments.
        feature_set_id : str | None
            Filter to one feature set; None returns all.
        start, end : int | None
            Millisecond POSIX timestamp range (inclusive).
        include_warmup : bool
            If False (default), warmup rows are excluded so training
            data has point-in-time correctness.
        """
        offline_root = self._base / "offline"
        if not offline_root.exists():
            return pd.DataFrame()

        parts: list[pd.DataFrame] = []
        for parquet_path in offline_root.rglob("*.parquet"):
            path_parts = parquet_path.parts
            if feature_set_id is not None and feature_set_id not in path_parts:
                continue
            safe_iid = (
                instrument_id.replace("/", "_").replace(".", "_")
                if instrument_id is not None
                else None
            )
            if safe_iid is not None and safe_iid not in path_parts:
                continue
            try:
                parts.append(pd.read_parquet(parquet_path, engine="pyarrow"))
            except Exception as exc:
                log.warning(
                    "OfflineFeatureStore: could not read %s: %s", parquet_path, exc
                )

        if not parts:
            return pd.DataFrame()

        df = pd.concat(parts, ignore_index=True)

        if instrument_id is not None:
            df = df[df["instrument_id"] == instrument_id]
        if feature_set_id is not None:
            df = df[df["feature_set_id"] == feature_set_id]
        if start is not None:
            df = df[df["ts_event"] >= start]
        if end is not None:
            df = df[df["ts_event"] <= end]
        if not include_warmup and "is_warmup" in df.columns:
            df = df[~df["is_warmup"]]

        # Dedup in case of overlapping flush windows
        subset = ["ts_event", "instrument_id", "feature_set_id"]
        subset = [c for c in subset if c in df.columns]
        if subset:
            df = df.drop_duplicates(subset=subset)

        return df.sort_values("ts_event").reset_index(drop=True)

    def pending_count(self) -> int:
        """Number of events in the write buffer awaiting flush."""
        return len(self._buffer)

    # ------------------------------------------------------------------
    # Schema management
    # ------------------------------------------------------------------

    def write_schema(self, schema: FeatureSetSpec) -> Path:
        """Persist a feature set schema to disk and cache it in memory."""
        dest = self._base / "schemas" / f"{schema.feature_set_id}_{schema.version}.json"
        self._schemas[schema.feature_set_id] = schema
        return schema.save(dest)

    def load_schema(
        self,
        feature_set_id: str,
        version: str | None = None,
    ) -> FeatureSetSpec:
        """Load a schema from disk (uses in-memory cache if available)."""
        if feature_set_id in self._schemas:
            return self._schemas[feature_set_id]
        schemas_dir = self._base / "schemas"
        if version:
            path = schemas_dir / f"{feature_set_id}_{version}.json"
            spec = FeatureSetSpec.load(path)
        else:
            candidates = sorted(schemas_dir.glob(f"{feature_set_id}_*.json"))
            if not candidates:
                raise FileNotFoundError(
                    f"No schema for {feature_set_id!r} in {schemas_dir}"
                )
            spec = FeatureSetSpec.load(candidates[-1])
        self._schemas[feature_set_id] = spec
        return spec

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _parquet_path(
        self,
        instrument_id: str,
        feature_set_id: str,
        df: "pd.DataFrame",
    ) -> Path:
        start_ts = int(df["ts_event"].min())
        end_ts = int(df["ts_event"].max())
        safe_iid = str(instrument_id).replace("/", "_").replace(".", "_")
        return (
            self._base
            / "offline"
            / str(feature_set_id)
            / safe_iid
            / f"{start_ts}-{end_ts}.parquet"
        )
