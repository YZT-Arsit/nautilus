#!/usr/bin/env python3
"""Pivot-style batch strategy evaluation table (rows = metrics, cols = symbols).

The boss view: one strategy (VWM) evaluated across instruments, with a complete
metric system for strategy assessment and instrument screening. This script does
NOT run backtests or touch raw outputs - it reads existing per-window matrix runs
(``<matrix-root>_w*``) plus an optional single-experiment run, reuses the
single-experiment row builder in :mod:`scripts.build_crypto_perpetual_eval_table`,
adds the metrics that audit found missing (Calmar, fee drag, payoff, expectancy,
consecutive wins/losses, daily-return stats, matrix-stability, ...), and emits:

    <out-dir>/batch_evaluation_long.csv    (Symbol, Metric, Value triples)
    <out-dir>/batch_evaluation_pivot.csv   (rows = metric, cols = symbol)
    <out-dir>/batch_evaluation_pivot.md
    <out-dir>/metric_coverage_audit.csv    (per-metric status/source/note)
    <out-dir>/metric_coverage_audit.md

Symbols without a comparable backtest are kept as columns filled with NA and a
``missing_comparable_backtest`` status (never fabricated). Pure-Python, stdlib
only; no network, no backtest, no private endpoints.
"""
from __future__ import annotations

import argparse
import csv
import math
import statistics
from pathlib import Path
from typing import Any

import scripts.build_crypto_perpetual_eval_table as base

NA = base.NA
_DAY_NS = 86_400_000_000_000


# --- ordered pivot metric rows (the boss-facing metric system) --------------

METRIC_ROWS = [
    # basic
    "Strategy", "Market Type", "Exchange", "Symbol", "Contract Type", "Bar Type",
    "Window", "Start", "End", "Days", "Bars", "Status", "Failure Reason",
    # returns
    "Initial Cash", "Final Equity", "Net PnL", "Total Return", "Annualized Return",
    "Benchmark Return", "Excess Return", "Zero Fee Return", "VIP Fee 20% Return",
    "Fee Drag", "Calmar Ratio", "Return / Max Drawdown", "Best Day Return", "Worst Day Return",
    # risk
    "Max Drawdown", "Max Drawdown %", "Sharpe", "Sortino", "Volatility", "Downside Volatility",
    # trade quality
    "Trade Count", "Fill Count", "Long Trades", "Short Trades", "Win Rate", "Profit Factor",
    "Payoff Ratio", "Expectancy", "Avg Trade PnL", "Avg Win", "Avg Loss", "Median Trade PnL",
    "Best Trade", "Worst Trade", "Max Consecutive Wins", "Max Consecutive Losses",
    # exposure
    "Exposure %", "Long Exposure %", "Short Exposure %", "Flat %", "Net Direction Bias",
    "Avg Holding Time", "Max Holding Time", "Avg Holding Bars", "Max Holding Bars",
    # cost
    "Gross PnL", "Gross Profit", "Gross Loss", "Total Commission", "Commission / Initial Cash",
    "Commission / |Gross PnL|", "Commission / |Net PnL|", "Avg Commission / Trade",
    "Avg Commission / Fill", "Net / Gross Ratio", "Break-even Commission",
    "Break-even Fee Ratio", "Turnover",
    # matrix stability
    "Positive Return Windows", "Positive Excess Windows", "Positive Excess Ratio",
    "Mean Excess Across Windows", "Std Excess Across Windows", "Best Window", "Worst Window",
    "Best Bar Type", "Worst Bar Type",
    # perpetual mechanism
    "Funding Modeled", "Funding Data Available", "Funding-adjusted Return", "Margin Modeled",
    "Liquidation Modeled", "Mark Price Modeled", "Mark Price Data Available",
    # note
    "Caveat",
]

# Always-known structural rows that we can fill even for a missing symbol.
_STRUCTURAL_FOR_MISSING = {
    "Market Type": "crypto_perpetual", "Exchange": "BINANCE",
    "Contract Type": "USD-M perpetual", "Funding Modeled": "No",
    "Funding Data Available": "No", "Funding-adjusted Return": NA,
    "Margin Modeled": "No", "Liquidation Modeled": "No", "Mark Price Modeled": "No",
    "Mark Price Data Available": "No",
}

# metric -> (status, source, note) for the coverage audit.
#   covered = already produced by the single/matrix builders
#   added   = newly computed this phase from existing outputs
#   planned = not reliably computable from current outputs (NA)
_AUDIT = {
    "Strategy": ("added", "cli", "from --strategy"),
    "Failure Reason": ("added", "derived", "missing-backtest reason"),
    "Annualized Return": ("added", "computed", "(1+ret)^(365/days)-1"),
    "Fee Drag": ("added", "computed", "zero-fee return - actual return"),
    "Calmar Ratio": ("added", "computed", "annualized return / max drawdown %"),
    "Return / Max Drawdown": ("added", "computed", "total return / max drawdown %"),
    "Best Day Return": ("added", "equity_curve", "daily resample"),
    "Worst Day Return": ("added", "equity_curve", "daily resample"),
    "Max Drawdown": ("added", "equity_curve", "absolute peak-trough"),
    "Downside Volatility": ("added", "equity_curve", "annualized downside std"),
    "Payoff Ratio": ("added", "summary", "avg win / |avg loss|"),
    "Expectancy": ("added", "summary", "win*avgWin + loss*avgLoss"),
    "Median Trade PnL": ("added", "trades", "median realized pnl"),
    "Best Trade": ("added", "trades", "max realized pnl"),
    "Worst Trade": ("added", "trades", "min realized pnl"),
    "Max Consecutive Wins": ("added", "trades", "by realized-pnl sign"),
    "Max Consecutive Losses": ("added", "trades", "by realized-pnl sign"),
    "Net Direction Bias": ("added", "computed", "long% - short%"),
    "Net / Gross Ratio": ("added", "computed", "net pnl / gross pnl"),
    "Break-even Commission": ("added", "computed", "gross pnl (fees affordable to break even)"),
    "Positive Return Windows": ("added", "matrix", "cells with total return > 0"),
    "Positive Excess Windows": ("added", "matrix", "cells with excess > 0"),
    "Positive Excess Ratio": ("added", "matrix", "positive-excess cells / cells"),
    "Mean Excess Across Windows": ("added", "matrix", "mean excess over cells"),
    "Std Excess Across Windows": ("added", "matrix", "std excess over cells"),
    "Best Window": ("added", "matrix", "window of max-excess cell"),
    "Worst Window": ("added", "matrix", "window of min-excess cell"),
    "Best Bar Type": ("added", "matrix", "bar type of max-excess cell"),
    "Worst Bar Type": ("added", "matrix", "bar type of min-excess cell"),
    "Funding Data Available": ("planned", "n/a", "funding rate not ingested"),
    "Funding-adjusted Return": ("planned", "n/a", "funding not in PnL"),
    "Mark Price Data Available": ("planned", "n/a", "mark price not ingested"),
}


def _audit_for(metric: str) -> tuple[str, str, str]:
    return _AUDIT.get(metric, ("covered", "single/matrix builder", "pre-existing metric"))


# --- numeric helpers --------------------------------------------------------

def _num(v: Any) -> float | None:
    return float(v) if base._finite(v) else None


def daily_returns(equity_rows: list[dict]) -> list[float]:
    """Resample bar equity to one point per UTC day, then daily simple returns."""
    by_day: dict[int, float] = {}
    for r in equity_rows:
        ns = r.get("event_time_ns")
        eq = r.get("equity")
        if not base._finite(ns) or not base._finite(eq):
            continue
        by_day[int(ns) // _DAY_NS] = float(eq)         # last bar of the day wins
    eq = [by_day[d] for d in sorted(by_day)]
    return [eq[i] / eq[i - 1] - 1.0 for i in range(1, len(eq)) if eq[i - 1] != 0.0]


def daily_stats(equity_rows: list[dict]) -> dict[str, float | None]:
    rets = daily_returns(equity_rows)
    if not rets:
        return {"best_day": None, "worst_day": None, "avg_day": None, "std_day": None}
    return {"best_day": max(rets), "worst_day": min(rets),
            "avg_day": sum(rets) / len(rets),
            "std_day": statistics.pstdev(rets) if len(rets) > 1 else 0.0}


def downside_volatility(equity_rows: list[dict], *, bars_per_day: int) -> float | None:
    """Annualized downside std from bar-level returns (matches base Volatility units)."""
    eq = [float(r["equity"]) for r in equity_rows if base._finite(r.get("equity"))]
    rets = [eq[i] / eq[i - 1] - 1.0 for i in range(1, len(eq)) if eq[i - 1] != 0.0]
    downside = [r for r in rets if r < 0.0]
    if len(downside) < 2:
        return None
    dstd = math.sqrt(sum(r * r for r in downside) / len(downside))
    return dstd * math.sqrt(bars_per_day * 365.0)


def trade_pnl_stats(trades: list[dict]) -> dict[str, float | int | None]:
    pnls = []
    for t in trades:
        try:
            pnls.append(float(t["realized_pnl"]))
        except (KeyError, TypeError, ValueError):
            pass
    if not pnls:
        return {"median": None, "best": None, "worst": None,
                "max_consec_wins": None, "max_consec_losses": None}
    mw = ml = cw = cl = 0
    for p in pnls:
        if p > 0:
            cw += 1; cl = 0
        elif p < 0:
            cl += 1; cw = 0
        else:
            cw = cl = 0
        mw = max(mw, cw); ml = max(ml, cl)
    return {"median": statistics.median(pnls), "best": max(pnls), "worst": min(pnls),
            "max_consec_wins": mw, "max_consec_losses": ml}


def payoff_ratio(avg_win: Any, avg_loss: Any) -> float | None:
    if not base._finite(avg_win) or not base._finite(avg_loss) or float(avg_loss) == 0.0:
        return None
    return float(avg_win) / abs(float(avg_loss))


def expectancy(win_rate: Any, avg_win: Any, avg_loss: Any) -> float | None:
    if not all(base._finite(x) for x in (win_rate, avg_win, avg_loss)):
        return None
    wr = float(win_rate)
    return wr * float(avg_win) + (1.0 - wr) * float(avg_loss)


def benchmark_direction(bench: Any) -> str:
    if not base._finite(bench):
        return NA
    b = float(bench)
    return "up" if b > 1e-9 else ("down" if b < -1e-9 else "flat")


def strategy_direction_bias(long_pct: Any, short_pct: Any) -> str:
    if not base._finite(long_pct) or not base._finite(short_pct):
        return NA
    d = float(long_pct) - float(short_pct)
    return "long" if d > 1e-9 else ("short" if d < -1e-9 else "neutral")


# --- cell loading (one bar_type x window backtest job) ----------------------

def _coerce_equity(rows: list[dict]) -> list[dict]:
    for r in rows:
        for k in ("close", "position", "equity", "event_time_ns"):
            try:
                r[k] = float(r[k])
            except (KeyError, TypeError, ValueError):
                r[k] = float("nan")
    return rows


def load_cell(summary: dict, job_dir: Path) -> dict:
    """Single backtest cell -> flat dict of base row + added metrics."""
    equity_rows = _coerce_equity(base._read_csv_rows(job_dir / "equity_curve.csv"))
    trades = base._read_csv_rows(job_dir / "trades.csv")
    row = dict(base.build_eval_row(summary, equity_rows=equity_rows, trades=trades,
                                   benchmark_closes=None))
    bar_seconds = base._bar_seconds(summary.get("bar_type") or "5m")
    bars_per_day = max(1, round(86400 / bar_seconds))

    days = base._days(summary.get("start"), summary.get("end"))
    total_return = _num(summary.get("total_return"))
    mdd_pct = _num(row.get("Max DD %"))
    ann = base._annualized_return(total_return, days)
    ds = daily_stats(equity_rows)
    tp = trade_pnl_stats(trades)
    est = base.equity_stats([r.get("equity") for r in equity_rows], bars_per_day=bars_per_day)

    zero_fee = _num(row.get("Zero Fee Return"))
    gross_pnl = _num(row.get("Gross PnL"))
    net_pnl = _num(summary.get("net_pnl"))
    long_pct = _num(row.get("Long Exposure %"))
    short_pct = _num(row.get("Short Exposure %"))

    row["Window"] = {7: "7d", 30: "30d", 90: "90d"}.get(days, f"{days}d" if days else NA)
    row["Max Drawdown %"] = row.get("Max DD %")        # spec label alias
    row["Trade Count"] = row.get("Trades")             # spec label alias
    row["Fill Count"] = summary.get("fill_count")
    row["Avg Trade PnL"] = summary.get("avg_trade_pnl")
    row["Avg Win"] = summary.get("avg_win")
    row["Avg Loss"] = summary.get("avg_loss")
    row["Annualized Return"] = ann
    row["Fee Drag"] = (zero_fee - total_return) if (zero_fee is not None and total_return is not None) else None
    row["Calmar Ratio"] = (ann / mdd_pct) if (ann is not None and mdd_pct) else None
    row["Return / Max Drawdown"] = (total_return / mdd_pct) if (total_return is not None and mdd_pct) else None
    row["Best Day Return"] = ds["best_day"]
    row["Worst Day Return"] = ds["worst_day"]
    row["Avg Daily Return"] = ds["avg_day"]
    row["Daily Return Std"] = ds["std_day"]
    row["Max Drawdown"] = est.get("max_drawdown_abs")
    row["Downside Volatility"] = downside_volatility(equity_rows, bars_per_day=bars_per_day)
    row["Payoff Ratio"] = payoff_ratio(summary.get("avg_win"), summary.get("avg_loss"))
    row["Expectancy"] = expectancy(summary.get("win_rate"), summary.get("avg_win"), summary.get("avg_loss"))
    row["Median Trade PnL"] = tp["median"]
    row["Best Trade"] = tp["best"]
    row["Worst Trade"] = tp["worst"]
    row["Max Consecutive Wins"] = tp["max_consec_wins"]
    row["Max Consecutive Losses"] = tp["max_consec_losses"]
    row["Net Direction Bias"] = (long_pct - short_pct) if (long_pct is not None and short_pct is not None) else None
    row["Net / Gross Ratio"] = (net_pnl / gross_pnl) if (net_pnl is not None and gross_pnl not in (None, 0.0)) else None
    row["Break-even Commission"] = max(gross_pnl, 0.0) if gross_pnl is not None else None
    row["Benchmark Direction"] = benchmark_direction(row.get("Benchmark Return"))
    row["Strategy Direction Bias"] = strategy_direction_bias(long_pct, short_pct)
    # spec label aliases
    row["Commission / |Gross PnL|"] = row.get("Commission / Gross PnL")
    row["Commission / |Net PnL|"] = row.get("Commission / Net PnL")
    row["Index Price Modeled"] = "No"
    row["Funding Data Available"] = "No"
    row["Funding-adjusted Return"] = NA
    row["Mark Price Data Available"] = "No"
    return {k: base._fmt(v) for k, v in row.items()}


def discover_cells(matrix_root: Path, single_root: Path | None) -> list[dict]:
    """All cells from per-window matrix run dirs (+ optional single run), deduped."""
    import json  # noqa: PLC0415
    parent = matrix_root.parent
    run_dirs = sorted(p for p in parent.glob(matrix_root.name + "_w*")
                      if p.is_dir() and (p / "summary.json").is_file())
    if single_root and (single_root / "summary.json").is_file():
        run_dirs.append(single_root)
    cells: list[dict] = []
    seen: set[tuple] = set()
    for rd in run_dirs:
        summaries = json.loads((rd / "summary.json").read_text(encoding="utf-8"))
        if isinstance(summaries, dict):
            summaries = [summaries]
        for s in summaries:
            job = s.get("job_id") or s.get("output_dir")
            job_dir = rd / Path(str(job)).name if job else rd
            cell = load_cell(s, job_dir)
            key = (cell.get("Symbol"), cell.get("Bar Type"), cell.get("Window"))
            if key in seen:
                continue
            seen.add(key)
            cells.append(cell)
    return cells


# --- per-symbol assembly ----------------------------------------------------

def stability(cells: list[dict]) -> dict[str, Any]:
    ex = [(_num(c.get("Excess Return")), c.get("Window"), c.get("Bar Type")) for c in cells]
    ex_ok = [(v, w, b) for (v, w, b) in ex if v is not None]
    rets = [_num(c.get("Total Return")) for c in cells]
    rets_ok = [r for r in rets if r is not None]
    out = {
        "Positive Return Windows": sum(1 for r in rets_ok if r > 0),
        "Positive Excess Windows": sum(1 for v, _, _ in ex_ok if v > 0),
        "Positive Excess Ratio": (sum(1 for v, _, _ in ex_ok if v > 0) / len(ex_ok)) if ex_ok else None,
        "Mean Excess Across Windows": (sum(v for v, _, _ in ex_ok) / len(ex_ok)) if ex_ok else None,
        "Std Excess Across Windows": (statistics.pstdev([v for v, _, _ in ex_ok]) if len(ex_ok) > 1 else (0.0 if ex_ok else None)),
        "Best Window": None, "Worst Window": None, "Best Bar Type": None, "Worst Bar Type": None,
    }
    if ex_ok:
        best = max(ex_ok, key=lambda t: t[0]); worst = min(ex_ok, key=lambda t: t[0])
        out["Best Window"], out["Best Bar Type"] = best[1], best[2]
        out["Worst Window"], out["Worst Bar Type"] = worst[1], worst[2]
    return out


def symbol_metrics(symbol: str, cells: list[dict], *, strategy: str,
                   pref_bar: str, pref_window: str) -> dict[str, Any]:
    sym_cells = [c for c in cells if c.get("Symbol") == symbol]
    if not sym_cells:
        out = {m: NA for m in METRIC_ROWS}
        out.update(_STRUCTURAL_FOR_MISSING)
        out["Strategy"] = strategy
        out["Symbol"] = symbol
        out["Status"] = "missing_comparable_backtest"
        out["Failure Reason"] = f"no {pref_bar}x{pref_window} result yet"
        out["Caveat"] = "no comparable backtest for this symbol"
        return out

    preferred = next((c for c in sym_cells
                      if c.get("Bar Type") == pref_bar and c.get("Window") == pref_window), None)
    stab = stability(sym_cells)
    if preferred is None:
        # symbol has data but not the preferred cell -> structural + stability only
        out = {m: NA for m in METRIC_ROWS}
        out.update(_STRUCTURAL_FOR_MISSING)
        out.update({k: v for k, v in stab.items()})
        out["Strategy"] = strategy
        out["Symbol"] = symbol
        out["Status"] = "missing_preferred_cell"
        out["Failure Reason"] = f"has data but no {pref_bar}x{pref_window} cell"
        out["Caveat"] = f"stability across {len(sym_cells)} cells; preferred cell absent"
        return out

    c = preferred
    out: dict[str, Any] = {}
    for m in METRIC_ROWS:
        out[m] = c.get(m, NA)
    out["Strategy"] = strategy
    out["Failure Reason"] = ""
    out.update(stab)
    out["Caveat"] = c.get("Caveat", NA)
    return out


# --- output -----------------------------------------------------------------

def _cell(v: Any) -> str:
    v = base._fmt(v)
    return f"{v:.6g}" if isinstance(v, float) else str(v)


def write_long(per_symbol: dict[str, dict], path: Path, symbols: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh); w.writerow(["Symbol", "Metric", "Value"])
        for sym in symbols:
            for m in METRIC_ROWS:
                w.writerow([sym, m, _cell(per_symbol[sym].get(m, NA))])


def write_pivot_csv(per_symbol: dict[str, dict], path: Path, symbols: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh); w.writerow(["Metric"] + symbols)
        for m in METRIC_ROWS:
            w.writerow([m] + [_cell(per_symbol[s].get(m, NA)) for s in symbols])


def write_pivot_md(per_symbol: dict[str, dict], path: Path, symbols: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["| Metric | " + " | ".join(symbols) + " |",
             "| --- | " + " | ".join("---" for _ in symbols) + " |"]
    for m in METRIC_ROWS:
        lines.append(f"| {m} | " + " | ".join(_cell(per_symbol[s].get(m, NA)) for s in symbols) + " |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_coverage(per_symbol: dict[str, dict], symbols: list[str], primary: str,
                   csv_path: Path, md_path: Path) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for m in METRIC_ROWS:
        status, source, note = _audit_for(m)
        val = per_symbol.get(primary, {}).get(m, NA)
        available = "yes" if val not in (NA, "", None) else "no"
        rows.append({"Metric": m, "Status": status, "Source": source,
                     "Available(%s)" % primary: available, "Note": note})
    cols = ["Metric", "Status", "Source", "Available(%s)" % primary, "Note"]
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols); w.writeheader()
        for r in rows:
            w.writerow(r)
    lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join("---" for _ in cols) + " |"]
    for r in rows:
        lines.append("| " + " | ".join(str(r[c]) for c in cols) + " |")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Build pivot-style batch strategy evaluation table")
    ap.add_argument("--matrix-root", default="outputs/backtests/vwm_btcusdt_perpetual_matrix")
    ap.add_argument("--single-root", default="outputs/backtests/vwm_btcusdt_perpetual_5m_eval")
    ap.add_argument("--out-dir", default="outputs/backtests/vwm_strategy_batch_eval")
    ap.add_argument("--strategy", default="VWM")
    ap.add_argument("--symbols", default="BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT")
    ap.add_argument("--preferred-bar-type", default="15m")
    ap.add_argument("--preferred-window", default="90d")
    ap.add_argument("--allow-missing-symbols", action="store_true")
    return ap


def run(args) -> dict[str, dict]:
    symbols = [s.strip().upper() for s in str(args.symbols).split(",") if s.strip()]
    cells = discover_cells(Path(args.matrix_root),
                           Path(args.single_root) if args.single_root else None)
    have = {c.get("Symbol") for c in cells}
    missing = [s for s in symbols if s not in have]
    if missing and not args.allow_missing_symbols:
        raise ValueError(f"missing comparable backtests for {missing}; pass --allow-missing-symbols")
    per_symbol = {s: symbol_metrics(s, cells, strategy=args.strategy,
                                    pref_bar=args.preferred_bar_type,
                                    pref_window=args.preferred_window) for s in symbols}
    return per_symbol


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    symbols = [s.strip().upper() for s in str(args.symbols).split(",") if s.strip()]
    per_symbol = run(args)
    out = Path(args.out_dir)
    primary = symbols[0]
    write_long(per_symbol, out / "batch_evaluation_long.csv", symbols)
    write_pivot_csv(per_symbol, out / "batch_evaluation_pivot.csv", symbols)
    write_pivot_md(per_symbol, out / "batch_evaluation_pivot.md", symbols)
    write_coverage(per_symbol, symbols, primary,
                   out / "metric_coverage_audit.csv", out / "metric_coverage_audit.md")
    print(f"BATCH_LONG {out / 'batch_evaluation_long.csv'}")
    print(f"BATCH_PIVOT_CSV {out / 'batch_evaluation_pivot.csv'}")
    print(f"BATCH_PIVOT_MD {out / 'batch_evaluation_pivot.md'}")
    print(f"COVERAGE_CSV {out / 'metric_coverage_audit.csv'}")
    print(f"COVERAGE_MD {out / 'metric_coverage_audit.md'}")
    print(f"SYMBOLS {symbols}")
    for s in symbols:
        print(f"  {s}: Status={per_symbol[s].get('Status')} TotalReturn={per_symbol[s].get('Total Return')} "
              f"Excess={per_symbol[s].get('Excess Return')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
