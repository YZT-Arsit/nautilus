#!/usr/bin/env python3
"""Guarded public metadata ingestion for Binance USD-M perpetual smoke tests."""
from __future__ import annotations

import argparse
import csv
import io
import json
import sys
import zipfile
from dataclasses import asdict
from dataclasses import dataclass
from datetime import datetime
from datetime import timezone
from pathlib import Path
from typing import Any
from urllib.request import urlopen

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from research.crypto_perpetual_metadata import EXCHANGE  # noqa: E402
from research.crypto_perpetual_metadata import VENUE_TYPE  # noqa: E402
from research.crypto_perpetual_metadata import build_exchange_info_url  # noqa: E402
from research.crypto_perpetual_metadata import build_funding_rate_archive_url  # noqa: E402
from research.crypto_perpetual_metadata import build_funding_rate_url  # noqa: E402
from research.crypto_perpetual_metadata import build_index_price_archive_url  # noqa: E402
from research.crypto_perpetual_metadata import build_index_price_url  # noqa: E402
from research.crypto_perpetual_metadata import build_mark_price_archive_url  # noqa: E402
from research.crypto_perpetual_metadata import build_mark_price_url  # noqa: E402
from research.crypto_perpetual_metadata import instrument_id  # noqa: E402
from research.crypto_perpetual_metadata import normalize_exchange_info  # noqa: E402
from research.crypto_perpetual_metadata import normalize_funding_rates  # noqa: E402
from research.crypto_perpetual_metadata import normalize_mark_index_prices  # noqa: E402
from research.crypto_perpetual_metadata import utc_day_bounds_ms  # noqa: E402
from research.crypto_perpetual_metadata import validate_endpoint  # noqa: E402


MAX_SYMBOLS = 4
METADATA_TYPES = ("exchange_info", "funding_rate", "mark_price")


@dataclass(frozen=True)
class MetadataPlan:
    exchange: str
    venue_type: str
    symbol: str
    instrument_id: str
    metadata_type: str
    date: str
    endpoints: tuple[str, ...]
    output_path: str
    status: str = "planned"
    rows: int | None = None
    error: str | None = None


def _parse_symbols(value: str, *, max_symbols: int) -> list[str]:
    symbols = [item.strip().upper() for item in value.split(",") if item.strip()]
    if not symbols:
        raise ValueError("at least one symbol is required")
    if len(symbols) > max_symbols:
        raise ValueError(f"max-symbols guard: requested {len(symbols)}, allowed {max_symbols}")
    if max_symbols > MAX_SYMBOLS:
        raise ValueError(f"max-symbols guard: requested limit {max_symbols}, allowed {MAX_SYMBOLS}")
    return symbols


def _parse_metadata_types(value: str) -> tuple[str, ...]:
    items = tuple(item.strip() for item in value.split(",") if item.strip())
    if not items:
        raise ValueError("at least one metadata type is required")
    unknown = sorted(set(items) - set(METADATA_TYPES))
    if unknown:
        raise ValueError(f"unsupported metadata types: {unknown}")
    return items


def _output_path(root: Path, *, symbol: str, metadata_type: str, day: str) -> Path:
    base = root / f"exchange={EXCHANGE}" / f"venue_type={VENUE_TYPE}" / f"symbol={symbol}" / f"metadata_type={metadata_type}"
    if metadata_type == "exchange_info":
        return base / "snapshot.json"
    return base / f"date={day}" / "part-0.parquet"


def build_plan(
    *,
    symbols: list[str],
    day: str,
    metadata_types: tuple[str, ...],
    out_root: Path,
) -> list[MetadataPlan]:
    if len(symbols) > MAX_SYMBOLS:
        raise ValueError(f"max-symbols guard: requested {len(symbols)}, allowed {MAX_SYMBOLS}")
    datetime.strptime(day, "%Y-%m-%d")
    plan: list[MetadataPlan] = []
    for symbol in symbols:
        for metadata_type in metadata_types:
            if metadata_type == "exchange_info":
                endpoints = (build_exchange_info_url(),)
            elif metadata_type == "funding_rate":
                endpoints = (build_funding_rate_archive_url(symbol, day), build_funding_rate_url(symbol, day))
            elif metadata_type == "mark_price":
                endpoints = (
                    build_mark_price_archive_url(symbol, day),
                    build_index_price_archive_url(symbol, day),
                    build_mark_price_url(symbol, day),
                    build_index_price_url(symbol, day),
                )
            else:
                raise ValueError(f"unsupported metadata type: {metadata_type}")
            for endpoint in endpoints:
                validate_endpoint(endpoint)
            out = _output_path(out_root, symbol=symbol, metadata_type=metadata_type, day=day)
            plan.append(
                MetadataPlan(
                    exchange=EXCHANGE,
                    venue_type=VENUE_TYPE,
                    symbol=symbol,
                    instrument_id=instrument_id(symbol),
                    metadata_type=metadata_type,
                    date=day,
                    endpoints=endpoints,
                    output_path=str(out),
                    status="skipped_existing" if out.exists() else "planned",
                )
            )
    return plan


def execute_plan(plan: list[MetadataPlan], *, timeout: int, no_overwrite: bool) -> list[MetadataPlan]:
    results: list[MetadataPlan] = []
    exchange_info_cache: dict[str, Any] | None = None
    fetched_at = datetime.now(timezone.utc).isoformat()
    for item in plan:
        out = Path(item.output_path)
        if out.exists():
            if no_overwrite:
                results.append(MetadataPlan(**{**asdict(item), "status": "skipped_existing"}))
                continue
            raise FileExistsError(f"refusing to overwrite existing output: {out}")
        try:
            if item.metadata_type == "exchange_info":
                if exchange_info_cache is None:
                    exchange_info_cache = _fetch_json(item.endpoints[0], timeout=timeout)
                record = normalize_exchange_info(exchange_info_cache, symbol=item.symbol, fetched_at=fetched_at)
                _write_json(out, record.to_dict())
                results.append(MetadataPlan(**{**asdict(item), "status": "downloaded", "rows": 1}))
            elif item.metadata_type == "funding_rate":
                payload = _fetch_funding_archive(item.endpoints[0], symbol=item.symbol, day=item.date, timeout=timeout)
                rows = [row.to_dict() for row in normalize_funding_rates(payload, symbol=item.symbol, ingested_at=fetched_at)]
                _write_parquet(out, rows)
                results.append(MetadataPlan(**{**asdict(item), "status": "downloaded", "rows": len(rows)}))
            elif item.metadata_type == "mark_price":
                mark_rows = _fetch_kline_archive(item.endpoints[0], timeout=timeout)
                index_rows = _fetch_kline_archive(item.endpoints[1], timeout=timeout)
                rows = [
                    row.to_dict()
                    for row in normalize_mark_index_prices(
                        mark_rows,
                        index_rows,
                        symbol=item.symbol,
                        ingested_at=fetched_at,
                    )
                ]
                _write_parquet(out, rows)
                results.append(MetadataPlan(**{**asdict(item), "status": "downloaded", "rows": len(rows)}))
            else:
                raise ValueError(f"unsupported metadata type: {item.metadata_type}")
        except Exception as exc:
            results.append(MetadataPlan(**{**asdict(item), "status": "failed", "error": f"{type(exc).__name__}: {exc}"}))
    return results


def resolve_smoke_root(root: Path) -> Path:
    if not root.exists() or not any(root.iterdir()):
        return root
    for suffix in range(2, 100):
        candidate = root.with_name(f"{root.name}_{suffix}")
        if not candidate.exists() or not any(candidate.iterdir()):
            return candidate
    raise FileExistsError(f"could not find free smoke output suffix for {root}")


def _fetch_json(url: str, *, timeout: int) -> Any:
    validate_endpoint(url)
    with urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _fetch_zip_csv(url: str, *, timeout: int) -> list[dict[str, str]]:
    validate_endpoint(url)
    with urlopen(url, timeout=timeout) as response:
        data = response.read()
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = [name for name in archive.namelist() if name.endswith(".csv")]
        if not names:
            raise ValueError(f"zip archive has no csv: {url}")
        with archive.open(names[0]) as handle:
            reader = csv.DictReader(io.TextIOWrapper(handle, encoding="utf-8"))
            return [dict(row) for row in reader]


def _fetch_funding_archive(url: str, *, symbol: str, day: str, timeout: int) -> list[dict[str, object]]:
    start_ms, end_ms = utc_day_bounds_ms(day)
    payload: list[dict[str, object]] = []
    for row in _fetch_zip_csv(url, timeout=timeout):
        ts_ms = int(row["calc_time"])
        if start_ms <= ts_ms <= end_ms:
            payload.append(
                {
                    "symbol": symbol.upper(),
                    "fundingRate": row["last_funding_rate"],
                    "fundingTime": ts_ms,
                }
            )
    return payload


def _fetch_kline_archive(url: str, *, timeout: int) -> list[list[object]]:
    rows: list[list[object]] = []
    for row in _fetch_zip_csv(url, timeout=timeout):
        rows.append(
            [
                int(row["open_time"]),
                row["open"],
                row["high"],
                row["low"],
                row["close"],
                row.get("volume", "0"),
                int(row["close_time"]),
                row.get("quote_volume", "0"),
                int(row.get("count", "0")),
                row.get("taker_buy_volume", "0"),
                row.get("taker_buy_quote_volume", "0"),
                row.get("ignore", "0"),
            ]
        )
    return rows


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _write_parquet(path: Path, rows: list[dict[str, object]]) -> None:
    import pyarrow as pa  # noqa: PLC0415
    import pyarrow.parquet as pq  # noqa: PLC0415

    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows), path)


def _write_manifest(results: list[MetadataPlan], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([asdict(row) for row in results], indent=2), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Guarded Binance USD-M perpetual metadata smoke ingestion")
    ap.add_argument("--exchange", default=EXCHANGE, choices=[EXCHANGE])
    ap.add_argument("--venue-type", default=VENUE_TYPE, choices=[VENUE_TYPE])
    ap.add_argument("--symbols", required=True)
    ap.add_argument("--date", required=True)
    ap.add_argument("--metadata-types", default="exchange_info,funding_rate,mark_price")
    ap.add_argument("--out-root", default="outputs/derived_market_data/crypto_perpetual_metadata_smoke")
    ap.add_argument("--plan-only", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max-symbols", type=int, default=MAX_SYMBOLS)
    ap.add_argument("--no-overwrite", action="store_true", default=True)
    ap.add_argument("--timeout", type=int, default=30)
    args = ap.parse_args(argv)

    symbols = _parse_symbols(args.symbols, max_symbols=args.max_symbols)
    metadata_types = _parse_metadata_types(args.metadata_types)
    requested_root = Path(args.out_root)
    out_root = requested_root if args.plan_only or args.dry_run else resolve_smoke_root(requested_root)
    plan = build_plan(symbols=symbols, day=args.date, metadata_types=metadata_types, out_root=out_root)

    print(f"PLAN jobs={len(plan)} exchange={args.exchange} venue_type={args.venue_type} root={out_root}")
    for item in plan:
        print(f"  {item.symbol} {item.metadata_type} {item.date}:")
        for endpoint in item.endpoints:
            print(f"    endpoint={endpoint}")
        print(f"    output={item.output_path} status={item.status}")
    if args.plan_only:
        print("PLAN_ONLY_NO_WRITES")
        return 0
    if args.dry_run:
        print("DRY_RUN_NO_WRITES")
        return 0

    results = execute_plan(plan, timeout=args.timeout, no_overwrite=args.no_overwrite)
    manifest = out_root / "_manifest.json"
    _write_manifest(results, manifest)
    print(f"MANIFEST {manifest}")
    for row in results:
        print(f"  {row.symbol} {row.metadata_type}: status={row.status} rows={row.rows or ''} error={row.error or ''}")
    return 1 if any(row.status == "failed" for row in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
