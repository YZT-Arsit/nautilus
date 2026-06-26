#!/usr/bin/env python3
"""Build a boss-readable evaluation table from a VWM backtest output dir.

Reads ``summary.json`` (a list of per-job metric dicts produced by
``run_vwm_batch_backtests.py``) plus, when present, each job's
``equity_curve.csv`` and ``trades.csv``, and emits a stable wide table:

    outputs/backtests/<run>/evaluation_table.csv
    outputs/backtests/<run>/evaluation_table.md

Rows = markets (one per backtest job), so the same table trivially extends to
ETHUSDT / SOLUSDT / BNBUSDT later. Existing summary fields are used as-is;
``Days`` / ``Annualized Return`` and (when an equity curve is available)
``Sharpe`` / ``Sortino`` / ``Volatility`` / absolute ``Max Drawdown`` /
``Turnover`` are computed. Anything that cannot be computed reliably is left as
``NA`` (never fabricated). Pure-Python (stdlib only) so it is unit-testable
without pandas, network, or running a backtest.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from datetime import date
from pathlib import Path
from typing import Any

NA = "NA"
BARS_PER_DAY_DEFAULT = 288  # 5m bars
SHORT_SAMPLE_DAYS = 30      # below this, annualized/risk ratios are flagged indicative

# Ordered output columns (exactly the boss-facing schema).
EVAL_COLUMNS = [
    "Market Type", "Exchange", "Symbol", "Contract Type", "Bar Type",
    "Start Date", "End Date", "Days", "Bars", "Initial Cash", "Final Equity",
    "Net PnL", "Total Return", "Annualized Return", "Max Drawdown",
    "Max Drawdown %", "Sharpe", "Sortino", "Volatility", "Trade Count",
    "Fill Count", "Long Trades", "Short Trades", "Win Rate", "Profit Factor",
    "Avg Trade PnL", "Avg Win", "Avg Loss", "Total Commission",
    "Commission / Gross PnL", "Turnover", "Status", "Caveat",
]

# venue_type -> (Market Type, Contract Type)
_VENUE_MAP = {
    "futures_um": ("crypto_perpetual", "USD-M perpetual"),
    "futures_cm": ("crypto_perpetual", "COIN-M perpetual"),
    "spot": ("crypto_spot", "spot"),
}

_BASE_CAVEAT = "funding/liquidation/margin/mark-index not modeled"


def _finite(x: Any) -> bool:
    try:
        return isinstance(x, (int, float)) and math.isfinite(float(x)) and not isinstance(x, bool)
    except (TypeError, ValueError):
        return False


def _days(start: str, end: str) -> int | None:
    try:
        a = date.fromisoformat(str(start))
        b = date.fromisoformat(str(end))
        return (b - a).days + 1
    except (TypeError, ValueError):
        return None


def _annualized_return(total_return: Any, days: int | None) -> float | None:
    if not _finite(total_return) or not days or days <= 0:
        return None
    base = 1.0 + float(total_return)
    if base <= 0.0:  # total loss; annualized power is undefined/degenerate
        return None
    return base ** (365.0 / days) - 1.0


def equity_stats(equity: list[float], *, bars_per_day: int) -> dict[str, float | None]:
    """Annualized volatility / Sharpe / Sortino and absolute max drawdown.

    Risk ratios use simple per-bar returns, risk-free 0, annualization factor
    ``sqrt(bars_per_day * 365)``. Returns ``None`` for any stat that needs more
    data than is present.
    """
    out: dict[str, float | None] = {"volatility": None, "sharpe": None,
                                    "sortino": None, "max_drawdown_abs": None}
    eq = [float(x) for x in equity if _finite(x)]
    if len(eq) >= 2:
        peak = eq[0]
        max_dd = 0.0
        for v in eq:
            peak = max(peak, v)
            max_dd = max(max_dd, peak - v)
        out["max_drawdown_abs"] = max_dd
    rets = [eq[i] / eq[i - 1] - 1.0 for i in range(1, len(eq)) if eq[i - 1] != 0.0]
    n = len(rets)
    if n >= 2:
        mean = sum(rets) / n
        var = sum((r - mean) ** 2 for r in rets) / (n - 1)
        std = math.sqrt(var)
        ann = math.sqrt(bars_per_day * 365.0)
        out["volatility"] = std * ann
        if std > 0.0:
            out["sharpe"] = (mean / std) * ann
        downside = [r for r in rets if r < 0.0]
        if downside:
            dvar = sum(r * r for r in downside) / len(downside)
            dstd = math.sqrt(dvar)
            if dstd > 0.0:
                out["sortino"] = (mean / dstd) * ann
    return out


def turnover_from_trades(trades: list[dict], initial_cash: Any) -> float | None:
    """Gross traded notional / initial cash (entry+exit legs)."""
    if not trades or not _finite(initial_cash) or float(initial_cash) == 0.0:
        return None
    notional = 0.0
    ok = False
    for t in trades:
        try:
            q = abs(float(t.get("quantity")))
            ep = abs(float(t.get("entry_price")))
            xp = abs(float(t.get("exit_price")))
        except (TypeError, ValueError):
            continue
        notional += q * ep + q * xp
        ok = True
    return notional / float(initial_cash) if ok else None


def _fmt(x: Any) -> Any:
    if x is None or (isinstance(x, float) and not math.isfinite(x)):
        return NA
    return x


def build_eval_row(
    summary: dict,
    *,
    equity: list[float] | None = None,
    trades: list[dict] | None = None,
    bars_per_day: int = BARS_PER_DAY_DEFAULT,
    market_type: str | None = None,
    contract_type: str | None = None,
) -> dict[str, Any]:
    """Map one summary job dict (+optional equity/trades) to an eval-table row."""
    venue = str(summary.get("venue_type") or "")
    mt, ct = _VENUE_MAP.get(venue, ("crypto_perpetual", "USD-M perpetual"))
    mt = market_type or mt
    ct = contract_type or ct

    days = _days(summary.get("start"), summary.get("end"))
    ann = _annualized_return(summary.get("total_return"), days)

    est = equity_stats(equity, bars_per_day=bars_per_day) if equity else {}

    def pick(field: str, computed_key: str | None = None):
        """Prefer a finite summary field; else the computed value; else None."""
        v = summary.get(field)
        if _finite(v):
            return v
        if computed_key is not None and est.get(computed_key) is not None:
            return est[computed_key]
        return None

    sharpe = pick("sharpe", "sharpe")
    sortino = pick("sortino", "sortino")
    volatility = pick("volatility", "volatility")

    # Max Drawdown: absolute dollars (computed from equity) when available;
    # Max Drawdown %: the summary fraction.
    mdd_abs = est.get("max_drawdown_abs")
    mdd_pct = summary.get("max_drawdown_pct")
    if not _finite(mdd_pct):
        mdd_pct = summary.get("max_drawdown")

    turnover = summary.get("turnover")
    if not _finite(turnover):
        turnover = turnover_from_trades(trades or [], summary.get("initial_cash"))

    caveats = [_BASE_CAVEAT]
    if days is not None and days < SHORT_SAMPLE_DAYS:
        caveats.append(f"short sample ({days}d): annualized/Sharpe/Sortino/vol indicative only")

    row = {
        "Market Type": mt,
        "Exchange": summary.get("exchange") or NA,
        "Symbol": summary.get("symbol") or NA,
        "Contract Type": ct,
        "Bar Type": summary.get("bar_type") or NA,
        "Start Date": summary.get("start") or NA,
        "End Date": summary.get("end") or NA,
        "Days": days,
        "Bars": summary.get("num_bars"),
        "Initial Cash": summary.get("initial_cash"),
        "Final Equity": summary.get("final_equity"),
        "Net PnL": summary.get("net_pnl"),
        "Total Return": summary.get("total_return"),
        "Annualized Return": ann,
        "Max Drawdown": mdd_abs,
        "Max Drawdown %": mdd_pct,
        "Sharpe": sharpe,
        "Sortino": sortino,
        "Volatility": volatility,
        "Trade Count": summary.get("trade_count"),
        "Fill Count": summary.get("fill_count"),
        "Long Trades": summary.get("long_trade_count"),
        "Short Trades": summary.get("short_trade_count"),
        "Win Rate": summary.get("win_rate"),
        "Profit Factor": summary.get("profit_factor"),
        "Avg Trade PnL": summary.get("avg_trade_pnl"),
        "Avg Win": summary.get("avg_win"),
        "Avg Loss": summary.get("avg_loss"),
        "Total Commission": summary.get("total_commission"),
        "Commission / Gross PnL": summary.get("commission_to_gross_pnl"),
        "Turnover": turnover,
        "Status": summary.get("status") or NA,
        "Caveat": "; ".join(caveats),
    }
    return {k: _fmt(v) for k, v in row.items()}


def build_eval_rows(summaries: list[dict], **kw) -> list[dict[str, Any]]:
    return [build_eval_row(s, **kw) for s in summaries]


def rows_to_csv(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=EVAL_COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, NA) for c in EVAL_COLUMNS})


def _md_cell(v: Any) -> str:
    if isinstance(v, float):
        return f"{v:.6g}"
    return str(v)


def rows_to_md(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["| " + " | ".join(EVAL_COLUMNS) + " |",
             "| " + " | ".join("---" for _ in EVAL_COLUMNS) + " |"]
    for r in rows:
        lines.append("| " + " | ".join(_md_cell(r.get(c, NA)) for c in EVAL_COLUMNS) + " |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# --- disk I/O (CLI side) ----------------------------------------------------

def _read_equity(job_dir: Path) -> list[float]:
    fp = job_dir / "equity_curve.csv"
    if not fp.is_file():
        return []
    out = []
    with fp.open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            try:
                out.append(float(row["equity"]))
            except (KeyError, TypeError, ValueError):
                pass
    return out


def _read_trades(job_dir: Path) -> list[dict]:
    fp = job_dir / "trades.csv"
    if not fp.is_file():
        return []
    with fp.open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def load_and_build(backtest_dir: Path, *, bars_per_day: int = BARS_PER_DAY_DEFAULT) -> list[dict]:
    summary = json.loads((backtest_dir / "summary.json").read_text(encoding="utf-8"))
    if isinstance(summary, dict):
        summary = [summary]
    rows = []
    for s in summary:
        job = s.get("job_id") or s.get("output_dir")
        job_dir = backtest_dir / Path(str(job)).name if job else backtest_dir
        rows.append(build_eval_row(
            s, equity=_read_equity(job_dir), trades=_read_trades(job_dir),
            bars_per_day=bars_per_day,
        ))
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Build crypto-perpetual VWM evaluation table")
    ap.add_argument("--backtest-dir", required=True,
                    help="run dir containing summary.json (e.g. outputs/backtests/vwm_btcusdt_perpetual_5m_eval)")
    ap.add_argument("--out", default=None, help="output dir (default: backtest-dir)")
    ap.add_argument("--bars-per-day", type=int, default=BARS_PER_DAY_DEFAULT)
    args = ap.parse_args(argv)

    bt = Path(args.backtest_dir)
    if not (bt / "summary.json").is_file():
        print(f"ERROR: no summary.json under {bt}")
        return 2
    out_dir = Path(args.out) if args.out else bt
    rows = load_and_build(bt, bars_per_day=args.bars_per_day)
    csv_path = out_dir / "evaluation_table.csv"
    md_path = out_dir / "evaluation_table.md"
    rows_to_csv(rows, csv_path)
    rows_to_md(rows, md_path)
    print(f"EVAL_TABLE_CSV {csv_path}")
    print(f"EVAL_TABLE_MD {md_path}")
    print(f"ROWS {len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
