#!/usr/bin/env python3
"""CLI for the historical data manager / local cache.

Subcommands:

  inventory  scan the local market_data root (read-only)
  plan       classify date range as existing/missing (read-only, no network)
  verify     read-only validate cached partitions
  download   download missing partitions (skip-existing by default)

Only ``download`` accesses the network (Binance Vision archive).  ``inventory`` /
``plan`` / ``verify`` are read-only.  No Nautilus, no live feed, no backtest.

Examples::

    python scripts/manage_historical_data.py inventory --root historical_data/market_data

    python scripts/manage_historical_data.py plan --exchange BINANCE --venue-type spot \\
        --symbol BTCUSDT --data-kind bar --bar-type 5m --start 2024-06-01 --end 2024-06-03 \\
        --root historical_data/market_data

    python scripts/manage_historical_data.py verify --exchange BINANCE --venue-type spot \\
        --symbol BTCUSDT --data-kind trade --data-type aggTrades \\
        --start 2024-06-01 --end 2024-06-01 --root historical_data/market_data

    python scripts/manage_historical_data.py download --exchange BINANCE --venue-type spot \\
        --symbol BTCUSDT --data-kind trade --data-type aggTrades \\
        --start 2024-06-01 --end 2024-06-03 --root historical_data/market_data --skip-existing
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from data_engine.historical import (  # noqa: E402
    BinanceVisionHistoricalDownloader,
    LocalDataCatalog,
    Manifest,
    ManifestRecord,
    build_plan,
    generate_dates,
)


def _add_selection_args(p: argparse.ArgumentParser, *, require_type: bool = True) -> None:
    p.add_argument("--exchange", default="BINANCE")
    p.add_argument("--venue-type", required=True, choices=["spot", "futures_um", "futures_cm"])
    p.add_argument("--symbol", required=True, action="append",
                   help="Trading pair (repeatable, e.g. --symbol BTCUSDT --symbol ETHUSDT)")
    p.add_argument("--data-kind", required=True, choices=["bar", "trade"])
    p.add_argument("--bar-type", default=None, help="e.g. 5m (required for --data-kind bar)")
    p.add_argument("--data-type", default="aggTrades", help="trade data type (default aggTrades)")
    p.add_argument("--start", required=True, help="YYYY-MM-DD (daily) or YYYY-MM (monthly)")
    p.add_argument("--end", required=True, help="YYYY-MM-DD (daily) or YYYY-MM (monthly)")
    p.add_argument("--frequency", default="daily", choices=["daily", "monthly"])
    p.add_argument("--root", default="historical_data/market_data")


def _check_bar_type(args) -> None:
    if args.data_kind == "bar" and not args.bar_type:
        raise SystemExit("Error: --bar-type is required for --data-kind bar")


def cmd_inventory(args) -> int:
    cat = LocalDataCatalog(args.root)
    parts = cat.inventory()
    print(f"[inventory] root={args.root}  partitions={len(parts)}")
    for p in parts:
        t = f"bar_type={p.bar_type}" if p.data_kind == "bar" else f"data_type={p.data_type}"
        print(f"  {p.exchange}/{p.venue_type}/{p.symbol}/{t}/date={p.date}"
              f"  files={p.file_count} size={p.total_size_bytes}")
    return 0


def cmd_plan(args) -> int:
    _check_bar_type(args)
    plan = build_plan(
        root=args.root, exchange=args.exchange, venue_type=args.venue_type,
        symbols=args.symbol, data_kind=args.data_kind, bar_type=args.bar_type,
        data_type=args.data_type, start=args.start, end=args.end,
        frequency=args.frequency, overwrite=False,
    )
    print(f"[plan] {plan.summary()}")
    for pp in plan.planned_downloads:
        print(f"  DOWNLOAD {pp.symbol} {pp.data_kind} {pp.date}")
    for pp in plan.skipped_existing:
        print(f"  SKIP_EXISTING {pp.symbol} {pp.data_kind} {pp.date}")
    return 0


def cmd_verify(args) -> int:
    _check_bar_type(args)
    from data_engine.historical import validate_partition  # lazy: needs pyarrow
    manifest = Manifest(args.root) if args.write_manifest else None
    dates = generate_dates(args.start, args.end, args.frequency)
    all_ok = True
    for symbol in args.symbol:
        for date in dates:
            res = validate_partition(
                root=args.root, exchange=args.exchange, venue_type=args.venue_type,
                symbol=symbol, data_kind=args.data_kind, bar_type=args.bar_type,
                data_type=args.data_type, date=date,
            )
            status = "OK" if res.ok else "FAIL"
            print(f"[verify] {status} {symbol} {args.data_kind} {date} "
                  f"rows={res.row_count} {res.details if res.ok else res.errors}")
            if not res.ok:
                all_ok = False
            if manifest is not None:
                manifest.append(ManifestRecord(
                    status="verified" if res.ok else "failed",
                    exchange=args.exchange, venue_type=args.venue_type, symbol=symbol,
                    data_kind=args.data_kind, bar_type=args.bar_type,
                    data_type=args.data_type, date=date, row_count=res.row_count,
                    error=None if res.ok else "; ".join(res.errors),
                ))
    return 0 if all_ok else 1


def cmd_download(args) -> int:
    _check_bar_type(args)
    dl = BinanceVisionHistoricalDownloader(args.root, timeout=args.timeout, frequency=args.frequency)
    result, plan = dl.download(
        exchange=args.exchange, venue_type=args.venue_type, symbol=args.symbol,
        data_kind=args.data_kind, bar_type=args.bar_type, data_type=args.data_type,
        start=args.start, end=args.end, overwrite=args.overwrite,
        validate=not args.no_validate,
    )
    print(f"[download] plan={plan.summary()} result={result.summary()}")
    for rec in result.downloaded:
        print(f"  DOWNLOADED {rec['symbol']} {rec['data_kind']} {rec['date']} "
              f"rows={rec['row_count']} size={rec['file_size_bytes']}")
    for rec in result.skipped_existing:
        print(f"  SKIPPED_EXISTING {rec['symbol']} {rec['data_kind']} {rec['date']}")
    for rec in result.failed:
        print(f"  FAILED {rec['symbol']} {rec['data_kind']} {rec['date']} {rec['error']}")
    return 1 if result.failed else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Historical data manager / local cache")
    sub = ap.add_subparsers(dest="command", required=True)

    p_inv = sub.add_parser("inventory", help="scan local market_data (read-only)")
    p_inv.add_argument("--root", default="historical_data/market_data")
    p_inv.set_defaults(func=cmd_inventory)

    p_plan = sub.add_parser("plan", help="classify existing vs missing (read-only)")
    _add_selection_args(p_plan)
    p_plan.set_defaults(func=cmd_plan)

    p_ver = sub.add_parser("verify", help="read-only validate cached partitions")
    _add_selection_args(p_ver)
    p_ver.add_argument("--write-manifest", action="store_true",
                       help="record verified/failed in the manifest (default off; verify is read-only)")
    p_ver.set_defaults(func=cmd_verify)

    p_dl = sub.add_parser("download", help="download missing partitions (skip-existing default)")
    _add_selection_args(p_dl)
    p_dl.add_argument("--overwrite", action="store_true", help="re-download existing partitions")
    p_dl.add_argument("--skip-existing", action="store_true",
                      help="explicit skip-existing (already the default)")
    p_dl.add_argument("--no-validate", action="store_true", help="skip post-download validation")
    p_dl.add_argument("--timeout", type=int, default=30)
    p_dl.set_defaults(func=cmd_download)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
