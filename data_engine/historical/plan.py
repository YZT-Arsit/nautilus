"""Download planning over the local catalog -- never touches the network.

Given a symbol/date range, classify each partition as existing or missing and
decide which to download given ``skip-existing`` / ``overwrite`` semantics.

Pure stdlib + :class:`LocalDataCatalog`; no ``pyarrow``/``polars``/network.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from data_engine.historical.catalog import LocalDataCatalog


def generate_dates(start: str, end: str, frequency: str = "daily") -> list[str]:
    """Inclusive date strings between ``start`` and ``end``.

    daily -> ``YYYY-MM-DD`` per day; monthly -> ``YYYY-MM`` per month.
    """
    if frequency == "daily":
        s = datetime.strptime(start, "%Y-%m-%d")
        e = datetime.strptime(end, "%Y-%m-%d")
        if e < s:
            raise ValueError(f"end {end} is before start {start}")
        out, cur = [], s
        while cur <= e:
            out.append(cur.strftime("%Y-%m-%d"))
            cur += timedelta(days=1)
        return out
    if frequency == "monthly":
        s = datetime.strptime(start, "%Y-%m")
        e = datetime.strptime(end, "%Y-%m")
        if e < s:
            raise ValueError(f"end {end} is before start {start}")
        out, cur = [], s
        while cur <= e:
            out.append(cur.strftime("%Y-%m"))
            cur = cur.replace(year=cur.year + 1, month=1) if cur.month == 12 \
                else cur.replace(month=cur.month + 1)
        return out
    raise ValueError(f"unsupported frequency {frequency!r}; expected 'daily' or 'monthly'")


@dataclass
class PlannedPartition:
    exchange: str
    venue_type: str
    symbol: str
    data_kind: str           # "bar" | "trade"
    date: str
    bar_type: str | None = None
    data_type: str | None = None
    exists: bool = False
    action: str = "download"  # "download" | "skip_existing"


@dataclass
class DownloadPlan:
    existing: list[PlannedPartition] = field(default_factory=list)
    missing: list[PlannedPartition] = field(default_factory=list)
    skipped_existing: list[PlannedPartition] = field(default_factory=list)
    planned_downloads: list[PlannedPartition] = field(default_factory=list)

    def summary(self) -> dict[str, int]:
        return {
            "existing": len(self.existing),
            "missing": len(self.missing),
            "skipped_existing": len(self.skipped_existing),
            "planned_downloads": len(self.planned_downloads),
        }


def build_plan(
    *,
    exchange: str,
    venue_type: str,
    symbols,
    data_kind: str,
    start: str,
    end: str,
    root: str | None = None,
    catalog: LocalDataCatalog | None = None,
    bar_type: str | None = None,
    data_type: str | None = None,
    frequency: str = "daily",
    overwrite: bool = False,
) -> DownloadPlan:
    """Build a :class:`DownloadPlan`. Read-only: only the local catalog and the
    parameters decide existing/missing. No network, no writes.

    ``overwrite=True`` puts existing partitions into ``planned_downloads`` too;
    otherwise they go to ``skipped_existing``.
    """
    if catalog is None:
        if root is None:
            raise ValueError("build_plan requires either 'root' or 'catalog'")
        catalog = LocalDataCatalog(root)
    if data_kind == "bar":
        if not bar_type:
            raise ValueError("bar plan requires bar_type")
        data_type = None  # bars never key on data_type
    elif data_kind == "trade":
        bar_type = None   # trades never key on bar_type
        if data_type is None:
            data_type = "aggTrades"
    else:
        raise ValueError(f"unknown data_kind {data_kind!r}; expected 'bar' or 'trade'")
    if isinstance(symbols, str):
        symbols = [symbols]

    dates = generate_dates(start, end, frequency)
    plan = DownloadPlan()
    for symbol in symbols:
        for date in dates:
            exists = catalog.partition_exists(
                exchange=exchange, venue_type=venue_type, symbol=symbol,
                data_kind=data_kind, bar_type=bar_type, data_type=data_type, date=date,
            )
            pp = PlannedPartition(
                exchange=exchange, venue_type=venue_type, symbol=symbol,
                data_kind=data_kind, date=date, bar_type=bar_type, data_type=data_type,
                exists=exists,
            )
            if exists:
                plan.existing.append(pp)
                if overwrite:
                    pp.action = "download"
                    plan.planned_downloads.append(pp)
                else:
                    pp.action = "skip_existing"
                    plan.skipped_existing.append(pp)
            else:
                pp.action = "download"
                plan.missing.append(pp)
                plan.planned_downloads.append(pp)
    return plan
