"""Per-date historical downloader for Binance Vision -> local Hive Parquet.

Reuses the existing Binance Vision adapter (``BinanceVisionImporter`` +
``build_*_url``); it does **not** re-implement any kline/aggTrades parser.  Each
date is fetched and written as its own partition, so skip-existing and failure
isolation work per date.

The network fetch is an injectable seam (``fetcher``): the default hits Binance
Vision, while tests pass a fake that returns a small in-memory table -- so the
test suite needs no network.

Imports no ``nautilus_trader``.  pyarrow/polars and the Binance adapter are
imported lazily (only the default fetcher / disk write touch them).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from data_engine.historical.catalog import LocalDataCatalog
from data_engine.historical.manifest import Manifest, ManifestRecord
from data_engine.historical.plan import build_plan

# Fetcher(*, data_kind, market, symbol, type_, frequency, date, timeout)
#   -> (pyarrow.Table, source_url)
Fetcher = Callable[..., Any]


def default_fetcher(*, data_kind: str, market: str, symbol: str, type_: str,
                    frequency: str, date: str, timeout: int):
    """Real fetcher: one date via the Binance Vision adapter -> (pa.Table, url)."""
    import pyarrow as pa  # noqa: PLC0415
    from feature_engine.data_sources.binance_vision import (  # noqa: PLC0415
        BinanceVisionImporter,
        build_binance_vision_aggtrades_url,
        build_binance_vision_kline_url,
    )

    importer = BinanceVisionImporter(timeout=timeout)
    if data_kind == "bar":
        df = importer.import_period(market, symbol, type_, frequency, date, date)
        url = build_binance_vision_kline_url(market, symbol, type_, frequency, date)
    else:
        df = importer.import_aggtrades_period(market, symbol, frequency, date, date)
        url = build_binance_vision_aggtrades_url(market, symbol, frequency, date)
    table = pa.table({c: df[c].to_list() for c in df.columns})
    return table, url


@dataclass
class DownloadResult:
    downloaded: list[dict[str, Any]] = field(default_factory=list)
    skipped_existing: list[dict[str, Any]] = field(default_factory=list)
    failed: list[dict[str, Any]] = field(default_factory=list)

    def summary(self) -> dict[str, int]:
        return {
            "downloaded": len(self.downloaded),
            "skipped_existing": len(self.skipped_existing),
            "failed": len(self.failed),
        }


class BinanceVisionHistoricalDownloader:
    """Download missing partitions per date; skip-existing by default."""

    def __init__(self, root: str | Path, *, fetcher: Fetcher | None = None,
                 timeout: int = 30, frequency: str = "daily") -> None:
        self._root = Path(root)
        self._fetcher = fetcher or default_fetcher
        self._timeout = timeout
        self._frequency = frequency
        self._manifest = Manifest(root)
        self._catalog = LocalDataCatalog(root)

    @property
    def manifest(self) -> Manifest:
        return self._manifest

    def download(
        self,
        *,
        exchange: str,
        venue_type: str,
        symbol,
        data_kind: str,
        start: str,
        end: str,
        bar_type: str | None = None,
        data_type: str | None = None,
        overwrite: bool = False,
        validate: bool = True,
        now: str | None = None,
    ) -> tuple[DownloadResult, Any]:
        """Execute the plan: skip existing (default), download missing/overwritten.

        ``market`` is taken as ``venue_type`` (spot/futures_um/futures_cm).
        Returns ``(DownloadResult, DownloadPlan)``.
        """
        if data_kind == "bar":
            data_type = None  # bars never key on data_type
        elif data_kind == "trade":
            bar_type = None   # trades never key on bar_type
            if data_type is None:
                data_type = "aggTrades"
        plan = build_plan(
            catalog=self._catalog, exchange=exchange, venue_type=venue_type,
            symbols=symbol, data_kind=data_kind, bar_type=bar_type, data_type=data_type,
            start=start, end=end, frequency=self._frequency, overwrite=overwrite,
        )
        result = DownloadResult()

        for pp in plan.skipped_existing:
            rec = self._manifest.append(ManifestRecord(
                status="skipped_existing", exchange=exchange, venue_type=venue_type,
                symbol=pp.symbol, data_kind=data_kind, bar_type=bar_type,
                data_type=data_type, date=pp.date, overwrite=overwrite,
            ), now=now)
            result.skipped_existing.append(rec)

        for pp in plan.planned_downloads:
            try:
                rec = self._download_one(
                    pp, exchange=exchange, venue_type=venue_type, data_kind=data_kind,
                    bar_type=bar_type, data_type=data_type, overwrite=overwrite,
                    validate=validate, now=now,
                )
                result.downloaded.append(rec)
            except Exception as exc:  # isolate failures; existing data untouched
                rec = self._manifest.append(ManifestRecord(
                    status="failed", exchange=exchange, venue_type=venue_type,
                    symbol=pp.symbol, data_kind=data_kind, bar_type=bar_type,
                    data_type=data_type, date=pp.date, overwrite=overwrite,
                    error=f"{type(exc).__name__}: {exc}",
                ), now=now)
                result.failed.append(rec)

        return result, plan

    def _download_one(self, pp, *, exchange, venue_type, data_kind, bar_type,
                      data_type, overwrite, validate, now) -> dict[str, Any]:
        # Fetch FIRST so a failing fetch never reaches the parquet write path.
        market = venue_type
        type_ = bar_type if data_kind == "bar" else data_type
        table, url = self._fetcher(
            data_kind=data_kind, market=market, symbol=pp.symbol, type_=type_,
            frequency=self._frequency, date=pp.date, timeout=self._timeout,
        )

        import pyarrow as pa  # noqa: PLC0415
        import pyarrow.compute as pc  # noqa: PLC0415
        import pyarrow.dataset as ds  # noqa: PLC0415

        n = table.num_rows
        cols = {name: table.column(name) for name in table.column_names}
        cols["date"] = pa.array([pp.date] * n)
        if data_kind == "trade":
            cols["data_type"] = pa.array([data_type] * n)
            part_cols = ["exchange", "venue_type", "symbol", "data_type", "date"]
        else:
            part_cols = ["exchange", "venue_type", "symbol", "bar_type", "date"]
        out_table = pa.table(cols)

        written: list[str] = []
        ds.write_dataset(
            out_table, base_dir=str(self._root), format="parquet",
            partitioning=part_cols, partitioning_flavor="hive",
            existing_data_behavior="overwrite_or_ignore",
            basename_template="part-{i}.parquet",
            file_visitor=lambda f: written.append(f.path),
        )
        local_path = written[0] if written else None

        ts_min = ts_max = None
        if "ts" in table.column_names and n > 0:
            ts = table.column("ts")
            ts_min, ts_max = str(pc.min(ts).as_py()), str(pc.max(ts).as_py())
        size = None
        if local_path and Path(local_path).exists():
            size = Path(local_path).stat().st_size

        if validate:
            from data_engine.historical.validators import validate_partition  # noqa: PLC0415
            v = validate_partition(
                root=self._root, exchange=exchange, venue_type=venue_type,
                symbol=pp.symbol, data_kind=data_kind, bar_type=bar_type,
                data_type=data_type, date=pp.date,
            )
            if not v.ok:
                raise ValueError(f"post-download validation failed: {v.errors}")

        return self._manifest.append(ManifestRecord(
            status="downloaded", exchange=exchange, venue_type=venue_type,
            symbol=pp.symbol, data_kind=data_kind, bar_type=bar_type,
            data_type=data_type, date=pp.date, source_url=url,
            local_path=str(local_path) if local_path else None, row_count=n,
            ts_min=ts_min, ts_max=ts_max, file_size_bytes=size, overwrite=overwrite,
        ), now=now)
