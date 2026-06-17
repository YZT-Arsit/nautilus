"""Read-only local catalog over the Hive-partitioned ``market_data`` root.

Scans the filesystem (no Parquet read, no network) and reports which partitions
exist.  A partition is one ``exchange/venue_type/symbol/{bar_type|data_type}/date``
leaf directory containing at least one ``.parquet`` file.

Reuses :func:`data_engine.sources.hive_partitioning.hive_partition_values` for
path parsing.  Imports no ``pyarrow``, ``polars``, ``feature_engine`` or
``nautilus_trader`` -- pure stdlib + the existing path helper.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from data_engine.sources.hive_partitioning import hive_partition_values


def partition_relpath(
    *,
    exchange: str,
    venue_type: str,
    symbol: str,
    data_kind: str,
    date: str,
    bar_type: str | None = None,
    data_type: str | None = None,
) -> str:
    """Hive-relative path for one partition, e.g.
    ``exchange=BINANCE/venue_type=spot/symbol=BTCUSDT/bar_type=5m/date=2024-06-01``.
    """
    if data_kind == "bar":
        if not bar_type:
            raise ValueError("bar partition requires bar_type")
        type_seg = f"bar_type={bar_type}"
    elif data_kind == "trade":
        if not data_type:
            raise ValueError("trade partition requires data_type")
        type_seg = f"data_type={data_type}"
    else:
        raise ValueError(f"unknown data_kind {data_kind!r}; expected 'bar' or 'trade'")
    return "/".join([
        f"exchange={exchange}", f"venue_type={venue_type}", f"symbol={symbol}",
        type_seg, f"date={date}",
    ])


def partition_dir(root: str | Path, **kwargs) -> Path:
    """Absolute partition directory under ``root`` (see :func:`partition_relpath`)."""
    return Path(root) / partition_relpath(**kwargs)


@dataclass(frozen=True)
class Partition:
    exchange: str
    venue_type: str
    symbol: str
    data_kind: str          # "bar" | "trade"
    date: str
    bar_type: str | None
    data_type: str | None
    path: str
    file_count: int
    total_size_bytes: int

    @property
    def type_value(self) -> str | None:
        return self.bar_type if self.data_kind == "bar" else self.data_type


class LocalDataCatalog:
    """Read-only scanner over a ``market_data`` root."""

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)

    @property
    def root(self) -> Path:
        return self._root

    def inventory(self) -> list[Partition]:
        """All partitions under the root (leaf dirs holding >=1 parquet file)."""
        if not self._root.exists():
            return []
        partitions: list[Partition] = []
        for dirpath, _dirs, files in os.walk(self._root):
            parquet = [f for f in files if f.endswith(".parquet")]
            if not parquet:
                continue
            parts = hive_partition_values(str(dirpath))
            if "date" not in parts:
                continue
            if "bar_type" in parts:
                data_kind, bar_type, data_type = "bar", parts.get("bar_type"), None
            elif "data_type" in parts:
                data_kind, bar_type, data_type = "trade", None, parts.get("data_type")
            else:
                continue  # not a recognised bar/trade partition
            total = 0
            for name in parquet:
                try:
                    total += (Path(dirpath) / name).stat().st_size
                except OSError:
                    pass
            partitions.append(Partition(
                exchange=parts.get("exchange"),
                venue_type=parts.get("venue_type"),
                symbol=parts.get("symbol"),
                data_kind=data_kind,
                date=parts.get("date"),
                bar_type=bar_type,
                data_type=data_type,
                path=str(dirpath),
                file_count=len(parquet),
                total_size_bytes=total,
            ))
        partitions.sort(key=lambda p: (p.exchange or "", p.venue_type or "", p.symbol or "",
                                       p.data_kind, p.type_value or "", p.date))
        return partitions

    def find_partitions(
        self,
        *,
        exchange: str | None = None,
        venue_type: str | None = None,
        symbol: str | None = None,
        data_kind: str | None = None,
        bar_type: str | None = None,
        data_type: str | None = None,
        date: str | None = None,
    ) -> list[Partition]:
        out = []
        for p in self.inventory():
            if exchange is not None and p.exchange != exchange:
                continue
            if venue_type is not None and p.venue_type != venue_type:
                continue
            if symbol is not None and p.symbol != symbol:
                continue
            if data_kind is not None and p.data_kind != data_kind:
                continue
            if bar_type is not None and p.bar_type != bar_type:
                continue
            if data_type is not None and p.data_type != data_type:
                continue
            if date is not None and p.date != date:
                continue
            out.append(p)
        return out

    def partition_exists(
        self,
        *,
        exchange: str,
        venue_type: str,
        symbol: str,
        data_kind: str,
        date: str,
        bar_type: str | None = None,
        data_type: str | None = None,
    ) -> bool:
        matches = self.find_partitions(
            exchange=exchange, venue_type=venue_type, symbol=symbol,
            data_kind=data_kind, bar_type=bar_type, data_type=data_type, date=date,
        )
        return any(p.file_count > 0 for p in matches)
