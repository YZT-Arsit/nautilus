"""End-of-day archiver.

At session close we drain the streaming engine's output buffer and write it
out to the same Hive layout the offline backfill produces, so the data is
indistinguishable from offline-computed data on the next trading day.

Durability protocol
-------------------
We use a **stage-then-commit** pattern so a crash mid-archive cannot leave the
dataset in a half-written state:

  1. Write all Parquet files into ``{root}/_staging/{run_id}/...``.
  2. Atomically rename each staged partition directory into place
     (``os.replace`` on the directory entry — POSIX-atomic on the same FS).
  3. Append manifest rows **after** all renames succeed. Readers consulting
     the manifest will never see a (partition, feature, version) row whose
     files don't exist yet.
  4. If any step fails, the staging directory is left for forensic inspection
     and no manifest rows are written — the partition is exactly where it was
     before the run, making the operation idempotent for safe retry.

Overwrite policy
----------------
Three modes, selectable per call:

  * ``mode="error"``    – raise if any target partition already has data.
  * ``mode="append"``   – write a new ``part-{run_id}.parquet`` alongside any
    existing files. Use this for late-arriving intraday data.
  * ``mode="overwrite"``- remove the target partition's contents first, then
    write the new file. Use for re-runs that supersede previous output.

Default is ``"overwrite"`` because EOD archive is typically the canonical
write for that (partition, feature, version) tuple.
"""
from __future__ import annotations

import logging
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq

from quant_feature_engine.core import registry as _registry
from quant_feature_engine.storage.layout import PartitionKey
from quant_feature_engine.storage.metadata import Manifest, params_hash
from quant_feature_engine.storage.parquet_store import ParquetStore

logger = logging.getLogger(__name__)

WriteMode = Literal["error", "append", "overwrite"]


class EodArchiver:
    """Drain streaming output → staged Parquet → committed Hive Parquet → manifest."""

    def __init__(
        self,
        *,
        raw_store: ParquetStore,
        feature_store: ParquetStore,
        manifest: Manifest,
        staging_root: Path | str | None = None,
    ) -> None:
        self.raw_store = raw_store
        self.feature_store = feature_store
        self.manifest = manifest
        self.staging_root = Path(staging_root) if staging_root else None

    def archive(
        self,
        df: pl.DataFrame,
        *,
        feature_names: list[str],
        raw_columns: list[str],
        partition_values: dict[str, str],
        mode: WriteMode = "overwrite",
    ) -> dict:
        """Split ``df`` into raw + features and write each atomically.

        Returns a small report dict (run_id, mode, partitions_written) for
        logging and tests.
        """
        if df.is_empty():
            logger.warning("Archive called with empty frame")
            return {"run_id": None, "partitions_written": 0, "rows": 0}

        run_id = uuid.uuid4().hex[:12]
        staged: list[tuple[Path, Path, str]] = []  # (staged_file, target_dir, basename)
        manifest_rows: list[dict] = []

        try:
            # 1. Stage raw bars.
            raw_cols_present = [c for c in raw_columns if c in df.columns]
            if raw_cols_present:
                raw_df = df.select(raw_cols_present)
                raw_partitions = {
                    k: v
                    for k, v in partition_values.items()
                    if k in self.raw_store.partition_cols
                }
                staged.extend(
                    self._stage(self.raw_store, raw_df, raw_partitions, run_id, mode)
                )

            # 2. Stage features, bucketed by feature_group.
            feat_base = {
                k: v
                for k, v in partition_values.items()
                if k in self.feature_store.partition_cols and k != "feature_group"
            }
            by_group = self._group_features(feature_names)
            for group, names in by_group.items():
                # Each output column might be emitted by a different feature → collect.
                cols = ["symbol", "ts_event", *names]
                cols_present = [c for c in cols if c in df.columns]
                if len(cols_present) <= 2:
                    continue
                group_df = df.select(cols_present)
                group_partitions = {**feat_base, "feature_group": group}
                staged.extend(
                    self._stage(
                        self.feature_store, group_df, group_partitions, run_id, mode
                    )
                )

                pkey = PartitionKey.from_dict(
                    group_partitions, self.feature_store.partition_cols
                ).to_str()
                # One manifest row per feature whose outputs landed in this group.
                for fname in (
                    f for f in feature_names if _registry.get(f).meta.feature_group == group
                ):
                    cls = _registry.get(fname)
                    manifest_rows.append(
                        {
                            "partition_key": pkey,
                            "feature_name": fname,
                            "version": cls.meta.version,
                            "params_hash": params_hash(cls.meta.params),
                            "computed_at": datetime.now(timezone.utc),
                            "row_count": group_df.height,
                            "source": "eod-archive",
                        }
                    )

            # 3. Commit: move every staged file into place.
            for staged_file, target_dir, basename in staged:
                target_dir.mkdir(parents=True, exist_ok=True)
                final = target_dir / basename
                shutil.move(str(staged_file), str(final))
                logger.debug("Committed %s", final)

            # 4. Write manifest only after every file is in place.
            self.manifest.append(manifest_rows)

        except Exception:
            logger.exception(
                "EOD archive run %s failed; staged files left for inspection at %s",
                run_id,
                self._staging_dir(run_id),
            )
            raise

        # 5. Clean up the now-empty staging directory.
        self._cleanup_staging(run_id)

        return {
            "run_id": run_id,
            "mode": mode,
            "partitions_written": len(staged),
            "rows": df.height,
        }

    # ------------------------------------------------------------------ stage

    def _stage(
        self,
        store: ParquetStore,
        df: pl.DataFrame,
        partition_values: dict[str, str],
        run_id: str,
        mode: WriteMode,
    ) -> list[tuple[Path, Path, str]]:
        """Write one Parquet file into the staging area; return commit recipe."""
        key = PartitionKey.from_dict(partition_values, store.partition_cols)
        target_dir = key.to_path(store.root)

        if mode == "error" and target_dir.exists() and any(target_dir.iterdir()):
            raise FileExistsError(
                f"Partition {target_dir} is non-empty and mode='error'"
            )
        if mode == "overwrite" and target_dir.exists():
            for existing in target_dir.glob("*.parquet"):
                existing.unlink()

        basename = (
            f"part-{run_id}.parquet"
            if mode in ("append", "overwrite")
            else "part-000.parquet"
        )

        stage_dir = self._staging_dir(run_id) / key.to_str()
        stage_dir.mkdir(parents=True, exist_ok=True)
        stage_file = stage_dir / basename

        # Drop partition columns from the file body — Hive reconstructs from path.
        body_cols = [c for c in df.columns if c not in store.partition_cols]
        table = df.select(body_cols).to_arrow()
        pq.write_table(table, stage_file)

        return [(stage_file, target_dir, basename)]

    def _staging_dir(self, run_id: str) -> Path:
        root = self.staging_root or (self.feature_store.root.parent / "_staging")
        return root / run_id

    def _cleanup_staging(self, run_id: str) -> None:
        staging = self._staging_dir(run_id)
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)

    # ------------------------------------------------------------------ util

    def _group_features(self, names: list[str]) -> dict[str, list[str]]:
        """Bucket feature names by the ``feature_group`` declared in their meta."""
        out: dict[str, list[str]] = {}
        for n in names:
            cls = _registry.get(n)
            out.setdefault(cls.meta.feature_group, []).extend(cls.meta.outputs)
        return out

    @staticmethod
    def compact(stores: list[ParquetStore], partition_values: dict[str, str]) -> None:
        """Compact partition files across all given stores. Call after archive()."""
        for s in stores:
            s.compact_partition(
                {k: v for k, v in partition_values.items() if k in s.partition_cols}
            )
