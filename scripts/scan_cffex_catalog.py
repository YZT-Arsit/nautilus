"""Read-only inventory of CFFEX instruments in the Nautilus catalog.

Walks::

    {catalog}/cffex_l1_quote/data/quote_tick/{INSTRUMENT}.CFFEX/*.parquet

and produces a per-instrument summary:

  * ``trading_date_partitions`` – distinct dates inferred from the start
    timestamp in each parquet filename (filename format
    ``{startISO}_{endISO}.parquet`` is used directly to avoid opening the
    parquet body just to discover the date).
  * ``total_quote_ticks`` – sum of ``num_rows`` from each file's parquet
    metadata (metadata read only — no row-group decode).
  * ``first_ts`` / ``last_ts`` – earliest / latest timestamp parsed from
    filenames across all files for the instrument.
  * ``date_coverage_days`` – span between first and last calendar day.
  * ``files`` – number of parquet files contributing to this instrument.
  * ``bytes_on_disk`` – sum of file sizes.

The script is **strictly read-only**: it never opens parquet files for write,
never touches the catalog tree, and writes the output CSV to a
``--output-dir`` outside the catalog (default ``outputs/qfe_catalog_inventory``
under the project root).

Output
------
Two artifacts:

  1. A console summary table (sorted by ``trading_date_partitions`` desc,
     ``total_quote_ticks`` desc).
  2. A CSV at ``{output-dir}/cffex_inventory.csv`` with the same columns.

Usage
-----
::

    python scripts/scan_cffex_catalog.py \\
        --catalog "D:\\QuanHub\\DataHome\\DataTrans\\nautilus_catalog" \\
        --output-dir "D:\\nautilus\\outputs\\qfe_catalog_inventory"
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

# pyarrow.parquet is the only "heavy" dep and it's already installed alongside
# the framework. We only ever ask for file *metadata*, never row groups.
import pyarrow.parquet as pq

# Filename pattern: 2023-01-03T01-29-00-200000000Z_2023-01-03T06-59-59-200000000Z.parquet
_TS_RE = re.compile(
    r"^(?P<start>\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}-\d{9}Z)"
    r"_(?P<end>\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}-\d{9}Z)\.parquet$"
)


def _parse_catalog_ts(s: str) -> datetime:
    """Parse the catalog's filename timestamp format → UTC datetime.

    Format example: ``2023-01-03T01-29-00-200000000Z`` →
    ``2023-01-03T01:29:00.200000+00:00``. We accept the literal hyphen-
    separated form and rebuild an ISO 8601 string before parsing.
    """
    # Split off the trailing 'Z' marker.
    body = s.rstrip("Z")
    date_part, time_part = body.split("T", 1)
    # time_part = HH-MM-SS-NNNNNNNNN
    hh, mm, ss, frac = time_part.split("-")
    # Trim fractional seconds to microseconds (datetime supports 6 digits).
    micros = frac[:6].ljust(6, "0")
    iso = f"{date_part}T{hh}:{mm}:{ss}.{micros}+00:00"
    return datetime.fromisoformat(iso)


@dataclass
class InstrumentReport:
    instrument_id: str
    series: str  # IC / IF / IH / IM
    trading_date_partitions: int
    total_quote_ticks: int
    files: int
    bytes_on_disk: int
    first_ts: str | None
    last_ts: str | None
    date_coverage_days: int
    trading_dates: list[str] = field(default_factory=list)


def _scan_instrument(inst_dir: Path) -> InstrumentReport | None:
    """Build a report for one ``{INSTRUMENT}.CFFEX/`` directory."""
    files = sorted(inst_dir.glob("*.parquet"))
    if not files:
        return None

    instrument_id = inst_dir.name  # e.g. IH2303.CFFEX
    series = instrument_id[:2]
    dates: set[str] = set()
    total_rows = 0
    total_bytes = 0
    min_ts: datetime | None = None
    max_ts: datetime | None = None

    for f in files:
        m = _TS_RE.match(f.name)
        if m is not None:
            try:
                start_dt = _parse_catalog_ts(m.group("start"))
                end_dt = _parse_catalog_ts(m.group("end"))
                dates.add(start_dt.date().isoformat())
                # An overnight file would also contribute an end-date partition.
                dates.add(end_dt.date().isoformat())
                if min_ts is None or start_dt < min_ts:
                    min_ts = start_dt
                if max_ts is None or end_dt > max_ts:
                    max_ts = end_dt
            except ValueError:
                # Filename didn't actually round-trip; fall back to mtime.
                stat = f.stat()
                dates.add(
                    datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
                    .date()
                    .isoformat()
                )

        # Cheap metadata read for the precise row count.
        try:
            total_rows += pq.ParquetFile(f).metadata.num_rows
        except Exception as e:  # noqa: BLE001 — log and continue, never block scan
            print(f"  [warn] could not read metadata for {f}: {e}", file=sys.stderr)
        total_bytes += f.stat().st_size

    coverage_days = 0
    if min_ts is not None and max_ts is not None:
        coverage_days = (max_ts.date() - min_ts.date()).days + 1

    return InstrumentReport(
        instrument_id=instrument_id,
        series=series,
        trading_date_partitions=len(dates),
        total_quote_ticks=total_rows,
        files=len(files),
        bytes_on_disk=total_bytes,
        first_ts=min_ts.isoformat() if min_ts else None,
        last_ts=max_ts.isoformat() if max_ts else None,
        date_coverage_days=coverage_days,
        trading_dates=sorted(dates),
    )


def scan_catalog(catalog_root: Path, series_filter: tuple[str, ...]) -> list[InstrumentReport]:
    quote_root = catalog_root / "cffex_l1_quote" / "data" / "quote_tick"
    if not quote_root.exists():
        raise FileNotFoundError(f"quote_tick directory not found: {quote_root}")
    reports: list[InstrumentReport] = []
    for inst_dir in sorted(p for p in quote_root.iterdir() if p.is_dir()):
        series = inst_dir.name[:2]
        if series_filter and series not in series_filter:
            continue
        r = _scan_instrument(inst_dir)
        if r is not None:
            reports.append(r)
    # Sort: more trading_date_partitions first, then more ticks.
    reports.sort(
        key=lambda r: (r.trading_date_partitions, r.total_quote_ticks),
        reverse=True,
    )
    return reports


def _format_bytes(n: int) -> str:
    units = ["B", "KB", "MB", "GB"]
    x = float(n)
    for u in units:
        if x < 1024 or u == units[-1]:
            return f"{x:.1f} {u}"
        x /= 1024
    return f"{n} B"


def print_summary(reports: list[InstrumentReport]) -> None:
    print()
    print("=" * 92)
    print("CFFEX QuoteTick catalog inventory")
    print("=" * 92)
    if not reports:
        print("(no instruments found)")
        return

    header = (
        f"{'instrument':<18} {'series':<6} {'dates':>6} {'ticks':>11} "
        f"{'files':>5} {'size':>10}  {'first → last (UTC)':<46}"
    )
    print(header)
    print("-" * len(header))
    for r in reports:
        window = f"{r.first_ts or '-'} → {r.last_ts or '-'}"
        print(
            f"{r.instrument_id:<18} {r.series:<6} {r.trading_date_partitions:>6} "
            f"{r.total_quote_ticks:>11,} {r.files:>5} {_format_bytes(r.bytes_on_disk):>10}  "
            f"{window:<46}"
        )
    print("-" * len(header))

    total_ticks = sum(r.total_quote_ticks for r in reports)
    total_files = sum(r.files for r in reports)
    total_bytes = sum(r.bytes_on_disk for r in reports)
    by_series: dict[str, int] = {}
    for r in reports:
        by_series[r.series] = by_series.get(r.series, 0) + 1
    print(
        f"totals: instruments={len(reports)}  ticks={total_ticks:,}  "
        f"files={total_files}  size={_format_bytes(total_bytes)}"
    )
    print(
        "by series: " + ", ".join(f"{s}={c}" for s, c in sorted(by_series.items()))
    )


def write_csv(reports: list[InstrumentReport], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cols = [
        "instrument_id", "series", "trading_date_partitions", "total_quote_ticks",
        "files", "bytes_on_disk", "first_ts", "last_ts", "date_coverage_days",
        "trading_dates",
    ]
    with output_path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for r in reports:
            row = asdict(r)
            # Join the list field for spreadsheet friendliness.
            row["trading_dates"] = "|".join(row["trading_dates"])
            w.writerow(row)
    print(f"\nCSV written: {output_path}  ({len(reports)} rows)")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--catalog", required=True, type=Path,
                   help="Path to nautilus_catalog root.")
    p.add_argument("--output-dir", required=True, type=Path,
                   help="Where to write cffex_inventory.csv.")
    p.add_argument("--series", default="IC,IF,IH,IM",
                   help="Comma-separated CFFEX series codes to include.")
    args = p.parse_args()

    series_filter = tuple(s.strip().upper() for s in args.series.split(",") if s.strip())
    reports = scan_catalog(args.catalog, series_filter)
    print_summary(reports)
    write_csv(reports, args.output_dir / "cffex_inventory.csv")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(line_buffering=True)  # type: ignore[attr-defined]
    raise SystemExit(main())
