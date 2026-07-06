"""Offline driver: materialise a feature matrix into ``feature_data``.

Reads OHLCV bars from a ``market_data`` Hive dataset, runs the shared
``SpecFeatureEngine`` over them (the SAME operator code path the live/backtest
loop uses — so offline features == streaming features), and writes one parquet
partition per ``date=`` under ``feature_data`` (a peer of ``market_data``).

Persisted features are reusable historical data: read back by other strategies
(compute once, read many) and used as the input matrix for model training.

Example (server)::

    python build_feature_data.py \
        --symbol BTCUSDT --venue-type futures_um --freq 1m \
        --feature-set technical_v1 \
        --start 2024-07-01 --end 2026-06-29

A short pilot (validate the loop, negligible load)::

    python build_feature_data.py --symbol BTCUSDT --venue-type futures_um \
        --freq 1m --start 2024-07-01 --end 2024-07-03 --feature-set technical_v1
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path

from data_engine.adapters.dataframe_adapter import polars_to_bars
from feature_engine.feature_sets import FEATURE_SETS, build_feature_set
from feature_engine.offline import HistoricalFeatureBuilder
from feature_engine.storage.market_reader import MarketDataReader


def _read_bars(reader, *, asset_class, exchange, venue_type, symbol, data_type, freq, date):
    """Scan one date's bars and normalise the timestamp column to ``ts_event``.

    The stored market_data parquet keeps its event time in a ``ts`` column
    (Datetime); ``polars_to_bars`` expects ``ts_event`` / ``event_time_ns``. We
    alias it here so the offline path reads the same bars the backtest loader does.
    """
    df = reader.scan(
        asset_class=asset_class, exchange=exchange, venue_type=venue_type,
        symbol=symbol, data_type=data_type, freq=freq, date=date,
    )
    if "ts_event" not in df.columns and "event_time_ns" not in df.columns and "ts" in df.columns:
        df = df.rename({"ts": "ts_event"})
    return polars_to_bars(df)


def _dates_in_range(
    market_root: str,
    *,
    asset_class: str,
    exchange: str,
    venue_type: str,
    symbol: str,
    data_type: str,
    freq: str,
    start: str,
    end: str,
) -> list[str]:
    """List ``date=`` partitions present in market_data within [start, end].

    Builds the Hive partition base path directly (locked layout:
    ``asset_class/exchange/venue_type/symbol/data_type/freq/date=``) so it does
    not depend on ``layout.market_data_path``'s signature, which differs across
    feature_engine versions.
    """
    base = (
        Path(market_root)
        / f"asset_class={asset_class}"
        / f"exchange={exchange}"
        / f"venue_type={venue_type}"
        / f"symbol={symbol}"
        / f"data_type={data_type}"
        / f"freq={freq}"
    )
    if not base.exists():
        raise FileNotFoundError(f"no market_data partition at {base}")
    dates = []
    for d in sorted(Path(base).glob("date=*")):
        val = d.name.split("=", 1)[1]
        if start <= val <= end:
            dates.append(val)
    return dates


def main() -> None:
    ap = argparse.ArgumentParser(description="Materialise feature_data from market_data bars")
    ap.add_argument("--market-root", default="historical_data/market_data")
    ap.add_argument("--feature-root", default="historical_data/feature_data")
    ap.add_argument("--manifest-root", default="historical_data")
    ap.add_argument("--asset-class", default="crypto")
    ap.add_argument("--exchange", default="BINANCE")
    ap.add_argument("--venue-type", default="futures_um")
    ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument("--data-type", default="bar")
    ap.add_argument("--freq", default="1m")
    ap.add_argument("--feature-set", default="technical_v1", choices=sorted(FEATURE_SETS))
    ap.add_argument("--feature-group", default="technical")
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--mode", default="overwrite", choices=["error", "append", "overwrite"])
    args = ap.parse_args()

    specs = build_feature_set(args.feature_set)
    builder = HistoricalFeatureBuilder(specs, feature_group=args.feature_group)
    feature_names = [s.name for s in specs]
    reader = MarketDataReader(args.market_root)

    dates = _dates_in_range(
        args.market_root,
        asset_class=args.asset_class, exchange=args.exchange,
        venue_type=args.venue_type, symbol=args.symbol,
        data_type=args.data_type, freq=args.freq,
        start=args.start, end=args.end,
    )
    print(f"[feature_data] set={args.feature_set} ({len(feature_names)} features) "
          f"symbol={args.symbol} venue={args.venue_type} freq={args.freq}")
    print(f"[feature_data] {len(dates)} date partitions in [{args.start}, {args.end}]")
    if not dates:
        print("[feature_data] nothing to do")
        return

    import polars as pl  # noqa: PLC0415

    t0 = time.time()
    # 1. Read ALL dates in order into one continuous bar stream so indicator state
    #    (SMA/ATR/returns windows) carries across the midnight boundary — warm-up
    #    nulls then occur ONCE at the very start, not at the head of every day.
    all_bars = []
    for date in dates:
        all_bars.extend(_read_bars(
            reader,
            asset_class=args.asset_class, exchange=args.exchange,
            venue_type=args.venue_type, symbol=args.symbol,
            data_type=args.data_type, freq=args.freq, date=date,
        ))
    print(f"[feature_data] read {len(all_bars)} bars across {len(dates)} dates; computing...")

    # 2. One continuous pass through the shared SpecFeatureEngine.
    df = builder.build_from_events(all_bars)

    # 3. Derive the date partition from ts_event (int ns) and write per date.
    df = df.with_columns(
        pl.from_epoch(pl.col("ts_event"), time_unit="ns").dt.strftime("%Y-%m-%d").alias("_date")
    )
    total_rows = 0
    written_dates = 0
    for (date_val,), sub in df.group_by(["_date"], maintain_order=True):
        part = sub.drop("_date")
        builder.write_feature_data(
            part, feature_root=args.feature_root,
            asset_class=args.asset_class, exchange=args.exchange,
            venue_type=args.venue_type, symbol=args.symbol,
            freq=args.freq, date=date_val,
            manifest_root=args.manifest_root, mode=args.mode,
        )
        total_rows += part.height
        written_dates += 1
        if written_dates <= 3 or written_dates == len(dates) or written_dates % 50 == 0:
            print(f"  [{written_dates}/{len(dates)}] {date_val}: {part.height} rows -> {args.feature_root}")
    dt = time.time() - t0
    print(f"[feature_data] DONE {written_dates} dates, {total_rows} rows in {dt:.1f}s "
          f"-> {args.feature_root} (feature_group={args.feature_group})")


if __name__ == "__main__":
    main()
