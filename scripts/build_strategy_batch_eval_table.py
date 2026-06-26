#!/usr/bin/env python3
"""Batch strategy evaluation table (rows = symbol, cols = evaluation metrics).

The boss-facing deliverable: one strategy (VWM) run across several instruments
under the SAME bar_type / window / initial cash / params, rendered as one row per
symbol and one column per metric. This is a thin CLI only:

    1. parse args / resolve paths;
    2. read the batch backtest run dir (``summary.json`` = list of per-symbol jobs)
       and each job's ``equity_curve.csv`` / ``trades.csv``;
    3. call :mod:`research.evaluation_tables` to assemble per-symbol rows + the
       metric coverage audit;
    4. write CSV / MD outputs.

All metric math lives in :mod:`research.evaluation_metrics`; all table assembly in
:mod:`research.evaluation_tables`. This script imports neither ``strategy`` nor
``feature_engine``. It does NOT run backtests, hit the network (beyond the lazy
pyarrow benchmark read of an already-downloaded local parquet), or touch raw
outputs. Symbols absent from the run are kept as ``missing_data`` rows; symbols
whose job failed are kept as ``failed`` rows (never dropped, never fabricated).

Outputs under ``--out-dir``:

    batch_evaluation_table.csv          (rows = symbol, full metric columns)
    batch_evaluation_table.md           (rows = symbol, compact columns)
    batch_metric_coverage_audit.csv     (per-metric status/source/reliability)
    batch_metric_coverage_audit.md
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

from research import evaluation_tables as et


def _load_sizing(path: Path | None) -> dict[str, dict]:
    """position_sizing.csv -> {SYMBOL: row}. Empty when no file."""
    if not path or not path.is_file():
        return {}
    with path.open(encoding="utf-8") as fh:
        return {str(r.get("symbol", "")).upper(): r for r in csv.DictReader(fh)}


def _load_summaries(backtest_root: Path) -> list[dict]:
    sj = backtest_root / "summary.json"
    if not sj.is_file():
        return []
    data = json.loads(sj.read_text(encoding="utf-8"))
    return [data] if isinstance(data, dict) else list(data)


def _job_dir(backtest_root: Path, summary: dict) -> Path:
    job = summary.get("job_id") or summary.get("output_dir")
    return backtest_root / Path(str(job)).name if job else backtest_root


def build_rows(backtest_root: Path, *, symbols: list[str], strategy: str, bar_type: str,
               start: str, end: str, data_root: Path | None,
               half_ratio: float = 0.5, vip_ratio: float = 0.2) -> list[dict]:
    """One row per requested symbol, preserving the requested order."""
    summaries = _load_summaries(backtest_root)
    by_symbol: dict[str, dict] = {}
    for s in summaries:
        sym = str(s.get("symbol") or "").upper()
        # keep the matching bar_type if multiple; else first seen for the symbol
        if sym and (sym not in by_symbol or s.get("bar_type") == bar_type):
            by_symbol[sym] = s

    rows: list[dict] = []
    for sym in symbols:
        s = by_symbol.get(sym)
        if s is None:
            rows.append(et.missing_data_row(
                sym, strategy=strategy, bar_type=bar_type, start=start, end=end,
                reason="no comparable backtest in run dir"))
            continue
        if str(s.get("status")) != "success":
            rows.append(et.failed_row(s, strategy=strategy))
            continue
        jd = _job_dir(backtest_root, s)
        equity_rows = et.read_csv_rows(jd / "equity_curve.csv")
        trades = et.read_csv_rows(jd / "trades.csv")
        bench = None
        if data_root is not None:
            bench = et.read_benchmark_closes(
                data_root, exchange=s.get("exchange") or "BINANCE",
                venue_type=s.get("venue_type") or "futures_um", symbol=sym,
                bar_type=s.get("bar_type") or bar_type,
                start=s.get("start") or start, end=s.get("end") or end)
        rows.append(et.build_symbol_row(
            s, equity_rows=equity_rows, trades=trades, benchmark_closes=bench,
            strategy=strategy, half_ratio=half_ratio, vip_ratio=vip_ratio))
    return rows


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Build batch strategy evaluation table (rows = symbol)")
    ap.add_argument("--backtest-root", required=True,
                    help="batch run dir containing summary.json + per-symbol job dirs")
    ap.add_argument("--data-root", default="historical_data/market_data")
    ap.add_argument("--out-dir", default=None, help="default: backtest-root")
    ap.add_argument("--strategy", default="VWM")
    ap.add_argument("--symbols", default="BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT")
    ap.add_argument("--bar-type", default="15m")
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--vip-fee-ratio", type=float, default=0.2)
    ap.add_argument("--half-fee-ratio", type=float, default=0.5)
    ap.add_argument("--sizing-file", default=None,
                    help="optional position_sizing.csv -> adds notional-normalization columns")
    ap.add_argument("--compare-fixed-table", default=None,
                    help="optional fixed-quantity batch_evaluation_table.csv -> writes normalization_comparison.csv")
    ap.add_argument("--no-overwrite", action="store_true",
                    help="refuse to overwrite an existing batch_evaluation_table")
    return ap


def run(args) -> tuple[list[dict], list[str]]:
    symbols = [s.strip().upper() for s in str(args.symbols).split(",") if s.strip()]
    rows = build_rows(
        Path(args.backtest_root), symbols=symbols, strategy=args.strategy,
        bar_type=args.bar_type, start=args.start, end=args.end,
        data_root=Path(args.data_root) if args.data_root else None,
        half_ratio=args.half_fee_ratio, vip_ratio=args.vip_fee_ratio)
    sizing = _load_sizing(Path(args.sizing_file) if getattr(args, "sizing_file", None) else None)
    if sizing:
        for r in rows:
            et.attach_sizing(r, sizing.get(str(r.get("Symbol", "")).upper()))
    return rows, symbols


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    bt = Path(args.backtest_root)
    out = Path(args.out_dir) if args.out_dir else bt
    table_csv = out / "batch_evaluation_table.csv"
    table_md = out / "batch_evaluation_table.md"
    cov_csv = out / "batch_metric_coverage_audit.csv"
    cov_md = out / "batch_metric_coverage_audit.md"
    if args.no_overwrite and any(p.exists() for p in (table_csv, table_md, cov_csv, cov_md)):
        print(f"REFUSING_OVERWRITE existing batch tables under {out}")
        return 3

    rows, symbols = run(args)
    has_sizing = bool(getattr(args, "sizing_file", None)) and any("Order Quantity" in r for r in rows)
    csv_cols = et.SYMBOL_METRIC_COLUMNS + (et.SIZING_COLUMNS if has_sizing else [])
    md_cols = et.MD_CORE_COLUMNS[:-2] + (["Order Quantity", "Initial Notional", "Realized Vol 15m"]
                                         if has_sizing else []) + et.MD_CORE_COLUMNS[-2:]
    et.write_table_csv(rows, table_csv, csv_cols)
    et.write_table_md(rows, table_md, md_cols)
    cov = et.build_coverage_rows(rows, primary_symbol=symbols[0] if symbols else None, columns=csv_cols)
    et.write_coverage_csv(cov, cov_csv)
    et.write_coverage_md(cov, cov_md)

    if getattr(args, "compare_fixed_table", None):
        fixed = et.read_table_csv(Path(args.compare_fixed_table))
        comp = et.build_comparison_rows(rows, fixed)
        comp_path = out / "normalization_comparison.csv"
        et.write_comparison_csv(comp, comp_path)
        print(f"COMPARISON_CSV {comp_path}")

    print(f"BATCH_TABLE_CSV {table_csv}")
    print(f"BATCH_TABLE_MD {table_md}")
    print(f"COVERAGE_CSV {cov_csv}")
    print(f"COVERAGE_MD {cov_md}")
    print(f"ROWS {len(rows)} (rows=symbol, cols=metric)")
    for r in rows:
        print(f"  {r.get('Symbol')}: status={r.get('Backtest Status')} "
              f"total={r.get('Total Return')} bench={r.get('Benchmark Return')} "
              f"excess={r.get('Excess Return')} zero_fee={r.get('Zero Fee Return')}")
    added = sum(1 for v in et.METRIC_AUDIT.values() if v[1] == "added")
    planned = sum(1 for v in et.METRIC_AUDIT.values() if v[1] == "planned")
    print(f"COVERAGE metrics={len(et.SYMBOL_METRIC_COLUMNS)} added={added} planned={planned}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
