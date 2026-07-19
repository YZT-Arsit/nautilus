#!/usr/bin/env python3
"""Read-only multi-day **bar loader smoke** for the Hive-partitioned cache.

Validates that a date range of 1m (or other) bars for one instrument can be
read **stably** through the canonical ``data_engine.load_events`` path
(``mode="hive_parquet_bars"``) and reports integrity statistics.

It is strictly **read-only**: no download, no parquet/manifest write, no
backtest, no strategy run, no Nautilus, no network, no account, no orders.
The only data access is one pass through ``load_events`` (pyarrow under the
hood); everything else is in-memory arithmetic.

    python scripts/run_bar_loader_smoke.py \\
        --root historical_data/market_data \\
        --exchange BINANCE --venue-type spot --symbol BTCUSDT \\
        --bar-type 1m --start 2024-06-17 --end 2026-06-16

The loader selects fragments by equality filters (exchange/venue_type/symbol/
bar_type) and reads them in one pass; this script then restricts the events to
the requested ``[start, end]`` UTC-date window and computes the report.
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from data_engine import load_events  # noqa: E402

_NS_PER_DAY = 86_400_000_000_000

# bars expected per UTC day for common bar types (used for expected-row math).
_BARS_PER_DAY = {
    "1m": 1440, "3m": 480, "5m": 288, "15m": 96, "30m": 48,
    "1h": 24, "2h": 12, "4h": 6, "6h": 4, "8h": 3, "12h": 2, "1d": 1,
}


def bars_per_day(bar_type: str):
    """Expected bars per UTC day for ``bar_type`` (or ``None`` if unknown)."""
    return _BARS_PER_DAY.get(bar_type)


def parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def expected_day_count(start: date, end: date) -> int:
    """Inclusive day count between ``start`` and ``end``."""
    if end < start:
        raise ValueError(f"end {end} is before start {start}")
    return (end - start).days + 1


def date_range(start: date, end: date) -> list[str]:
    """Inclusive list of ``YYYY-MM-DD`` strings."""
    n = expected_day_count(start, end)
    return [(start + timedelta(days=i)).isoformat() for i in range(n)]


def ns_to_utc_date(event_time_ns: int) -> str:
    """UTC ``YYYY-MM-DD`` for an epoch-nanosecond timestamp."""
    return datetime.fromtimestamp(event_time_ns // 1_000_000_000, tz=timezone.utc).date().isoformat()


def ns_to_iso(event_time_ns: int) -> str:
    secs, rem = divmod(int(event_time_ns), 1_000_000_000)
    base = datetime.fromtimestamp(secs, tz=timezone.utc).replace(tzinfo=None).isoformat()
    return f"{base}.{rem:09d}Z"


def _is_null(v) -> bool:
    return v is None or (isinstance(v, float) and v != v)  # None or NaN


def compute_bar_stats(events, start: date, end: date, *, per_day_expected):
    """Pure integrity statistics over an in-memory list of bar events.

    ``events`` only needs ``event_time_ns`` plus ``open/high/low/close/volume``
    attributes (real :class:`BarEvent` or any stand-in). No pyarrow, no network.
    Events are filtered to the inclusive ``[start, end]`` UTC-date window first.
    """
    start_s, end_s = start.isoformat(), end.isoformat()
    in_range = [e for e in events if start_s <= ns_to_utc_date(e.event_time_ns) <= end_s]
    in_range.sort(key=lambda e: e.event_time_ns)

    total = len(in_range)
    day_count = expected_day_count(start, end)
    expected_rows = day_count * per_day_expected if per_day_expected else None

    # per-day counts
    per_day: dict[str, int] = {}
    for e in in_range:
        d = ns_to_utc_date(e.event_time_ns)
        per_day[d] = per_day.get(d, 0) + 1

    # monotonic (non-decreasing) + duplicate timestamps over the sorted list
    monotonic = True
    duplicate_ts = 0
    prev = None
    for e in in_range:
        ts = e.event_time_ns
        if prev is not None:
            if ts < prev:
                monotonic = False  # cannot happen post-sort, but assert the invariant
            if ts == prev:
                duplicate_ts += 1
        prev = ts

    # OHLCV null/NaN count
    null_count = 0
    for e in in_range:
        for col in ("open", "high", "low", "close", "volume"):
            if _is_null(getattr(e, col, None)):
                null_count += 1

    # value ranges (ignoring nulls)
    closes = [e.close for e in in_range if not _is_null(getattr(e, "close", None))]
    vols = [e.volume for e in in_range if not _is_null(getattr(e, "volume", None))]
    close_min = min(closes) if closes else None
    close_max = max(closes) if closes else None
    vol_min = min(vols) if vols else None
    vol_max = max(vols) if vols else None

    # per-day anomalies vs expected
    under_days, over_days = [], []
    if per_day_expected:
        for d, c in sorted(per_day.items()):
            if c < per_day_expected:
                under_days.append((d, c))
            elif c > per_day_expected:
                over_days.append((d, c))
    missing_days = [d for d in date_range(start, end) if d not in per_day]

    day_counts = sorted(per_day.values())
    return {
        "total_events": total,
        "expected_rows": expected_rows,
        "day_count": day_count,
        "per_day_expected": per_day_expected,
        "first_ts_ns": in_range[0].event_time_ns if in_range else None,
        "last_ts_ns": in_range[-1].event_time_ns if in_range else None,
        "monotonic": monotonic,
        "duplicate_ts": duplicate_ts,
        "per_day_min": day_counts[0] if day_counts else 0,
        "per_day_max": day_counts[-1] if day_counts else 0,
        "under_days": under_days,
        "over_days": over_days,
        "missing_days": missing_days,
        "ohlcv_null_count": null_count,
        "close_min": close_min,
        "close_max": close_max,
        "volume_min": vol_min,
        "volume_max": vol_max,
        "days_present": len(per_day),
    }


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Read-only multi-day bar loader smoke")
    ap.add_argument("--root", required=True, help="Hive market_data root")
    ap.add_argument("--exchange", default="BINANCE")
    ap.add_argument("--venue-type", default="spot")
    ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument("--bar-type", default="1m")
    ap.add_argument("--start", required=True, help="inclusive UTC date YYYY-MM-DD")
    ap.add_argument("--end", required=True, help="inclusive UTC date YYYY-MM-DD")
    ap.add_argument("--instrument-id", default=None,
                    help="BarEvent label only; defaults to '<symbol>.<exchange>'")
    ap.add_argument("--timestamp-column", default="ts")
    ap.add_argument("--timestamp-unit", default="ns")
    ap.add_argument(
        "--physical-date-filter", action=argparse.BooleanOptionalAction, default=True,
        help="read only the requested date partitions (per-date load); "
             "use --no-physical-date-filter to read all dates then filter in-memory",
    )
    return ap


def build_data_config(args) -> dict:
    """Base ``hive_parquet_bars`` config with equality filters (no ``date``)."""
    return {
        "mode": "hive_parquet_bars",
        "root": args.root,
        "instrument_id": args.instrument_id or f"{args.symbol}.{args.exchange}",
        "warmup_bars": 0,
        "filters": {
            "asset_class": "crypto",
            "exchange": args.exchange,
            "venue_type": args.venue_type,
            "symbol": args.symbol,
            "data_type": "bar",
            "freq": args.bar_type,
        },
        "timestamp_column": args.timestamp_column,
        "timestamp_unit": args.timestamp_unit,
    }


def per_date_configs(base_cfg: dict, dates):
    """Yield ``(date_str, cfg)`` where each cfg adds a ``date`` equality filter.

    The loader's ``matching_fragments`` selects only that date's partition for
    physical read, so a per-date sweep reads only the requested dates — not the
    whole ``symbol/freq`` cache.
    """
    for d in dates:
        cfg = dict(base_cfg)
        cfg["filters"] = {**base_cfg["filters"], "date": d}
        yield d, cfg


def _is_missing_partition_error(exc: Exception) -> bool:
    """True when ``load_events`` failed only because a date partition is absent."""
    return isinstance(exc, ValueError) and "no parquet fragments" in str(exc)


def load_range_per_date(base_cfg: dict, dates, *, load_fn=load_events):
    """Load each date's partition independently and merge.

    Returns ``(events_sorted, days_loaded, missing_days)``. A date whose
    partition does not exist (loader raises ``no parquet fragments``) is recorded
    in ``missing_days`` — reported, never auto-created. Any other error
    propagates (fail-stop).
    """
    events: list = []
    days_loaded: list[str] = []
    missing: list[str] = []
    for d, cfg in per_date_configs(base_cfg, dates):
        try:
            warmup, live = load_fn(cfg)
        except Exception as exc:  # noqa: BLE001 - re-raised unless it's a missing partition
            if _is_missing_partition_error(exc):
                missing.append(d)
                continue
            raise
        day_events = list(warmup) + list(live)
        if day_events:
            days_loaded.append(d)
        else:
            missing.append(d)
        events.extend(day_events)
    events.sort(key=lambda e: e.event_time_ns)
    return events, days_loaded, missing


def _approx_memory_mb(events) -> float:
    """Rough resident estimate of the event list (shallow sizeof * count)."""
    if not events:
        return 0.0
    per = sys.getsizeof(events[0])
    return (sys.getsizeof(events) + per * len(events)) / (1024 * 1024)


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    start, end = parse_date(args.start), parse_date(args.end)
    per_day_expected = bars_per_day(args.bar_type)

    cfg = build_data_config(args)
    requested_dates = date_range(start, end)
    print(f"LOADER_MODE: {cfg['mode']}")
    print(f"ROOT: {cfg['root']}")
    print(f"FILTERS: {cfg['filters']}")
    print(f"DATE_RANGE: {args.start} .. {args.end} (inclusive)")
    print(f"PHYSICAL_READ_MODE: {'per_date_partition' if args.physical_date_filter else 'full_then_filter'}")
    print(f"PHYSICAL_DAYS_REQUESTED: {len(requested_dates)}")

    t0 = time.perf_counter()
    if args.physical_date_filter:
        events, days_loaded, phys_missing = load_range_per_date(cfg, requested_dates)
    else:
        warmup, live = load_events(cfg)
        events = list(warmup) + list(live)
        days_loaded, phys_missing = None, None
    load_elapsed = time.perf_counter() - t0

    stats = compute_bar_stats(events, start, end, per_day_expected=per_day_expected)

    if args.physical_date_filter:
        print(f"PHYSICAL_DAYS_LOADED: {len(days_loaded)}")
        print(f"PHYSICAL_MISSING_DAYS({len(phys_missing)}): "
              f"{phys_missing[:20]}{' ...' if len(phys_missing) > 20 else ''}")
    print(f"LOADED_EVENTS_TOTAL(physically read): {len(events)}")
    print(f"TOTAL_EVENTS(in range): {stats['total_events']}")
    print(f"EXPECTED_ROWS: {stats['expected_rows']} "
          f"(day_count={stats['day_count']} x per_day={stats['per_day_expected']})")
    print(f"FIRST_TS: {ns_to_iso(stats['first_ts_ns']) if stats['first_ts_ns'] else 'none'} "
          f"({stats['first_ts_ns']})")
    print(f"LAST_TS: {ns_to_iso(stats['last_ts_ns']) if stats['last_ts_ns'] else 'none'} "
          f"({stats['last_ts_ns']})")
    print(f"MONOTONIC_TS: {stats['monotonic']}")
    print(f"DUPLICATE_TS_COUNT: {stats['duplicate_ts']}")
    print(f"PER_DAY_ROW_MIN: {stats['per_day_min']}")
    print(f"PER_DAY_ROW_MAX: {stats['per_day_max']}")
    print(f"DAYS_PRESENT: {stats['days_present']} / {stats['day_count']}")
    print(f"UNDER_{stats['per_day_expected']}_DAYS({len(stats['under_days'])}): "
          f"{stats['under_days'][:20]}{' ...' if len(stats['under_days']) > 20 else ''}")
    print(f"OVER_{stats['per_day_expected']}_DAYS({len(stats['over_days'])}): "
          f"{stats['over_days'][:20]}{' ...' if len(stats['over_days']) > 20 else ''}")
    print(f"MISSING_DAYS({len(stats['missing_days'])}): "
          f"{stats['missing_days'][:20]}{' ...' if len(stats['missing_days']) > 20 else ''}")
    print(f"OHLCV_NULL_COUNT: {stats['ohlcv_null_count']}")
    print(f"CLOSE_MIN/MAX: {stats['close_min']} / {stats['close_max']}")
    print(f"VOLUME_MIN/MAX: {stats['volume_min']} / {stats['volume_max']}")
    print(f"LOAD_ELAPSED_SECONDS: {load_elapsed:.3f}")
    eps = (stats["total_events"] / load_elapsed) if load_elapsed > 0 else 0.0
    print(f"EVENTS_PER_SEC: {eps:.0f}")
    print(f"MEMORY_ESTIMATE_MB(rough): {_approx_memory_mb(events):.1f}")
    print("SMOKE_DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
