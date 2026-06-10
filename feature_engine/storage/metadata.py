"""Feature manifest: which (partition, feature, version) outputs exist.

The manifest is itself a Parquet table, so any tool that reads Parquet can
introspect it (DuckDB, Polars, pandas, Spark). We keep it append-only and
de-duplicate on read — much cheaper than locking for write.

Schema
------
``(partition_key, feature_name, version, params_hash, computed_at, row_count, source)``

The engine consults this before computing: if ``(partition, feature, version,
params_hash)`` is already present, the partition is skipped. The
``--force`` flag in the offline CLI bypasses this check.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq


_MANIFEST_SCHEMA = pa.schema(
    [
        ("partition_key", pa.string()),
        ("feature_name", pa.string()),
        ("version", pa.int32()),
        ("params_hash", pa.string()),
        ("computed_at", pa.timestamp("ns", tz="UTC")),
        ("row_count", pa.int64()),
        ("source", pa.string()),  # 'backfill' | 'streaming' | 'eod-archive'
    ]
)


def params_hash(params: dict[str, Any]) -> str:
    """Stable hash of a params dict. JSON with sorted keys avoids order noise."""
    blob = json.dumps(params, sort_keys=True, default=str).encode()
    return hashlib.sha256(blob).hexdigest()[:16]


class Manifest:
    """Append-only feature manifest stored at ``{root}/manifest.parquet``."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "manifest.parquet"

    # ------------------------------------------------------------------ read

    def read(self) -> pl.DataFrame:
        """Load the manifest, de-duplicated on (partition, feature, version).

        Reads both the coalesced ``manifest.parquet`` (if present) and every
        un-compacted ``manifest-{ts}.parquet`` shard, so freshly appended rows
        are visible immediately without waiting for compaction.
        """
        shards = sorted(self.root.glob("manifest*.parquet"))
        if not shards:
            return pl.from_arrow(_MANIFEST_SCHEMA.empty_table())  # type: ignore[return-value]
        frames = [pl.read_parquet(p) for p in shards]
        df = pl.concat(frames, how="vertical_relaxed")
        return df.sort("computed_at").unique(
            subset=["partition_key", "feature_name", "version", "params_hash"],
            keep="last",
        )

    def has(
        self,
        partition_key: str,
        feature_name: str,
        version: int,
        ph: str,
    ) -> bool:
        df = self.read()
        if df.is_empty():
            return False
        return not df.filter(
            (pl.col("partition_key") == partition_key)
            & (pl.col("feature_name") == feature_name)
            & (pl.col("version") == version)
            & (pl.col("params_hash") == ph)
        ).is_empty()

    # ------------------------------------------------------------------ write

    def append(self, rows: list[dict[str, Any]]) -> None:
        """Append manifest rows. Cheap: writes a new Parquet file alongside.

        On read we coalesce all files in the manifest directory. This avoids
        write locks and concurrent-writer races at the cost of a slightly more
        expensive read — acceptable because the manifest is tiny.
        """
        if not rows:
            return
        for r in rows:
            r.setdefault("computed_at", datetime.now(timezone.utc))
        table = pa.Table.from_pylist(rows, schema=_MANIFEST_SCHEMA)
        suffix = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
        out = self.root / f"manifest-{suffix}.parquet"
        pq.write_table(table, out)

        # Periodically coalesce so we don't accumulate thousands of small files.
        shards = sorted(self.root.glob("manifest-*.parquet"))
        if len(shards) >= 32:
            self._compact(shards)

    def _compact(self, shards: list[Path]) -> None:
        merged_frames = [pl.read_parquet(p) for p in shards]
        merged = pl.concat(merged_frames, how="vertical_relaxed").to_arrow()
        tmp = self.root / "manifest.parquet.tmp"
        pq.write_table(merged, tmp)
        for p in shards:
            p.unlink(missing_ok=True)
        if self.path.exists():
            self.path.unlink()
        tmp.rename(self.path)
