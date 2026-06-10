"""Parquet/Hive read & write façade.

Reads
-----
We use ``pyarrow.dataset`` so we get partition pruning and column pushdown for
free. Filters are passed as ``pyarrow.compute`` expressions (or as a dict of
equality predicates which we translate). The frame is materialised into Polars
via ``pl.from_arrow`` — a zero-copy view onto the same Arrow buffers.

Writes
------
We use ``pyarrow.dataset.write_dataset`` with ``partitioning="hive"`` so writes
end up in the same layout readers expect. For streaming output we keep one
file per ``trading_date`` partition; the EOD archiver compacts those into a
single coalesced file.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import polars as pl
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.dataset as ds
import pyarrow.parquet as pq


class ParquetStore:
    """A Hive-partitioned Parquet dataset.

    Parameters
    ----------
    root : Root directory of the dataset (e.g. ``data/raw`` or ``data/features``).
    partition_cols : Hive partition columns in path order. Must match the
        directory structure on disk.
    """

    def __init__(self, root: Path | str, partition_cols: tuple[str, ...]) -> None:
        self.root = Path(root)
        self.partition_cols = partition_cols
        self.root.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ read

    def scan(
        self,
        filters: dict[str, Any] | pc.Expression | None = None,
        columns: list[str] | None = None,
        *,
        drop_partition_cols: bool = True,
    ) -> pl.DataFrame:
        """Read with partition pruning and column projection.

        ``filters`` accepts either a dict of equality predicates (translated
        into an Arrow expression — fastest, fully pushed down to the row group
        statistics) or a raw ``pc.Expression`` for richer predicates.

        Schema homogeneity
        ------------------
        When ``filters`` is an equality dict that fully specifies every
        partition column, we **rebuild the dataset rooted at the matching
        partition directory** instead of scanning the whole tree. That keeps
        the schema homogeneous and avoids the PyArrow behaviour where a
        union dataset surfaces null-padded columns from sibling partitions.

        When ``drop_partition_cols`` is ``True`` (default), the Hive partition
        columns are stripped from the result frame; they're already pinned by
        the filter so re-exposing them is just bookkeeping noise.
        """
        if not self.root.exists() or not any(self.root.iterdir()):
            empty_schema = pa.schema([])
            return pl.from_arrow(empty_schema.empty_table())  # type: ignore[return-value]

        scan_root, residual_filter = self._narrow_root(filters)
        try:
            dataset = ds.dataset(scan_root, format="parquet", partitioning="hive")
        except (pa.ArrowInvalid, FileNotFoundError):
            empty_schema = pa.schema([])
            return pl.from_arrow(empty_schema.empty_table())  # type: ignore[return-value]

        expr = (
            self._filters_to_expr(residual_filter)
            if residual_filter is not None
            else None
        )
        scanner = dataset.scanner(columns=columns, filter=expr)
        table = scanner.to_table()
        frame = pl.from_arrow(table)
        if drop_partition_cols:
            to_drop = [c for c in self.partition_cols if c in frame.columns]
            if to_drop:
                frame = frame.drop(to_drop)
        return frame  # type: ignore[return-value]

    def _narrow_root(
        self, filters: dict[str, Any] | pc.Expression | None
    ) -> tuple[Path, dict[str, Any] | pc.Expression | None]:
        """If ``filters`` fully specifies every partition column, rebase the
        dataset root at the matching directory and drop those keys from the
        filter — equivalent to manual partition pruning but homogeneous in
        schema.
        """
        if not isinstance(filters, dict):
            return self.root, filters
        if not all(k in filters for k in self.partition_cols):
            return self.root, filters
        sub = self.root
        for k in self.partition_cols:
            sub = sub / f"{k}={filters[k]}"
        residual = {k: v for k, v in filters.items() if k not in self.partition_cols}
        return sub, (residual or None)

    def _filters_to_expr(
        self, filters: dict[str, Any] | pc.Expression
    ) -> pc.Expression:
        if isinstance(filters, pc.Expression):
            return filters
        expr: pc.Expression | None = None
        for k, v in filters.items():
            term = pc.field(k) == pa.scalar(v)
            expr = term if expr is None else expr & term
        assert expr is not None
        return expr

    # ------------------------------------------------------------------ write

    def write(
        self,
        df: pl.DataFrame,
        partition_values: dict[str, str] | None = None,
        basename_template: str = "part-{i}.parquet",
    ) -> list[Path]:
        """Write a frame with Hive partitioning.

        If ``partition_values`` is provided we attach those as columns before
        writing (they become the partition directories). Otherwise the columns
        must already be in ``df`` and named exactly as ``self.partition_cols``.
        """
        if partition_values:
            df = df.with_columns(
                [pl.lit(v).alias(k) for k, v in partition_values.items()]
            )
        missing = [c for c in self.partition_cols if c not in df.columns]
        if missing:
            raise ValueError(f"Missing partition columns in frame: {missing}")

        table = df.to_arrow()
        written: list[Path] = []

        def _visit(file_visit: ds.WrittenFile) -> None:
            written.append(Path(file_visit.path))

        ds.write_dataset(
            table,
            base_dir=str(self.root),
            format="parquet",
            partitioning=list(self.partition_cols),
            partitioning_flavor="hive",
            existing_data_behavior="overwrite_or_ignore",
            basename_template=basename_template,
            file_visitor=_visit,
        )
        return written

    # ------------------------------------------------------------------ compact

    def compact_partition(self, partition_values: dict[str, str]) -> Path | None:
        """Coalesce all files in one partition into a single Parquet file.

        Useful after a streaming session has written many small files. Returns
        the path of the resulting file, or ``None`` if the partition is empty.
        """
        from feature_engine.storage.layout import PartitionKey

        key = PartitionKey.from_dict(partition_values, self.partition_cols)
        part_dir = key.to_path(self.root)
        if not part_dir.exists():
            return None

        parts = sorted(part_dir.glob("*.parquet"))
        if not parts:
            return None
        if len(parts) == 1:
            return parts[0]

        # Read all, write one, drop the originals atomically (rename trick).
        table = pq.read_table(part_dir)
        merged = part_dir / "part-compacted.parquet.tmp"
        pq.write_table(table, merged)
        for p in parts:
            p.unlink()
        final = part_dir / "part-000.parquet"
        merged.rename(final)
        return final
