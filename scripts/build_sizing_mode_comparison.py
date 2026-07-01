#!/usr/bin/env python3
"""Build the fixed / notional / vol-targeted sizing-mode comparison table.

Reads the three already-built batch evaluation tables (one per sizing mode) plus
their sizing CSVs, and writes:

    <out-dir>/sizing_mode_comparison.csv   (rows = symbol x sizing_mode)
    <out-dir>/sizing_mode_comparison.md

Pure reporting: reads existing CSVs only, no backtest, no network, no strategy or
feature_engine import. ``fixed_quantity`` needs no sizing file (quantity = 1.0,
initial_notional = initial_price x 1.0, with initial_price taken from the
vol-targeted sizing CSV).
"""
from __future__ import annotations

import argparse
import csv
from pathlib import Path

from research import evaluation_tables as et


def _read_sizing(path: Path | None) -> dict[str, dict]:
    if not path or not path.is_file():
        return {}
    with path.open(encoding="utf-8") as fh:
        return {str(r.get("symbol", "")).upper(): r for r in csv.DictReader(fh)}


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="fixed/notional/vol-targeted sizing comparison")
    ap.add_argument("--symbols", default="BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT")
    ap.add_argument("--fixed-table", required=True, help="fixed-quantity batch_evaluation_table.csv")
    ap.add_argument("--notional-table", required=True)
    ap.add_argument("--notional-sizing", required=True)
    ap.add_argument("--vol-table", required=True)
    ap.add_argument("--vol-sizing", required=True)
    ap.add_argument("--out-dir", required=True)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    symbols = [s.strip().upper() for s in str(args.symbols).split(",") if s.strip()]

    fixed_tbl = et.read_table_csv(Path(args.fixed_table))
    notional_tbl = et.read_table_csv(Path(args.notional_table))
    vol_tbl = et.read_table_csv(Path(args.vol_table))
    notional_sz = _read_sizing(Path(args.notional_sizing))
    vol_sz = _read_sizing(Path(args.vol_sizing))

    # initial_price / realized_vol per symbol come from the vol sizing CSV.
    price_by_symbol = {s: _num(vol_sz.get(s, {}).get("initial_price")) for s in symbols}
    vol_by_symbol = {s: vol_sz.get(s, {}).get("realized_vol_bar",
                                              vol_sz.get(s, {}).get("realized_vol_15m", et.NA))
                     for s in symbols}

    mode_specs = [
        {"mode": "fixed_quantity", "table": fixed_tbl, "sizing": {}},
        {"mode": "notional_normalized", "table": notional_tbl, "sizing": notional_sz},
        {"mode": "vol_targeted", "table": vol_tbl, "sizing": vol_sz},
    ]
    rows = et.build_sizing_mode_comparison(symbols, mode_specs,
                                           vol_by_symbol=vol_by_symbol,
                                           price_by_symbol=price_by_symbol)
    out = Path(args.out_dir)
    et.write_sizing_mode_comparison_csv(rows, out / "sizing_mode_comparison.csv")
    et.write_sizing_mode_comparison_md(rows, out / "sizing_mode_comparison.md")
    print(f"SIZING_MODE_COMPARISON_CSV {out / 'sizing_mode_comparison.csv'}")
    print(f"SIZING_MODE_COMPARISON_MD {out / 'sizing_mode_comparison.md'}")
    print(f"ROWS {len(rows)} (symbols={len(symbols)} x modes=3)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
