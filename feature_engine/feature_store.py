"""
Feature stores — online (in-memory ring buffer) and offline (Parquet-backed).

Two layers with separate responsibilities:

OnlineFeatureStore (real-time path)
    - Two-level index:
        _latest  dict[instrument_id][feature_set_id] -> FeatureEvent   O(1) dict lookup
        _buffers dict[(iid, fsid)] -> deque[FeatureEvent]              for get_window()
    - get_latest / get_all_latest are true O(1) dict lookups — no deque scan.
    - Zero file I/O on this path.

OfflineFeatureStore (persistence path)
    - Buffers FeatureEvents in memory; flushes to Parquet in batches.
    - Never writes one file per event (DataHandler anti-pattern avoided).
    - Maintains a FeatureManifest JSON index: (feature_set_id, instrument_id,
      ts range) → Parquet file path.  query() uses the manifest to skip files
      whose time range does not overlap the query — avoids rglob on every call.
    - Falls back to rglob when manifest is empty (backward compat / new store).

Directory layout:
    base_path/
        feature_manifest.json
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
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

import pandas as pd

from feature_engine.feature_event import FeatureEvent
from feature_engine.feature_manifest import FeatureManifest, ManifestRecord
from feature_engine.feature_schema import FeatureSetSpec

if TYPE_CHECKING:
    pass

log = logging.getLogger(__name__)

_DEFAULT_WINDOW_SIZE = 500
_DEFAULT_FLUSH_THRESHOLD = 1_000


class OnlineFeatureStore:
    """In-memory ring buffer for real-time feature access.

    Internal structure:

    ``_latest``  — dict[instrument_id][feature_set_id] → FeatureEvent
        Primary O(1) index.  Always holds the most recent event per key.
        Updated on every put(); read by get_latest() and get_all_latest().

    ``_buffers`` — dict[(instrument_id, feature_set_id)] → deque[FeatureEvent]
        Bounded ring buffer for time-windowed access.
        Read by get_window(); deque.maxlen prevents unbounded growth.

    Parameters
    ----------
    window_size : int
        Number of recent FeatureEvents to retain per (instrument_id, feature_set_id).
    """

    def __init__(self, window_size: int = _DEFAULT_WINDOW_SIZE) -> None:
        self._window_size = window_size
        # O(1) latest index: instrument_id -> {feature_set_id -> FeatureEvent}
        self._latest: dict[str, dict[str, FeatureEvent]] = {}
        # Window buffer: (instrument_id, feature_set_id) -> deque
        self._buffers: dict[tuple[str, str], deque[FeatureEvent]] = {}

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def put(self, event: FeatureEvent) -> None:
        """Add a FeatureEvent (online hot path — O(1), no file I/O)."""
        iid = event.instrument_id
        fsid = event.feature_set_id
        # Update O(1) latest index
        if iid not in self._latest:
            self._latest[iid] = {}
        self._latest[iid][fsid] = event
        # Update window buffer
        key = (iid, fsid)
        if key not in self._buffers:
            self._buffers[key] = deque(maxlen=self._window_size)
        self._buffers[key].append(event)

    # ------------------------------------------------------------------
    # Read — O(1) latest access
    # ------------------------------------------------------------------

    def get_latest(
        self,
        instrument_id: str,
        feature_set_id: str,
    ) -> FeatureEvent | None:
        """O(1) dict lookup — most recent FeatureEvent or None."""
        return self._latest.get(instrument_id, {}).get(feature_set_id)

    def get_all_latest(self, instrument_id: str) -> dict[str, FeatureEvent]:
        """O(1) — return latest FeatureEvent per feature_set_id for one instrument.

        Returns a shallow copy so callers cannot mutate the store's internal dict.
        """
        return dict(self._latest.get(instrument_id, {}))

    # ------------------------------------------------------------------
    # Read — windowed access
    # ------------------------------------------------------------------

    def get_window(
        self,
        instrument_id: str,
        feature_set_id: str,
        n: int | None = None,
        start: int | None = None,
        end: int | None = None,
    ) -> list[FeatureEvent]:
        """Return a slice of recent events, optionally filtered by time.

        The deque has a fixed maxlen so this never scans unbounded history;
        at most ``window_size`` events are inspected.
        """
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
        """Clear all or a subset of buffers and latest entries."""
        if instrument_id is None and feature_set_id is None:
            self._buffers.clear()
            self._latest.clear()
            return
        # Remove matching buffer keys
        to_remove = [
            k for k in self._buffers
            if (instrument_id is None or k[0] == instrument_id)
            and (feature_set_id is None or k[1] == feature_set_id)
        ]
        for k in to_remove:
            del self._buffers[k]
        # Remove matching latest entries
        for iid in ([instrument_id] if instrument_id else list(self._latest.keys())):
            if iid not in self._latest:
                continue
            if feature_set_id is None:
                del self._latest[iid]
            elif feature_set_id in self._latest[iid]:
                del self._latest[iid][feature_set_id]
                if not self._latest[iid]:
                    del self._latest[iid]

    def keys(self) -> list[tuple[str, str]]:
        """Return all (instrument_id, feature_set_id) pairs currently buffered."""
        return list(self._buffers.keys())

    def __len__(self) -> int:
        return sum(len(b) for b in self._buffers.values())


class OfflineFeatureStore:
    """Parquet-backed feature store for historical and offline persistence.

    Buffers events in memory; flushes in batches to avoid per-event small files.
    After each flush, a JSON manifest is updated so that subsequent query()
    calls can find files by time range without scanning the directory tree.

    Parameters
    ----------
    base_path : str | Path
        Root directory.  Will be created on first flush.
    flush_threshold : int
        Buffer size that triggers an automatic flush.
    use_manifest : bool
        If True (default), maintain a JSON manifest for fast query().
        Set False only for isolated testing or read-only base_paths.
    """

    def __init__(
        self,
        base_path: str | Path,
        flush_threshold: int = _DEFAULT_FLUSH_THRESHOLD,
        use_manifest: bool = True,
    ) -> None:
        self._base = Path(base_path)
        self._flush_threshold = flush_threshold
        self._buffer: list[FeatureEvent] = []
        self._schemas: dict[str, FeatureSetSpec] = {}
        self._manifest: FeatureManifest | None = None
        if use_manifest:
            self._manifest = FeatureManifest(self._base / "feature_manifest.json")
            try:
                self._manifest.load()
            except Exception as exc:
                log.warning("OfflineFeatureStore: could not load manifest: %s", exc)

    # ------------------------------------------------------------------
    # Write path
    # ------------------------------------------------------------------

    def append(self, event: FeatureEvent) -> None:
        """Buffer one event; auto-flush when threshold is reached."""
        self._buffer.append(event)
        if len(self._buffer) >= self._flush_threshold:
            self.flush()

    def extend(self, events: list[FeatureEvent]) -> None:
        """Buffer a list of events (alias for write())."""
        self.write(events)

    def write(self, events: list[FeatureEvent]) -> None:
        """Buffer a list of events; auto-flush when threshold is reached."""
        self._buffer.extend(events)
        if len(self._buffer) >= self._flush_threshold:
            self.flush()

    def flush(self) -> int:
        """Flush all buffered events to Parquet and update the manifest.

        Returns the number of rows written.  Each (instrument_id, feature_set_id)
        group is written to its own file; the timestamp range is encoded in the
        filename so manual glob queries can also skip irrelevant files.
        """
        if not self._buffer:
            return 0

        rows = [e.to_row() for e in self._buffer]
        df = pd.DataFrame(rows)
        n = len(df)
        created_at = datetime.now(timezone.utc).isoformat()

        for (instrument_id, feature_set_id), grp in df.groupby(
            ["instrument_id", "feature_set_id"]
        ):
            dest = self._parquet_path(instrument_id, feature_set_id, grp)
            dest.parent.mkdir(parents=True, exist_ok=True)
            grp.to_parquet(dest, index=False, engine="pyarrow")

            if self._manifest is not None:
                fv = (
                    str(grp["feature_version"].iloc[0])
                    if "feature_version" in grp.columns
                    else "1"
                )
                self._manifest.append_file_record(ManifestRecord(
                    feature_set_id=str(feature_set_id),
                    feature_version=fv,
                    instrument_id=str(instrument_id),
                    start_ts=int(grp["ts_event"].min()),
                    end_ts=int(grp["ts_event"].max()),
                    row_count=len(grp),
                    file_path=str(dest),
                    created_at=created_at,
                ))

            log.debug(
                "OfflineFeatureStore: flushed %d rows → %s", len(grp), dest
            )

        if self._manifest is not None:
            self._manifest.save()

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
        columns: list[str] | None = None,
    ) -> pd.DataFrame:
        """Read and filter persisted feature data.

        Uses the manifest index when available to skip files outside the
        requested time range.  Falls back to rglob when the manifest is empty
        or absent (backward compatibility with existing stores).

        Parameters
        ----------
        instrument_id : str | None
            Filter to one instrument; None returns all instruments.
        feature_set_id : str | None
            Filter to one feature set; None returns all.
        start, end : int | None
            Millisecond POSIX timestamp range (inclusive).
        include_warmup : bool
            If False (default), warmup rows are excluded (point-in-time safe).
        columns : list[str] | None
            If provided, only these columns are returned in addition to the
            required metadata columns (ts_event, instrument_id, feature_set_id,
            feature_version, is_warmup).  None returns all columns.
        """
        parts: list[pd.DataFrame] = []

        # --- manifest path (fast) -------------------------------------
        if self._manifest is not None and len(self._manifest) > 0:
            file_paths = self._manifest.find_files(
                feature_set_id=feature_set_id,
                instrument_id=instrument_id,
                start=start,
                end=end,
            )
            for fp in file_paths:
                p = Path(fp)
                if not p.exists():
                    log.warning(
                        "OfflineFeatureStore: manifest references missing file %s", p
                    )
                    continue
                try:
                    parts.append(pd.read_parquet(p, engine="pyarrow"))
                except Exception as exc:
                    log.warning(
                        "OfflineFeatureStore: could not read %s: %s", p, exc
                    )
        else:
            # --- fallback: directory scan (backward compat / empty manifest) ---
            offline_root = self._base / "offline"
            if not offline_root.exists():
                return pd.DataFrame()
            safe_iid = (
                instrument_id.replace("/", "_").replace(".", "_")
                if instrument_id is not None
                else None
            )
            for parquet_path in offline_root.rglob("*.parquet"):
                path_parts = parquet_path.parts
                if feature_set_id is not None and feature_set_id not in path_parts:
                    continue
                if safe_iid is not None and safe_iid not in path_parts:
                    continue
                try:
                    parts.append(pd.read_parquet(parquet_path, engine="pyarrow"))
                except Exception as exc:
                    log.warning(
                        "OfflineFeatureStore: could not read %s: %s",
                        parquet_path, exc,
                    )

        if not parts:
            return pd.DataFrame()

        df = pd.concat(parts, ignore_index=True)

        # --- row-level filters (precise boundaries) -------------------
        if instrument_id is not None and "instrument_id" in df.columns:
            df = df[df["instrument_id"] == instrument_id]
        if feature_set_id is not None and "feature_set_id" in df.columns:
            df = df[df["feature_set_id"] == feature_set_id]
        if start is not None and "ts_event" in df.columns:
            df = df[df["ts_event"] >= start]
        if end is not None and "ts_event" in df.columns:
            df = df[df["ts_event"] <= end]
        if not include_warmup and "is_warmup" in df.columns:
            df = df[~df["is_warmup"]]

        # Dedup in case of overlapping flush windows
        subset = [
            c for c in ["ts_event", "instrument_id", "feature_set_id"]
            if c in df.columns
        ]
        if subset:
            df = df.drop_duplicates(subset=subset)

        df = df.sort_values("ts_event").reset_index(drop=True)

        # --- column selection -----------------------------------------
        if columns is not None:
            _required = {
                "ts_event", "instrument_id", "feature_set_id",
                "feature_version", "is_warmup",
            }
            keep = list(_required | set(columns))
            keep = [c for c in keep if c in df.columns]
            df = df[keep]

        return df

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
