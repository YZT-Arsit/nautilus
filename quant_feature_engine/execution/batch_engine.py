"""Offline batch engine.

Computes features partition-by-partition. Each partition is independent (a
clean Feature state per worker) which makes parallelism trivial. By default we
use a ``concurrent.futures.ProcessPoolExecutor`` for CPU-bound work; the
distributed runner in :mod:`quant_feature_engine.execution.distributed` swaps
this out for Ray when scaling across machines.
"""
from __future__ import annotations

import logging
import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import polars as pl

from quant_feature_engine.core import registry as _registry
from quant_feature_engine.core.dag import FeatureDAG
from quant_feature_engine.storage.layout import PartitionKey
from quant_feature_engine.storage.metadata import Manifest, params_hash
from quant_feature_engine.storage.parquet_store import ParquetStore

logger = logging.getLogger(__name__)


@dataclass
class BackfillSpec:
    """One unit of offline work: compute features over one raw partition."""

    raw_filters: dict[str, str]
    """Equality predicates that select exactly the raw partition to read."""

    feature_names: list[str]
    """Top-level features requested. Transitive deps are pulled by the DAG."""

    raw_store_root: Path
    feature_store_root: Path

    def partition_values_for_feature(self, feature_group: str) -> dict[str, str]:
        """Translate the raw filters into the feature partition values."""
        return {
            "feature_group": feature_group,
            "frequency": self.raw_filters["frequency"],
            "trading_date": self.raw_filters["trading_date"],
        }


def _run_partition(spec: BackfillSpec) -> dict:
    """Worker entry point. Idempotent and self-contained — pickle-safe."""
    # Import inside the worker so child processes pick the same registrations.
    from quant_feature_engine.features import load_all  # noqa: PLC0415

    load_all()

    raw_store = ParquetStore(
        spec.raw_store_root,
        partition_cols=("asset_class", "exchange", "frequency", "trading_date"),
    )
    feature_store = ParquetStore(
        spec.feature_store_root,
        partition_cols=("feature_group", "frequency", "trading_date"),
    )

    df = raw_store.scan(filters=spec.raw_filters)
    if df.is_empty():
        return {"partition": spec.raw_filters, "rows": 0, "skipped": True}
    # Time-series correctness: features must see rows sorted by (symbol, ts_event).
    # Sorting here once is cheaper than sorting per-feature.
    df = df.sort(["symbol", "ts_event"])

    dag = FeatureDAG(spec.feature_names)
    features = dag.instantiate()

    for name in dag.order:
        f = features[name]
        present = [c for c in f.meta.inputs if c in df.columns]
        proj = df.select(present) if present else df
        cols = f.compute_batch(proj)
        df = df.hstack(cols)

    # Bucket feature outputs by feature_group and write each.
    by_group: dict[str, list[str]] = {}
    for name in dag.order:
        cls = _registry.get(name)
        by_group.setdefault(cls.meta.feature_group, []).extend(cls.meta.outputs)

    manifest_rows: list[dict] = []
    for group, cols_out in by_group.items():
        out_df = df.select(["symbol", "ts_event", *cols_out])
        partitions = spec.partition_values_for_feature(group)
        feature_store.write(out_df, partition_values=partitions)
        pkey = PartitionKey.from_dict(
            partitions, feature_store.partition_cols
        ).to_str()
        for col_owner in {n for n in dag.order if _registry.get(n).meta.feature_group == group}:
            cls = _registry.get(col_owner)
            manifest_rows.append(
                {
                    "partition_key": pkey,
                    "feature_name": col_owner,
                    "version": cls.meta.version,
                    "params_hash": params_hash(cls.meta.params),
                    "computed_at": datetime.now(timezone.utc),
                    "row_count": out_df.height,
                    "source": "backfill",
                }
            )

    return {
        "partition": spec.raw_filters,
        "rows": df.height,
        "manifest_rows": manifest_rows,
    }


class BatchEngine:
    """Drive offline backfill across many partitions.

    Parameters
    ----------
    raw_root, feature_root : Hive dataset roots.
    manifest : Used to skip already-computed (partition, feature, version).
    n_workers : Process pool size. Default: cpu_count.
    """

    def __init__(
        self,
        *,
        raw_root: Path | str,
        feature_root: Path | str,
        manifest: Manifest,
        n_workers: int | None = None,
    ) -> None:
        self.raw_root = Path(raw_root)
        self.feature_root = Path(feature_root)
        self.manifest = manifest
        self.n_workers = n_workers or (os.cpu_count() or 1)

    def run(
        self,
        partitions: list[dict[str, str]],
        feature_names: list[str],
        *,
        force: bool = False,
    ) -> list[dict]:
        """Run the backfill. Returns one result dict per partition.

        ``partitions`` is a list of equality-predicate dicts identifying raw
        partitions (e.g. ``{"asset_class": "stock", "exchange": "SSE",
        "frequency": "1m", "trading_date": "2026-05-26"}``).
        """
        specs = [
            BackfillSpec(
                raw_filters=p,
                feature_names=feature_names,
                raw_store_root=self.raw_root,
                feature_store_root=self.feature_root,
            )
            for p in partitions
            if force or self._needs_recompute(p, feature_names)
        ]
        logger.info("Backfill: %d/%d partitions need work", len(specs), len(partitions))
        if not specs:
            return []

        if self.n_workers <= 1:
            results = [_run_partition(s) for s in specs]
        else:
            with ProcessPoolExecutor(max_workers=self.n_workers) as pool:
                futures = [pool.submit(_run_partition, s) for s in specs]
                results = [f.result() for f in as_completed(futures)]

        all_manifest_rows: list[dict] = []
        for r in results:
            all_manifest_rows.extend(r.get("manifest_rows", []))
        self.manifest.append(all_manifest_rows)
        return results

    def _needs_recompute(
        self, partition: dict[str, str], feature_names: list[str]
    ) -> bool:
        """Check the manifest: skip if all requested features are up to date."""
        for fname in feature_names:
            cls = _registry.get(fname)
            partitions = {
                "feature_group": cls.meta.feature_group,
                "frequency": partition["frequency"],
                "trading_date": partition["trading_date"],
            }
            pkey = PartitionKey(tuple((k, partitions[k]) for k in
                                      ("feature_group", "frequency", "trading_date"))).to_str()
            if not self.manifest.has(
                pkey, fname, cls.meta.version, params_hash(cls.meta.params)
            ):
                return True
        return False
