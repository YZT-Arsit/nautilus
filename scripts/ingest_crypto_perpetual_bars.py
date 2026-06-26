#!/usr/bin/env python3
"""Small guarded Binance USD-M perpetual kline ingestion for smoke tests."""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from data_engine.historical.catalog import partition_dir  # noqa: E402
from feature_engine.data_sources.binance_vision import (  # noqa: E402
    BinanceVisionImporter,
    build_binance_vision_kline_url,
)


MAX_SYMBOLS = 4
MAX_DAYS = 1
EXCHANGE = "BINANCE"
MARKET = "futures_um"
VENUE_TYPE = "futures_um"


@dataclass(frozen=True)
class IngestPlan:
    exchange: str
    market_type: str
    venue_type: str
    symbol: str
    instrument_id: str
    bar_type: str
    date: str
    url: str
    output_path: str
    status: str = "planned"
    rows: int | None = None
    error: str | None = None


def _parse_symbols(value: str, *, max_symbols: int = MAX_SYMBOLS) -> list[str]:
    symbols = [s.strip().upper() for s in value.split(",") if s.strip()]
    if not symbols:
        raise ValueError("at least one symbol is required")
    if max_symbols > MAX_SYMBOLS:
        raise ValueError(f"max-symbols guard: requested limit {max_symbols}, allowed {MAX_SYMBOLS}")
    if len(symbols) > max_symbols:
        raise ValueError(f"max-symbols guard: requested {len(symbols)}, allowed {max_symbols}")
    return symbols


def _dates(start: str, end: str, *, max_days: int = MAX_DAYS) -> list[str]:
    from datetime import timedelta  # noqa: PLC0415

    first = datetime.strptime(start, "%Y-%m-%d").date()
    last = datetime.strptime(end, "%Y-%m-%d").date()
    if last < first:
        raise ValueError("end date is before start date")
    days = (last - first).days + 1
    if days > max_days:
        raise ValueError(f"max-days guard: requested {days}, allowed {max_days}")
    return [(first + timedelta(days=i)).isoformat() for i in range(days)]


def _instrument_id(symbol: str) -> str:
    return f"{symbol}-PERP.BINANCE"


def _output_file(root: Path, *, symbol: str, bar_type: str, date: str) -> Path:
    return (
        partition_dir(
            root,
            exchange=EXCHANGE,
            venue_type=VENUE_TYPE,
            symbol=symbol,
            data_kind="bar",
            bar_type=bar_type,
            date=date,
        )
        / "part-0.parquet"
    )


def build_plan(*, symbols: list[str], bar_type: str, start: str, end: str, out_root: Path,
               max_days: int = MAX_DAYS) -> list[IngestPlan]:
    if len(symbols) > MAX_SYMBOLS:
        raise ValueError(f"max-symbols guard: requested {len(symbols)}, allowed {MAX_SYMBOLS}")
    plans: list[IngestPlan] = []
    for date in _dates(start, end, max_days=max_days):
        for symbol in symbols:
            url = build_binance_vision_kline_url(MARKET, symbol, bar_type, "daily", date)
            out = _output_file(out_root, symbol=symbol, bar_type=bar_type, date=date)
            plans.append(
                IngestPlan(
                    exchange=EXCHANGE,
                    market_type="crypto_perpetual",
                    venue_type=VENUE_TYPE,
                    symbol=symbol,
                    instrument_id=_instrument_id(symbol),
                    bar_type=bar_type,
                    date=date,
                    url=url,
                    output_path=str(out),
                    status="skipped_existing" if out.exists() else "planned",
                )
            )
    if len(plans) > MAX_SYMBOLS * max_days:
        raise ValueError("download guard exceeded")
    return plans


def _canonicalize_frame(df, *, symbol: str, bar_type: str):
    import polars as pl  # noqa: PLC0415

    return (
        df.with_columns(
            [
                pl.lit(_instrument_id(symbol)).alias("instrument_id"),
                pl.lit("binance_vision_futures_um_klines").alias("source"),
                pl.lit("trade_bar").alias("bar_source"),
                pl.lit(True).alias("is_trade_bar"),
                pl.lit(bar_type).alias("bar_type"),
                pl.lit(VENUE_TYPE).alias("venue_type"),
            ]
        )
        .select(
            [
                "ts",
                "instrument_id",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "quote_volume",
                "trade_count",
                "source",
                "bar_source",
                "is_trade_bar",
                "ingested_at",
            ]
        )
    )


def _validate_frame(df, *, symbol: str, date: str) -> None:
    import math

    rows = df.to_dicts()
    if not rows:
        raise ValueError(f"{symbol} {date}: no rows")
    timestamps = [row["ts"] for row in rows]
    if timestamps != sorted(timestamps):
        raise ValueError(f"{symbol} {date}: timestamps not monotonic")
    if len(set(timestamps)) != len(timestamps):
        raise ValueError(f"{symbol} {date}: duplicate timestamps")
    for row in rows:
        o, h, l, c = (float(row[k]) for k in ("open", "high", "low", "close"))
        if not all(math.isfinite(x) for x in (o, h, l, c)):
            raise ValueError(f"{symbol} {date}: non-finite OHLC")
        if h < max(o, c) or l > min(o, c):
            raise ValueError(f"{symbol} {date}: invalid OHLC bounds")
        if float(row["volume"]) < 0 or float(row["quote_volume"]) < 0 or int(row["trade_count"]) < 0:
            raise ValueError(f"{symbol} {date}: invalid volume/trade_count")
        if row["bar_source"] != "trade_bar" or row.get("is_trade_bar") is not True:
            raise ValueError(f"{symbol} {date}: expected trade_bar")


def execute_plan(plan: list[IngestPlan], *, out_root: Path, no_overwrite: bool, timeout: int) -> list[IngestPlan]:
    import pyarrow as pa  # noqa: PLC0415
    import pyarrow.parquet as pq  # noqa: PLC0415

    importer = BinanceVisionImporter(timeout=timeout)
    results: list[IngestPlan] = []
    for item in plan:
        out = Path(item.output_path)
        if out.exists():
            if no_overwrite:
                results.append(IngestPlan(**{**asdict(item), "status": "skipped_existing"}))
                continue
            raise FileExistsError(f"refusing to overwrite existing output: {out}")
        try:
            df = importer.import_period(
                market=MARKET,
                symbol=item.symbol,
                interval=item.bar_type,
                frequency="daily",
                start_date=item.date,
                end_date=item.date,
            )
            df = _canonicalize_frame(df, symbol=item.symbol, bar_type=item.bar_type)
            _validate_frame(df, symbol=item.symbol, date=item.date)
            out.parent.mkdir(parents=True, exist_ok=True)
            pq.write_table(pa.Table.from_pylist(df.to_dicts()), out)
            results.append(IngestPlan(**{**asdict(item), "status": "downloaded", "rows": df.height}))
        except Exception as exc:
            results.append(IngestPlan(**{**asdict(item), "status": "failed", "error": f"{type(exc).__name__}: {exc}"}))
    return results


def _write_manifest(results: list[IngestPlan], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [asdict(row) for row in results]
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Guarded crypto perpetual bar ingestion smoke")
    ap.add_argument("--exchange", default=EXCHANGE, choices=[EXCHANGE])
    ap.add_argument("--market-type", default="perpetual", choices=["perpetual", "futures_um"])
    ap.add_argument("--symbols", required=True)
    ap.add_argument("--bar-type", default="5m")
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--out-root", default="historical_data/market_data")
    ap.add_argument("--plan-only", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max-symbols", type=int, default=MAX_SYMBOLS)
    ap.add_argument("--max-days", type=int, default=MAX_DAYS,
                    help="multi-day range guard (default 1 = smoke; raise to ingest a window)")
    ap.add_argument("--no-overwrite", action="store_true", default=True)
    ap.add_argument("--timeout", type=int, default=30)
    args = ap.parse_args(argv)

    if args.market_type not in {"perpetual", "futures_um"}:
        raise ValueError("only Binance USD-M perpetual smoke is supported")
    symbols = _parse_symbols(args.symbols, max_symbols=args.max_symbols)
    out_root = Path(args.out_root)
    plan = build_plan(symbols=symbols, bar_type=args.bar_type, start=args.start, end=args.end,
                      out_root=out_root, max_days=args.max_days)
    print(f"PLAN jobs={len(plan)} exchange={args.exchange} market=futures_um bar_type={args.bar_type}")
    for item in plan:
        print(f"  {item.symbol} {item.date}: {item.url} -> {item.output_path} status={item.status}")
    if args.plan_only:
        print("PLAN_ONLY_NO_WRITES")
        return 0
    if args.dry_run:
        print("DRY_RUN_NO_WRITES")
        return 0
    results = execute_plan(plan, out_root=out_root, no_overwrite=args.no_overwrite, timeout=args.timeout)
    manifest = Path("outputs") / "ingestion_manifests" / "crypto_perpetual_multisymbol_bars_smoke_manifest.json"
    _write_manifest(results, manifest)
    print(f"MANIFEST {manifest}")
    failed = [row for row in results if row.status == "failed"]
    for row in results:
        print(f"  {row.symbol} {row.date}: status={row.status} rows={row.rows or ''} error={row.error or ''}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
