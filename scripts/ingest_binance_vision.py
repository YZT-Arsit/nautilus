#!/usr/bin/env python3
"""CLI: Ingest Binance Vision historical market data into Hive Parquet.

Downloads bars from Binance Vision archive and writes to market_data with
Hive partitioning (exchange=BINANCE/venue_type=.../symbol=.../bar_type=.../date=...).

Examples::

    python scripts/ingest_binance_vision.py \\
        --market spot \\
        --symbol BTCUSDT \\
        --interval 1m \\
        --frequency daily \\
        --start 2024-01-01 \\
        --end 2024-01-31 \\
        --output historical_data/market_data

    # Dry run (download and normalize, but don't write)
    python scripts/ingest_binance_vision.py \\
        --market futures_um \\
        --symbol ETHUSDT \\
        --interval 1h \\
        --frequency monthly \\
        --start 2024-01 \\
        --end 2024-06 \\
        --dry-run

    # Overwrite existing data
    python scripts/ingest_binance_vision.py \\
        --market spot \\
        --symbol BTCUSDT \\
        --interval 5m \\
        --frequency daily \\
        --start 2024-06-01 \\
        --end 2024-06-30 \\
        --output historical_data/market_data \\
        --overwrite
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from feature_engine.data_sources import BinanceVisionImporter  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    """Main entry point."""
    ap = argparse.ArgumentParser(
        description="Ingest Binance Vision historical market data",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="For more examples, see script docstring.",
    )

    # Required arguments
    ap.add_argument(
        "--market",
        required=True,
        choices=["spot", "futures_um", "futures_cm"],
        help="Market type",
    )
    ap.add_argument(
        "--symbol",
        required=True,
        help="Trading pair symbol (e.g., BTCUSDT)",
    )
    ap.add_argument(
        "--data-type",
        default="klines",
        choices=["klines", "aggTrades"],
        help="Data type to ingest (default: klines)",
    )
    ap.add_argument(
        "--interval",
        required=False,
        default=None,
        help="Bar interval (e.g., 1m, 5m, 15m, 1h, 4h, 1d). Required for klines.",
    )
    ap.add_argument(
        "--frequency",
        required=True,
        choices=["monthly", "daily"],
        help="Download frequency (monthly or daily files)",
    )
    ap.add_argument(
        "--start",
        required=True,
        help="Start date (YYYY-MM for monthly, YYYY-MM-DD for daily)",
    )
    ap.add_argument(
        "--end",
        required=True,
        help="End date (YYYY-MM for monthly, YYYY-MM-DD for daily)",
    )

    # Optional arguments
    ap.add_argument(
        "--output",
        default="historical_data/market_data",
        help="Output root directory for Hive Parquet (default: historical_data/market_data)",
    )
    ap.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="Download timeout in seconds (default: 30)",
    )
    ap.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing partitions",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Download and normalize data but don't write to disk",
    )

    args = ap.parse_args(argv)

    try:
        # Validate date formats
        if args.frequency == "monthly":
            try:
                datetime.strptime(args.start, "%Y-%m")
                datetime.strptime(args.end, "%Y-%m")
            except ValueError as e:
                print(f"Error: Invalid monthly date format. Expected YYYY-MM. {e}", file=sys.stderr)
                return 1
        else:  # daily
            try:
                datetime.strptime(args.start, "%Y-%m-%d")
                datetime.strptime(args.end, "%Y-%m-%d")
            except ValueError as e:
                print(f"Error: Invalid daily date format. Expected YYYY-MM-DD. {e}", file=sys.stderr)
                return 1

        is_trades = args.data_type == "aggTrades"
        if not is_trades and not args.interval:
            print("Error: --interval is required for --data-type klines", file=sys.stderr)
            return 1

        print(f"[ingest_binance_vision] Starting import:")
        print(f"  Data type: {args.data_type}")
        print(f"  Market: {args.market}")
        print(f"  Symbol: {args.symbol}")
        if not is_trades:
            print(f"  Interval: {args.interval}")
        print(f"  Frequency: {args.frequency}")
        print(f"  Date range: {args.start} to {args.end}")
        print(f"  Output: {args.output}")
        if args.dry_run:
            print(f"  Mode: DRY RUN (no disk write)")
        if args.overwrite:
            print(f"  Mode: OVERWRITE")
        print()

        # Import data
        importer = BinanceVisionImporter(timeout=args.timeout)
        if is_trades:
            df = importer.import_aggtrades_period(
                market=args.market,
                symbol=args.symbol,
                frequency=args.frequency,
                start_date=args.start,
                end_date=args.end,
            )
            unit_label, price_field = "trades", "price"
        else:
            df = importer.import_period(
                market=args.market,
                symbol=args.symbol,
                interval=args.interval,
                frequency=args.frequency,
                start_date=args.start,
                end_date=args.end,
            )
            unit_label, price_field = "bars", "close"

        print(f"[ingest_binance_vision] Imported {df.height} {unit_label}")
        if df.height == 0:
            print(f"Warning: No {unit_label} imported", file=sys.stderr)
            return 1

        # Show sample
        print(f"  Date range: {df['ts'].min()} to {df['ts'].max()}")
        print(f"  Price range: {df[price_field].min()} to {df[price_field].max()}")
        print()

        if args.dry_run:
            print("[ingest_binance_vision] Dry run complete - skipping disk write")
            return 0

        # Write to Hive Parquet
        try:
            import polars as pl  # noqa: PLC0415
            import pyarrow as pa  # noqa: PLC0415
            import pyarrow.dataset as ds  # noqa: PLC0415
        except ImportError as e:
            print(f"Error: polars and pyarrow required for disk write. {e}", file=sys.stderr)
            return 1

        output_root = Path(args.output)
        output_root.mkdir(parents=True, exist_ok=True)

        # Extract the date partition column from the timestamp.
        df_with_date = df.with_columns([
            pl.col("ts").dt.strftime("%Y-%m-%d").alias("date")
        ])
        if is_trades:
            # Trade data has no bar_type; partition by data_type instead.
            df_with_date = df_with_date.with_columns(
                pl.lit("aggTrades").alias("data_type")
            )
            partitioning_cols = ["exchange", "venue_type", "symbol", "data_type", "date"]
        else:
            partitioning_cols = ["exchange", "venue_type", "symbol", "bar_type", "date"]

        # Convert to PyArrow table
        table = pa.table({
            col: df_with_date[col].to_list() for col in df_with_date.columns
        })
        existing_behavior = "overwrite_or_ignore" if args.overwrite else "error"

        print(f"[ingest_binance_vision] Writing to {output_root}")
        written_paths = []

        def _visit(f: "ds.WrittenFile") -> None:
            written_paths.append(Path(f.path))

        ds.write_dataset(
            table,
            base_dir=str(output_root),
            format="parquet",
            partitioning=partitioning_cols,
            partitioning_flavor="hive",
            existing_data_behavior=existing_behavior,
            basename_template="part-{i}.parquet",
            file_visitor=_visit,
        )

        print(f"  Wrote {len(written_paths)} file(s)")
        for path in written_paths[:3]:
            print(f"    -> {path}")
        if len(written_paths) > 3:
            print(f"    ... and {len(written_paths) - 3} more")

        print(f"\n[ingest_binance_vision] Complete")
        return 0

    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Error: {type(e).__name__}: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())