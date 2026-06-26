#!/usr/bin/env python3
"""Build the baseline-VWM vs trend-filtered-VWM comparison table.

Reads the two already-built batch evaluation tables (baseline = vol-targeted, and
trend-filtered) and writes:

    <out-dir>/trend_filter_comparison.csv   (rows = symbol, baseline/filtered/delta)
    <out-dir>/trend_filter_comparison.md

Pure reporting: reads existing CSVs only. No backtest, no network, no strategy or
feature_engine import.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from research import evaluation_tables as et


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="baseline vs trend-filtered VWM comparison")
    ap.add_argument("--symbols", default="BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT")
    ap.add_argument("--baseline-table", required=True, help="vol-targeted batch_evaluation_table.csv")
    ap.add_argument("--filtered-table", required=True, help="trend-filtered batch_evaluation_table.csv")
    ap.add_argument("--out-dir", required=True)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    symbols = [s.strip().upper() for s in str(args.symbols).split(",") if s.strip()]
    baseline = et.read_table_csv(Path(args.baseline_table))
    filtered = et.read_table_csv(Path(args.filtered_table))
    rows = et.build_trend_filter_comparison(symbols, baseline, filtered)
    out = Path(args.out_dir)
    et.write_trend_filter_comparison_csv(rows, out / "trend_filter_comparison.csv")
    et.write_trend_filter_comparison_md(rows, out / "trend_filter_comparison.md")
    print(f"TREND_FILTER_COMPARISON_CSV {out / 'trend_filter_comparison.csv'}")
    print(f"TREND_FILTER_COMPARISON_MD {out / 'trend_filter_comparison.md'}")
    print(f"ROWS {len(rows)}")
    for r in rows:
        print(f"  {r['symbol']}: total {r['baseline_total_return']}->{r['filtered_total_return']} "
              f"trades {r['baseline_trade_count']}->{r['filtered_trade_count']} "
              f"short {r['baseline_short_exposure_pct']}->{r['filtered_short_exposure_pct']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
