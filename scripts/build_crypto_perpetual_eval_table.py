#!/usr/bin/env python3
"""Build a boss-readable evaluation table from a VWM backtest output dir.

Reads ``summary.json`` (a list of per-job metric dicts produced by
``run_vwm_batch_backtests.py``) plus each job's ``equity_curve.csv`` (close +
position per bar) and ``trades.csv``, and (optionally) the BTCUSDT bar parquet
for a buy-and-hold benchmark. Emits a stable wide table:

    <out-dir>/evaluation_table.csv   (full column set)
    <out-dir>/evaluation_table.md    (core columns)

Rows = markets (one per job), so the table extends to ETH/SOL/BNB later. This is
the single-experiment / matrix builder; the rows=symbol *batch* view lives in
``scripts/build_strategy_batch_eval_table.py``. All metric math is centralized in
:mod:`research.evaluation_metrics` and re-exported here under the historical
names so this module's public API (and the matrix builder that reuses it) stays
stable. Pure-Python core (stdlib only); pyarrow is imported lazily *only* for the
optional benchmark read. No network, no backtest execution.
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

from research import evaluation_metrics as em

# Pure metric math lives in research.evaluation_metrics; re-exported here under the
# historical names this builder, its matrix sibling, and the tests depend on.
NA = em.NA
_finite = em.is_finite
_fmt = em.fmt_na
_days = em.days_inclusive
_bar_seconds = em.bar_seconds
_annualized_return = em.annualized_return
benchmark_return = em.benchmark_return
fee_scenarios = em.fee_scenarios
exposure_from_positions = em.exposure_from_positions
holding_from_trades = em.holding_from_trades
gross_from_trades = em.gross_from_trades
equity_stats = em.equity_stats
turnover_from_trades = em.turnover_from_trades

SHORT_SAMPLE_DAYS = 30      # below this, annualized/risk ratios are flagged indicative

# venue_type -> (Market Type, Contract Type)
_VENUE_MAP = {
    "futures_um": ("crypto_perpetual", "USD-M perpetual"),
    "futures_cm": ("crypto_perpetual", "COIN-M perpetual"),
    "spot": ("crypto_spot", "spot"),
}
_BASE_CAVEAT = "funding/liquidation/margin/mark-index not modeled"

# Full CSV schema (boss-facing superset).
FULL_COLUMNS = [
    "Market Type", "Exchange", "Symbol", "Contract Type", "Bar Type",
    "Start", "End", "Days", "Bars", "Initial Cash", "Final Equity",
    "Net PnL", "Total Return", "Benchmark Return", "Excess Return",
    "Zero Fee Return", "Zero Fee Final Equity", "Zero Fee Excess Return",
    "Half Fee Return", "VIP Fee 20% Return", "Break-even Fee Ratio",
    "Zero Fee Profitable", "Net Without Commission", "Fee Sensitivity Note",
    "Max DD %", "Sharpe", "Sortino", "Volatility",
    "Trades", "Long Trades", "Short Trades", "Win Rate", "Profit Factor",
    "Gross PnL", "Gross Profit", "Gross Loss",
    "Total Commission", "Commission / Initial Cash", "Commission / Gross PnL",
    "Commission / Net PnL", "Avg Commission / Trade", "Avg Commission / Fill",
    "Turnover", "Exposure %", "Long Exposure %", "Short Exposure %", "Flat %",
    "Avg Holding Time", "Avg Holding Bars", "Max Holding Time", "Max Holding Bars",
    "Funding Modeled", "Margin Modeled", "Liquidation Modeled", "Mark Price Modeled",
    "Status", "Caveat",
]

# Core MD schema (compact).
CORE_COLUMNS = [
    "Market Type", "Symbol", "Bar Type", "Days", "Bars", "Total Return",
    "Benchmark Return", "Excess Return", "Zero Fee Return", "VIP Fee 20% Return",
    "Max DD %", "Trades", "Win Rate", "Profit Factor", "Commission / Gross PnL",
    "Exposure %", "Short Exposure %", "Status", "Caveat",
]


# --- row assembly -----------------------------------------------------------

def build_eval_row(
    summary: dict,
    *,
    equity_rows: list[dict] | None = None,
    trades: list[dict] | None = None,
    benchmark_closes: tuple[float, float] | None = None,
    bar_seconds: int | None = None,
    half_ratio: float = 0.5,
    vip_ratio: float = 0.2,
    market_type: str | None = None,
    contract_type: str | None = None,
) -> dict[str, Any]:
    equity_rows = equity_rows or []
    trades = trades or []
    bar_seconds = bar_seconds or _bar_seconds(summary.get("bar_type") or "5m")
    bars_per_day = max(1, round(86400 / bar_seconds))

    venue = str(summary.get("venue_type") or "")
    mt, ct = _VENUE_MAP.get(venue, ("crypto_perpetual", "USD-M perpetual"))
    mt = market_type or mt
    ct = contract_type or ct

    days = _days(summary.get("start"), summary.get("end"))
    total_return = summary.get("total_return")
    initial_cash = summary.get("initial_cash")

    # benchmark: explicit closes, else first/last close from equity curve.
    if benchmark_closes is not None:
        bench = benchmark_return(*benchmark_closes)
    else:
        closes = [r.get("close") for r in equity_rows if _finite(r.get("close"))]
        bench = benchmark_return(closes[0], closes[-1]) if len(closes) >= 2 else None
    excess = (float(total_return) - bench) if (_finite(total_return) and bench is not None) else None

    fees = fee_scenarios(summary.get("net_pnl"), summary.get("total_commission"),
                         initial_cash, half_ratio=half_ratio, vip_ratio=vip_ratio)
    zero = fees.get("zero", {}); half = fees.get("half", {}); vip = fees.get("vip", {})
    zero_excess = (zero["total_return"] - bench) if (zero and bench is not None) else None

    equity = [r.get("equity") for r in equity_rows]
    est = equity_stats(equity, bars_per_day=bars_per_day) if equity else {}
    exp = exposure_from_positions([r.get("position") for r in equity_rows]) if equity_rows else {}
    hold = holding_from_trades(trades, bar_seconds=bar_seconds)
    gr = gross_from_trades(trades)

    def pick(field, computed=None):
        v = summary.get(field)
        if _finite(v):
            return v
        return est.get(computed) if computed else None

    mdd_pct = summary.get("max_drawdown_pct")
    if not _finite(mdd_pct):
        mdd_pct = summary.get("max_drawdown")

    gross_pnl = gr.get("gross_pnl")
    if gross_pnl is None and _finite(summary.get("gross_realized_pnl")):
        gross_pnl = summary.get("gross_realized_pnl")

    total_commission = summary.get("total_commission")
    net_pnl = summary.get("net_pnl")
    fill_count = summary.get("fill_count")
    comm_to_init = (float(total_commission) / float(initial_cash)
                    if _finite(total_commission) and _finite(initial_cash) and float(initial_cash) else None)
    comm_to_net = (float(total_commission) / abs(float(net_pnl))
                   if _finite(total_commission) and _finite(net_pnl) and float(net_pnl) != 0 else None)
    comm_per_fill = (float(total_commission) / float(fill_count)
                     if _finite(total_commission) and _finite(fill_count) and float(fill_count) else None)
    turnover = summary.get("turnover")
    if not _finite(turnover):
        turnover = turnover_from_trades(trades, initial_cash)

    caveats = [_BASE_CAVEAT]
    if days is not None and days < SHORT_SAMPLE_DAYS:
        caveats.append(f"short sample ({days}d): annualized/Sharpe/Sortino/vol indicative only")

    row = {
        "Market Type": mt, "Exchange": summary.get("exchange") or NA,
        "Symbol": summary.get("symbol") or NA, "Contract Type": ct,
        "Bar Type": summary.get("bar_type") or NA,
        "Start": summary.get("start") or NA, "End": summary.get("end") or NA,
        "Days": days, "Bars": summary.get("num_bars"),
        "Initial Cash": initial_cash, "Final Equity": summary.get("final_equity"),
        "Net PnL": net_pnl, "Total Return": total_return,
        "Benchmark Return": bench, "Excess Return": excess,
        "Zero Fee Return": zero.get("total_return"),
        "Zero Fee Final Equity": zero.get("final_equity"),
        "Zero Fee Excess Return": zero_excess,
        "Half Fee Return": half.get("total_return"),
        "VIP Fee 20% Return": vip.get("total_return"),
        "Break-even Fee Ratio": fees.get("break_even_fee_ratio_vs_current"),
        "Zero Fee Profitable": ("Yes" if fees.get("zero_fee_profitable") else "No") if fees else NA,
        "Net Without Commission": fees.get("net_without_commission"),
        "Fee Sensitivity Note": fees.get("fee_sensitivity_note") or NA,
        "Max DD %": mdd_pct, "Sharpe": pick("sharpe", "sharpe"),
        "Sortino": pick("sortino", "sortino"), "Volatility": pick("volatility", "volatility"),
        "Trades": summary.get("trade_count"),
        "Long Trades": summary.get("long_trade_count"),
        "Short Trades": summary.get("short_trade_count"),
        "Win Rate": summary.get("win_rate"), "Profit Factor": summary.get("profit_factor"),
        "Gross PnL": gross_pnl, "Gross Profit": gr.get("gross_profit"),
        "Gross Loss": gr.get("gross_loss"),
        "Total Commission": total_commission,
        "Commission / Initial Cash": comm_to_init,
        "Commission / Gross PnL": summary.get("commission_to_gross_pnl"),
        "Commission / Net PnL": comm_to_net,
        "Avg Commission / Trade": summary.get("avg_commission_per_trade"),
        "Avg Commission / Fill": comm_per_fill, "Turnover": turnover,
        "Exposure %": exp.get("exposure_pct"),
        "Long Exposure %": exp.get("long_exposure_pct"),
        "Short Exposure %": exp.get("short_exposure_pct"),
        "Flat %": exp.get("flat_pct"),
        "Avg Holding Time": hold.get("avg_holding_minutes"),
        "Avg Holding Bars": hold.get("avg_holding_bars"),
        "Max Holding Time": hold.get("max_holding_minutes"),
        "Max Holding Bars": hold.get("max_holding_bars"),
        "Funding Modeled": "No", "Margin Modeled": "No",
        "Liquidation Modeled": "No", "Mark Price Modeled": "No",
        "Status": summary.get("status") or NA, "Caveat": "; ".join(caveats),
    }
    return {k: _fmt(v) for k, v in row.items()}


def build_eval_rows(summaries: list[dict], **kw) -> list[dict[str, Any]]:
    return [build_eval_row(s, **kw) for s in summaries]


# --- output -----------------------------------------------------------------

def rows_to_csv(rows: list[dict], path: Path, columns: list[str] = FULL_COLUMNS) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=columns)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, NA) for c in columns})


def _md_cell(v: Any) -> str:
    return f"{v:.6g}" if isinstance(v, float) else str(v)


def rows_to_md(rows: list[dict], path: Path, columns: list[str] = CORE_COLUMNS) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["| " + " | ".join(columns) + " |",
             "| " + " | ".join("---" for _ in columns) + " |"]
    for r in rows:
        lines.append("| " + " | ".join(_md_cell(r.get(c, NA)) for c in columns) + " |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# --- disk readers (CLI side) ------------------------------------------------

def _read_csv_rows(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def read_benchmark_closes(data_root: Path, *, exchange: str, venue_type: str, symbol: str,
                          bar_type: str, start: str, end: str) -> tuple[float, float] | None:
    """First/last close over [start, end] from the bar parquet (lazy pyarrow).

    Returns ``None`` (caller fills NA) if pyarrow is missing or no bars found.
    """
    base = (data_root / f"exchange={exchange}" / f"venue_type={venue_type}"
            / f"symbol={symbol}" / f"bar_type={bar_type}")
    if not base.is_dir():
        return None
    try:
        import pyarrow.parquet as pq  # noqa: PLC0415
    except Exception:
        return None
    rows: list[tuple[int, float]] = []
    for part in sorted(base.glob("date=*/part-*.parquet")):
        day = part.parent.name.replace("date=", "")
        if not (start <= day <= end):
            continue
        try:
            t = pq.read_table(part, columns=["ts", "close"])
        except Exception:
            continue
        ts = t.column("ts").to_pylist(); cl = t.column("close").to_pylist()
        for a, b in zip(ts, cl):
            try:
                rows.append((int(a), float(b)))
            except (TypeError, ValueError):
                pass
    if len(rows) < 2:
        return None
    rows.sort(key=lambda r: r[0])
    return rows[0][1], rows[-1][1]


def load_and_build(backtest_root: Path, *, data_root: Path | None = None,
                   exchange: str | None = None, venue_type: str | None = None,
                   symbol: str | None = None, bar_type: str | None = None,
                   start: str | None = None, end: str | None = None,
                   half_ratio: float = 0.5, vip_ratio: float = 0.2) -> list[dict]:
    summary = json.loads((backtest_root / "summary.json").read_text(encoding="utf-8"))
    if isinstance(summary, dict):
        summary = [summary]
    rows = []
    for s in summary:
        job = s.get("job_id") or s.get("output_dir")
        job_dir = backtest_root / Path(str(job)).name if job else backtest_root
        equity_rows = _read_csv_rows(job_dir / "equity_curve.csv")
        # coerce close/position/equity to float
        for r in equity_rows:
            for k in ("close", "position", "equity"):
                try:
                    r[k] = float(r[k])
                except (KeyError, TypeError, ValueError):
                    r[k] = float("nan")
        trades = _read_csv_rows(job_dir / "trades.csv")
        bench = None
        if data_root is not None:
            bench = read_benchmark_closes(
                data_root,
                exchange=exchange or s.get("exchange"),
                venue_type=venue_type or s.get("venue_type"),
                symbol=symbol or s.get("symbol"),
                bar_type=bar_type or s.get("bar_type"),
                start=start or s.get("start"), end=end or s.get("end"),
            )
        rows.append(build_eval_row(
            s, equity_rows=equity_rows, trades=trades, benchmark_closes=bench,
            half_ratio=half_ratio, vip_ratio=vip_ratio,
        ))
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Build crypto-perpetual VWM evaluation table")
    ap.add_argument("--backtest-root", required=True,
                    help="run dir containing summary.json")
    ap.add_argument("--data-root", default="historical_data/market_data")
    ap.add_argument("--out-dir", default=None, help="default: backtest-root")
    ap.add_argument("--symbol", default=None)
    ap.add_argument("--exchange", default=None)
    ap.add_argument("--venue-type", default=None)
    ap.add_argument("--bar-type", default=None)
    ap.add_argument("--start", default=None)
    ap.add_argument("--end", default=None)
    ap.add_argument("--vip-fee-ratio", type=float, default=0.2)
    ap.add_argument("--half-fee-ratio", type=float, default=0.5)
    ap.add_argument("--no-overwrite", action="store_true",
                    help="refuse to overwrite an existing evaluation_table")
    args = ap.parse_args(argv)

    bt = Path(args.backtest_root)
    if not (bt / "summary.json").is_file():
        print(f"ERROR: no summary.json under {bt}")
        return 2
    out_dir = Path(args.out_dir) if args.out_dir else bt
    csv_path = out_dir / "evaluation_table.csv"
    md_path = out_dir / "evaluation_table.md"
    if args.no_overwrite and (csv_path.exists() or md_path.exists()):
        print(f"REFUSING_OVERWRITE existing evaluation_table under {out_dir}")
        return 3

    data_root = Path(args.data_root) if args.data_root else None
    rows = load_and_build(
        bt, data_root=data_root, exchange=args.exchange, venue_type=args.venue_type,
        symbol=args.symbol, bar_type=args.bar_type, start=args.start, end=args.end,
        half_ratio=args.half_fee_ratio, vip_ratio=args.vip_fee_ratio,
    )
    rows_to_csv(rows, csv_path)
    rows_to_md(rows, md_path)
    print(f"EVAL_TABLE_CSV {csv_path}")
    print(f"EVAL_TABLE_MD {md_path}")
    print(f"ROWS {len(rows)}")
    for r in rows:
        print(f"  {r['Symbol']}: total={r['Total Return']} bench={r['Benchmark Return']} "
              f"excess={r['Excess Return']} zero_fee={r['Zero Fee Return']} "
              f"vip={r['VIP Fee 20% Return']} breakeven={r['Break-even Fee Ratio']} "
              f"exposure={r['Exposure %']} short_exp={r['Short Exposure %']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
