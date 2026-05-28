"""Bridge: nautilus_catalog QuoteTicks → quant_feature_engine Hive raw bars.

Reads QuoteTicks for a single instrument out of the verified server-side
catalog::

    D:\\QuanHub\\DataHome\\DataTrans\\nautilus_catalog\\cffex_l1_quote\\...

aggregates them to 1-minute mid-price OHLCV-like bars via
``nautilus_ext.aggregation.TickToBarAggregator`` (the **same** aggregator the
live strategy pipeline uses, so live and historical bar definitions stay
identical), groups them by trading date, and writes one Hive-partitioned
Parquet file per (asset_class, exchange, frequency, trading_date) under the
``quant_feature_engine`` raw layout::

    {output_root}/asset_class=futures/exchange=CFFEX/frequency=1m/trading_date=YYYY-MM-DD/

Volume note
-----------
``TickToBarAggregator`` counts ticks per minute as a synthetic volume; the
catalog stores L1 quotes, not trades. Per SKILL.md this is acceptable for
engineering validation but not for performance conclusions. The script emits
a ``volume_type`` log line so downstream consumers know what they're getting.

Usage
-----
::

    python internal_examples/build_qfe_raw_from_catalog.py \\
        --catalog "D:\\QuanHub\\DataHome\\DataTrans\\nautilus_catalog" \\
        --instrument-id IH2303.CFFEX \\
        --output-root D:\\nautilus\\data\\raw \\
        --asset-class futures --exchange CFFEX --frequency 1m

The script is idempotent: re-running with the same arguments overwrites the
target partition (``ParquetStore`` writes with ``overwrite_or_ignore``).
"""
from __future__ import annotations

import argparse
import logging
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

# Make the in-repo packages importable when run directly.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import polars as pl  # noqa: E402

from nautilus_ext.aggregation.tick_to_bar import (  # noqa: E402
    BarAggregationConfig,
    TickToBarAggregator,
)
from nautilus_ext.data.catalog_quote_reader import CatalogQuoteTickSource  # noqa: E402
from nautilus_ext.data.events import BarEvent  # noqa: E402
from quant_feature_engine.storage.parquet_store import ParquetStore  # noqa: E402

logger = logging.getLogger("build_qfe_raw_from_catalog")


def _bars_for_instrument(
    catalog: Path,
    instrument_id: str,
    interval: str,
    start: str | None,
    end: str | None,
    limit: int | None,
) -> list[BarEvent]:
    """Read every quote for ``instrument_id`` and emit minute bars in order."""
    source = CatalogQuoteTickSource(
        catalog_path=str(catalog),
        instrument_id=instrument_id,
        start=start,
        end=end,
        limit=limit,
    )
    aggregator = TickToBarAggregator(BarAggregationConfig(interval=interval))
    bars: list[BarEvent] = []
    n_events = 0
    for event in source.iter_events():
        n_events += 1
        emitted = aggregator.update(event)
        if emitted is not None:
            bars.append(emitted)
    final = aggregator.flush()
    if final is not None:
        bars.append(final)
    logger.info(
        "Consumed %d quote ticks → %d bars for %s (interval=%s, volume_type=%s)",
        n_events,
        len(bars),
        instrument_id,
        interval,
        bars[0].volume_type if bars else "n/a",
    )
    return bars


def _bars_to_frame(bars: list[BarEvent]) -> pl.DataFrame:
    """Convert ``BarEvent`` records to the canonical raw-bar Polars frame.

    The framework's :mod:`quant_feature_engine.core.schema.BAR_SCHEMA` expects
    ``symbol, ts_event, ts_init, open, high, low, close, volume, turnover,
    bid, ask``. We synthesise ``turnover = close * volume`` (volume here is a
    tick count so turnover is also synthetic — preserved for column-shape
    parity) and leave ``bid``/``ask`` null because the aggregator collapses to
    mid price.
    """
    if not bars:
        return pl.DataFrame()
    rows = [
        {
            "symbol": b.instrument_id,
            "ts_event": b.ts_event,
            "ts_init": b.ts_init,
            "open": b.open,
            "high": b.high,
            "low": b.low,
            "close": b.close,
            "volume": float(b.volume),
            "turnover": float(b.close * b.volume),
            "bid": None,
            "ask": None,
        }
        for b in bars
    ]
    df = pl.DataFrame(
        rows,
        schema={
            "symbol": pl.String,
            "ts_event": pl.Datetime("us", "UTC"),
            "ts_init": pl.Datetime("us", "UTC"),
            "open": pl.Float64,
            "high": pl.Float64,
            "low": pl.Float64,
            "close": pl.Float64,
            "volume": pl.Float64,
            "turnover": pl.Float64,
            "bid": pl.Float64,
            "ask": pl.Float64,
        },
    )
    return df


def _group_by_trading_date(df: pl.DataFrame) -> dict[str, pl.DataFrame]:
    """Split a frame into one sub-frame per UTC trading_date."""
    if df.is_empty():
        return {}
    tagged = df.with_columns(pl.col("ts_event").dt.date().alias("_date"))
    out: dict[str, pl.DataFrame] = {}
    for part in tagged.partition_by("_date", maintain_order=True):
        date_str = part["_date"][0].isoformat()
        out[date_str] = part.drop("_date")
    return out


def _write_partitions(
    store: ParquetStore,
    by_date: dict[str, pl.DataFrame],
    *,
    asset_class: str,
    exchange: str,
    frequency: str,
) -> list[str]:
    """Write one Hive partition per trading_date. Returns the list of dates."""
    written: list[str] = []
    for date_str, sub in by_date.items():
        store.write(
            sub,
            partition_values={
                "asset_class": asset_class,
                "exchange": exchange,
                "frequency": frequency,
                "trading_date": date_str,
            },
        )
        written.append(date_str)
        logger.info(
            "  wrote %d bars to partition %s",
            sub.height,
            f"asset_class={asset_class}/exchange={exchange}/"
            f"frequency={frequency}/trading_date={date_str}",
        )
    return written


def build(
    *,
    catalog: Path,
    instrument_id: str,
    output_root: Path,
    asset_class: str,
    exchange: str,
    frequency: str,
    interval: str,
    start: str | None = None,
    end: str | None = None,
    limit: int | None = None,
) -> dict:
    """Programmatic entry point. Returns a small report dict for tests/logs."""
    bars = _bars_for_instrument(catalog, instrument_id, interval, start, end, limit)
    df = _bars_to_frame(bars)
    if df.is_empty():
        logger.warning("No bars produced for %s; nothing written.", instrument_id)
        return {
            "instrument_id": instrument_id,
            "bars": 0,
            "partitions": [],
            "output_root": str(output_root),
        }

    store = ParquetStore(
        output_root,
        partition_cols=("asset_class", "exchange", "frequency", "trading_date"),
    )
    by_date = _group_by_trading_date(df)
    written = _write_partitions(
        store,
        by_date,
        asset_class=asset_class,
        exchange=exchange,
        frequency=frequency,
    )
    return {
        "instrument_id": instrument_id,
        "bars": df.height,
        "partitions": written,
        "output_root": str(output_root),
        "first_ts": df["ts_event"].min().isoformat() if df.height else None,
        "last_ts": df["ts_event"].max().isoformat() if df.height else None,
    }


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--catalog", required=True, type=Path,
                   help="Path to nautilus_catalog root (contains cffex_l1_quote/, ...).")
    p.add_argument("--instrument-id", required=True,
                   help="e.g. IH2303.CFFEX")
    p.add_argument("--output-root", required=True, type=Path,
                   help="Hive root for raw bars; one directory per partition will be created.")
    p.add_argument("--asset-class", default="futures")
    p.add_argument("--exchange", default="CFFEX")
    p.add_argument("--frequency", default="1m",
                   help="Logical label used in the Hive partition path.")
    p.add_argument("--interval", default="1min",
                   help="pandas-style interval string for the aggregator (e.g. '1min', '5min').")
    p.add_argument("--start", default=None, help="UTC ISO timestamp, inclusive.")
    p.add_argument("--end", default=None, help="UTC ISO timestamp, inclusive.")
    p.add_argument("--limit", type=int, default=None,
                   help="Cap on quote ticks consumed (useful for smoke tests).")
    p.add_argument("--quiet", action="store_true")
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    logging.basicConfig(
        level=logging.WARNING if args.quiet else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    report = build(
        catalog=args.catalog,
        instrument_id=args.instrument_id,
        output_root=args.output_root,
        asset_class=args.asset_class,
        exchange=args.exchange,
        frequency=args.frequency,
        interval=args.interval,
        start=args.start,
        end=args.end,
        limit=args.limit,
    )
    print(
        f"OK instrument={report['instrument_id']} bars={report['bars']} "
        f"partitions={len(report['partitions'])} "
        f"window=[{report.get('first_ts')} .. {report.get('last_ts')}]"
    )
    return 0 if report["bars"] > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
