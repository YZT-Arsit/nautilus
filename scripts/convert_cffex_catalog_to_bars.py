#!/usr/bin/env python3
"""Plan or convert CFFEX Nautilus quote/depth catalog data to derived mid bars."""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from research.cffex_bar_converter import depth_rows_to_mid_bars  # noqa: E402
from research.cffex_bar_converter import quote_rows_to_mid_bars  # noqa: E402
from research.cffex_bar_converter import read_parquet_rows  # noqa: E402
from research.cffex_bar_converter import write_mid_bars  # noqa: E402


@dataclass(frozen=True)
class ConversionPlan:
    instrument_id: str
    source: str
    files: list[Path]


def safe_output_root(path: str | Path) -> Path:
    out = Path(path)
    parts = out.parts
    allowed = {("outputs", "derived_market_data"), ("historical_data", "market_data_derived")}
    if not any(parts[i : i + 2] in allowed for i in range(max(0, len(parts) - 1))):
        raise ValueError(
            "output root must live under outputs/derived_market_data or "
            "historical_data/market_data_derived"
        )
    if ("outputs", "backtests") in {parts[i : i + 2] for i in range(max(0, len(parts) - 1))}:
        raise ValueError("refusing to write under the backtest output tree")
    if ("historical_data", "market_data") in {parts[i : i + 2] for i in range(max(0, len(parts) - 1))}:
        raise ValueError("refusing to write under original historical_data/market_data")
    return out


def _instrument_id(symbol: str) -> str:
    return symbol if "." in symbol else f"{symbol}.CFFEX"


def _source_dir(root: Path, source: str, instrument_id: str) -> Path:
    if source == "quote_tick":
        return root / "cffex_l1_quote" / "data" / "quote_tick" / instrument_id
    if source == "order_book_depths":
        return root / "cffex_l1_depth10" / "data" / "order_book_depths" / instrument_id
    raise ValueError("source must be quote_tick or order_book_depths")


def _parse_catalog_start(path: Path) -> str:
    stem = path.stem
    return stem.split("_", 1)[0] if "_" in stem else ""


def _date_from_catalog_start(value: str) -> str | None:
    if not value:
        return None
    return value.split("T", 1)[0]


def build_plan(
    *,
    native_catalog_root: str | Path,
    symbols: list[str],
    source: str,
    start: str | None = None,
    end: str | None = None,
    max_symbols: int | None = None,
    max_days: int | None = None,
) -> list[ConversionPlan]:
    root = Path(native_catalog_root)
    if not root.exists():
        raise FileNotFoundError(f"native catalog root not found: {root}")
    selected = symbols[: max_symbols or len(symbols)]
    plans: list[ConversionPlan] = []
    for symbol in selected:
        instrument_id = _instrument_id(symbol)
        src_dir = _source_dir(root, source, instrument_id)
        if not src_dir.exists():
            raise FileNotFoundError(f"{source} directory not found for {instrument_id}: {src_dir}")
        files = sorted(src_dir.glob("*.parquet"))
        if start or end:
            files = [
                p for p in files
                if _date_in_range(_date_from_catalog_start(_parse_catalog_start(p)), start, end)
            ]
        if max_days is not None:
            files = files[:max_days]
        if not files:
            raise FileNotFoundError(f"no parquet files selected for {instrument_id}")
        plans.append(ConversionPlan(instrument_id=instrument_id, source=source, files=files))
    return plans


def _date_in_range(value: str | None, start: str | None, end: str | None) -> bool:
    if value is None:
        return False
    if start and value < start:
        return False
    if end and value > end:
        return False
    return True


def run_conversion(
    plans: list[ConversionPlan],
    *,
    out: str | Path,
    bar_type: str,
    source: str,
    volume_policy: str = "tick_count",
) -> list[Path]:
    written: list[Path] = []
    for plan in plans:
        rows = read_parquet_rows(plan.files)
        if source == "quote_tick":
            bars = quote_rows_to_mid_bars(
                rows,
                instrument_id=plan.instrument_id,
                bar_type=bar_type,
                volume_policy=volume_policy,
                price_scale=1_000_000_000,
            )
        else:
            bars = depth_rows_to_mid_bars(
                rows,
                instrument_id=plan.instrument_id,
                bar_type=bar_type,
                volume_policy=volume_policy,
                price_scale=1_000_000_000,
            )
        written.extend(write_mid_bars(bars, out, bar_type=bar_type))
    return written


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Convert CFFEX quote/depth catalog to derived mid bars")
    ap.add_argument("--native-catalog-root", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--symbols", required=True, help="Comma-separated symbols, e.g. IF2303,IH2303")
    ap.add_argument("--bar-type", default="1m", choices=["1m", "5m"])
    ap.add_argument("--source", default="quote_tick", choices=["quote_tick", "order_book_depths"])
    ap.add_argument("--start", default=None)
    ap.add_argument("--end", default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--plan-only", action="store_true")
    ap.add_argument("--overwrite", action="store_true")
    ap.add_argument("--max-symbols", type=int, default=None)
    ap.add_argument("--max-days", type=int, default=None)
    args = ap.parse_args(argv)

    out = safe_output_root(args.out)
    if out.exists() and not args.overwrite:
        raise FileExistsError(f"output root exists; pass --overwrite to reuse: {out}")
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    plans = build_plan(
        native_catalog_root=args.native_catalog_root,
        symbols=symbols,
        source=args.source,
        start=args.start,
        end=args.end,
        max_symbols=args.max_symbols,
        max_days=args.max_days,
    )
    print(f"PLAN jobs={len(plans)} source={args.source} bar_type={args.bar_type}")
    for plan in plans:
        print(f"  {plan.instrument_id}: files={len(plan.files)}")
    if args.dry_run or args.plan_only:
        print("DRY_RUN_NO_WRITES")
        return 0
    written = run_conversion(plans, out=out, bar_type=args.bar_type, source=args.source)
    print(f"WROTE files={len(written)} out={out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
