#!/usr/bin/env python3
"""Aggregate BTCUSDT-perpetual VWM matrix runs (bar_type x window) into one table.

``run_vwm_batch_backtests`` applies a single global window per invocation, so the
matrix is produced as one run *per window* into sibling dirs named
``<prefix>_7d`` / ``<prefix>_30d`` / ``<prefix>_90d`` (each holding the 3 bar_type
jobs). This script discovers those run dirs, reuses the single-experiment row
builder from :mod:`scripts.build_crypto_perpetual_eval_table`, adds a ``Window``
column, and writes:

    <out-dir>/matrix_evaluation_table.csv   (full columns + Window)
    <out-dir>/matrix_evaluation_table.md    (core columns + Window)
    <out-dir>/matrix_ranking.csv            (ranked by excess return + net_score)
    <out-dir>/matrix_ranking.md

Missing/failed jobs are kept as rows with ``Status != success`` and NA metrics
(never dropped, never fabricated). Pure-Python core; pyarrow only for the
optional benchmark read (lazy, inside the reused builder). No network, no
backtest execution.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from typing import Any

import scripts.build_crypto_perpetual_eval_table as base

NA = base.NA

# matrix table = single-experiment full schema with a Window column after Bar Type.
_FULL = base.FULL_COLUMNS
_bar_idx = _FULL.index("Bar Type")
MATRIX_FULL_COLUMNS = _FULL[:_bar_idx + 1] + ["Window"] + _FULL[_bar_idx + 1:]
MATRIX_CORE_COLUMNS = [
    "Symbol", "Bar Type", "Window", "Start", "End", "Bars", "Total Return",
    "Benchmark Return", "Excess Return", "Zero Fee Return", "VIP Fee 20% Return",
    "Max DD %", "Sharpe", "Trades", "Win Rate", "Profit Factor",
    "Commission / Gross PnL", "Exposure %", "Short Exposure %", "Status", "Caveat",
]
RANKING_COLUMNS = [
    "Rank", "Symbol", "Bar Type", "Window", "Total Return", "Benchmark Return",
    "Excess Return", "Zero Fee Return", "Max DD %", "Profit Factor",
    "Commission / Gross PnL", "Exposure %", "Net Score", "Status",
]

# net_score weights (normalized components; sample is small -> sort aid only).
_W_EXCESS, _W_DD, _W_PF, _W_COMM = 0.40, 0.25, 0.20, 0.15


def _window_label(days: Any) -> str:
    try:
        d = int(days)
    except (TypeError, ValueError):
        return NA
    return {7: "7d", 30: "30d", 90: "90d"}.get(d, f"{d}d")


def build_matrix_rows(backtest_root: Path, *, run_prefix: str, data_root: Path | None,
                      half_ratio: float = 0.5, vip_ratio: float = 0.2,
                      symbol: str | None = None, exchange: str | None = None,
                      venue_type: str | None = None) -> list[dict]:
    """Discover ``<prefix>*`` run dirs under backtest_root and build matrix rows."""
    run_dirs = sorted(p for p in backtest_root.glob(run_prefix + "*")
                      if p.is_dir() and (p / "summary.json").is_file())
    rows: list[dict] = []
    for rd in run_dirs:
        sub = base.load_and_build(
            rd, data_root=data_root, exchange=exchange, venue_type=venue_type,
            symbol=symbol, half_ratio=half_ratio, vip_ratio=vip_ratio,
        )
        for r in sub:
            r = dict(r)
            r["Window"] = _window_label(r.get("Days"))
            rows.append(r)
    return rows


# --- ranking ----------------------------------------------------------------

def _num(v: Any) -> float | None:
    return float(v) if base._finite(v) else None


def _normalize(vals: list[float | None], *, higher_better: bool) -> list[float]:
    present = [v for v in vals if v is not None]
    if not present:
        return [0.0 for _ in vals]
    lo, hi = min(present), max(present)
    rng = hi - lo
    out = []
    for v in vals:
        if v is None:
            out.append(0.0)
        elif rng == 0.0:
            out.append(0.5)
        else:
            n = (v - lo) / rng
            out.append(n if higher_better else 1.0 - n)
    return out


def rank_rows(rows: list[dict]) -> list[dict]:
    """Attach Net Score and return rows sorted best-first.

    net_score = 0.40*norm(excess) + 0.25*norm(-maxdd) + 0.20*norm(profit_factor)
                + 0.15*norm(-commission_pressure). Only ``success`` rows are
    scored/ranked; failed rows sort last with NA score.
    """
    ok = [r for r in rows if r.get("Status") == "success"]
    bad = [r for r in rows if r.get("Status") != "success"]

    excess = _normalize([_num(r.get("Excess Return")) for r in ok], higher_better=True)
    dd = _normalize([_num(r.get("Max DD %")) for r in ok], higher_better=False)
    pf = _normalize([_num(r.get("Profit Factor")) for r in ok], higher_better=True)
    comm = _normalize([_num(r.get("Commission / Gross PnL")) for r in ok], higher_better=False)

    for i, r in enumerate(ok):
        r["Net Score"] = (_W_EXCESS * excess[i] + _W_DD * dd[i]
                          + _W_PF * pf[i] + _W_COMM * comm[i])

    def sort_key(r):
        return (-(r.get("Net Score") or 0.0),
                -(_num(r.get("Excess Return")) or -1e9),
                (_num(r.get("Max DD %")) if _num(r.get("Max DD %")) is not None else 1e9))
    ok.sort(key=sort_key)
    for r in bad:
        r["Net Score"] = None
    ranked = ok + bad
    for i, r in enumerate(ranked, 1):
        r["Rank"] = i
    return ranked


# --- output -----------------------------------------------------------------

def _fmt(v: Any) -> Any:
    if v is None or (isinstance(v, float) and not math.isfinite(v)):
        return NA
    return v


def write_csv(rows: list[dict], path: Path, columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=columns)
        w.writeheader()
        for r in rows:
            w.writerow({c: _fmt(r.get(c, NA)) for c in columns})


def _md_cell(v: Any) -> str:
    v = _fmt(v)
    return f"{v:.6g}" if isinstance(v, float) else str(v)


def write_md(rows: list[dict], path: Path, columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["| " + " | ".join(columns) + " |",
             "| " + " | ".join("---" for _ in columns) + " |"]
    for r in rows:
        lines.append("| " + " | ".join(_md_cell(r.get(c, NA)) for c in columns) + " |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Build BTCUSDT-perpetual VWM matrix eval + ranking")
    ap.add_argument("--backtest-root", default="outputs/backtests",
                    help="parent dir holding the per-window run dirs")
    ap.add_argument("--run-prefix", default="vwm_btcusdt_perpetual_matrix_",
                    help="prefix of per-window run dirs (e.g. ..._7d/_30d/_90d)")
    ap.add_argument("--data-root", default="historical_data/market_data")
    ap.add_argument("--out-dir", default="outputs/backtests/vwm_btcusdt_perpetual_matrix")
    ap.add_argument("--symbol", default=None)
    ap.add_argument("--exchange", default=None)
    ap.add_argument("--venue-type", default=None)
    ap.add_argument("--vip-fee-ratio", type=float, default=0.2)
    ap.add_argument("--half-fee-ratio", type=float, default=0.5)
    ap.add_argument("--no-overwrite", action="store_true")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    bt_root = Path(args.backtest_root)
    out_dir = Path(args.out_dir)
    targets = [out_dir / n for n in ("matrix_evaluation_table.csv", "matrix_evaluation_table.md",
                                     "matrix_ranking.csv", "matrix_ranking.md")]
    if args.no_overwrite and any(t.exists() for t in targets):
        print(f"REFUSING_OVERWRITE existing matrix tables under {out_dir}")
        return 3

    rows = build_matrix_rows(
        bt_root, run_prefix=args.run_prefix,
        data_root=Path(args.data_root) if args.data_root else None,
        half_ratio=args.half_fee_ratio, vip_ratio=args.vip_fee_ratio,
        symbol=args.symbol, exchange=args.exchange, venue_type=args.venue_type,
    )
    if not rows:
        print(f"ERROR: no matrix run dirs found under {bt_root} matching {args.run_prefix}*")
        return 2

    ranked = rank_rows([dict(r) for r in rows])

    write_csv(rows, out_dir / "matrix_evaluation_table.csv", MATRIX_FULL_COLUMNS)
    write_md(rows, out_dir / "matrix_evaluation_table.md", MATRIX_CORE_COLUMNS)
    write_csv(ranked, out_dir / "matrix_ranking.csv", RANKING_COLUMNS)
    write_md(ranked, out_dir / "matrix_ranking.md", RANKING_COLUMNS)

    print(f"MATRIX_EVAL_CSV {out_dir / 'matrix_evaluation_table.csv'}")
    print(f"MATRIX_EVAL_MD {out_dir / 'matrix_evaluation_table.md'}")
    print(f"MATRIX_RANKING_CSV {out_dir / 'matrix_ranking.csv'}")
    print(f"MATRIX_RANKING_MD {out_dir / 'matrix_ranking.md'}")
    print(f"ROWS {len(rows)}")
    for r in ranked[:3]:
        print(f"  #{r['Rank']} {r['Bar Type']}x{r['Window']}: excess={r.get('Excess Return')} "
              f"net_score={r.get('Net Score')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
