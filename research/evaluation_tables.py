#!/usr/bin/env python3
"""Batch strategy-evaluation table assembly + writers (rows = symbol).

This is the boss-facing table layer: one strategy (VWM) across many instruments,
**one row per symbol, one column per evaluation metric**. It composes the pure
math in :mod:`research.evaluation_metrics` into a flat per-symbol metric row and
renders CSV / Markdown plus a per-metric coverage audit.

Responsibilities kept here (and ONLY here):

* column schemas (``SYMBOL_METRIC_COLUMNS`` full CSV, ``MD_CORE_COLUMNS`` compact MD);
* per-symbol row assembly (``build_symbol_row`` / ``missing_data_row`` / ``failed_row``);
* table writers (``write_table_csv`` / ``write_table_md``);
* the metric coverage audit (``METRIC_AUDIT`` + ``build_coverage_rows`` + writers).

Disk I/O is limited to small reporting-side readers (``read_csv_rows`` for a job's
equity/trades CSV, and ``read_benchmark_closes`` which lazily imports pyarrow only
to read a bar parquet's first/last close). Imports nothing from ``strategy`` /
``feature_engine`` / ``data_engine`` / ``nautilus_trader``; no network, no
backtest. Anything not reliably computable is left ``NA`` (never fabricated).
"""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from research import evaluation_metrics as em

NA = em.NA
SHORT_SAMPLE_DAYS = 30          # below this, annualized/risk ratios are indicative only
_BASE_CAVEAT = "funding/liquidation/margin/mark-index not modeled"

# venue_type -> (Market Type, Contract Type)
_VENUE_MAP = {
    "futures_um": ("crypto_perpetual", "USD-M perpetual"),
    "futures_cm": ("crypto_perpetual", "COIN-M perpetual"),
    "spot": ("crypto_spot", "spot"),
}

# Full per-symbol CSV schema (rows = symbol, cols = these metrics). Order mirrors
# the metric-system spec: basic / returns / risk / trade-quality / exposure /
# cost / relative / perpetual-mechanism / note.
SYMBOL_METRIC_COLUMNS = [
    # basic
    "Strategy", "Market Type", "Exchange", "Symbol", "Contract Type", "Bar Type",
    "Start", "End", "Days", "Expected Bars", "Actual Bars",
    "Data Quality Status", "Backtest Status", "Failure Reason",
    # returns
    "Initial Cash", "Final Equity", "Net PnL", "Total Return", "Annualized Return",
    "Benchmark Return", "Excess Return", "Zero Fee Return", "Half Fee Return",
    "VIP Fee 20% Return", "Fee Drag", "Calmar Ratio", "Return / Max Drawdown",
    "Best Day Return", "Worst Day Return", "Avg Daily Return", "Daily Return Std",
    # risk
    "Max Drawdown", "Max Drawdown %", "Sharpe", "Sortino", "Volatility", "Downside Volatility",
    # trade quality
    "Trade Count", "Fill Count", "Long Trades", "Short Trades", "Win Rate",
    "Profit Factor", "Payoff Ratio", "Expectancy", "Avg Trade PnL", "Avg Win",
    "Avg Loss", "Median Trade PnL", "Best Trade", "Worst Trade",
    "Max Consecutive Wins", "Max Consecutive Losses",
    # exposure
    "Exposure %", "Long Exposure %", "Short Exposure %", "Flat %", "Net Direction Bias",
    "Avg Holding Time", "Max Holding Time", "Avg Holding Bars", "Max Holding Bars",
    # cost
    "Gross PnL", "Gross Profit", "Gross Loss", "Total Commission",
    "Commission / Initial Cash", "Commission / |Gross PnL|", "Commission / |Net PnL|",
    "Avg Commission / Trade", "Avg Commission / Fill", "Net / Gross Ratio",
    "Break-even Commission", "Break-even Fee Ratio", "Turnover",
    # relative
    "Benchmark Direction", "Strategy Direction Bias", "Zero Fee Excess Return",
    # perpetual mechanism
    "Funding Modeled", "Funding Data Available", "Funding-adjusted Return",
    "Margin Modeled", "Liquidation Modeled", "Mark Price Modeled", "Mark Price Data Available",
    # note
    "Caveat",
]

# Compact MD column set (boss summary view).
MD_CORE_COLUMNS = [
    "Symbol", "Bar Type", "Days", "Actual Bars", "Total Return", "Benchmark Return",
    "Excess Return", "Zero Fee Return", "VIP Fee 20% Return", "Max Drawdown %",
    "Sharpe", "Trade Count", "Win Rate", "Profit Factor", "Exposure %",
    "Short Exposure %", "Commission / |Gross PnL|", "Fee Drag", "Backtest Status", "Caveat",
]

# Optional notional-normalization columns (appended only when a sizing file is supplied).
SIZING_COLUMNS = ["Sizing Method", "Target Notional USDT", "Order Quantity", "Actual Initial Notional"]

# Structural fields we can always state, even when a symbol has no backtest.
_STRUCTURAL = {
    "Market Type": "crypto_perpetual", "Exchange": "BINANCE",
    "Contract Type": "USD-M perpetual",
    "Funding Modeled": "No", "Funding Data Available": "No", "Funding-adjusted Return": NA,
    "Margin Modeled": "No", "Liquidation Modeled": "No",
    "Mark Price Modeled": "No", "Mark Price Data Available": "No",
}


# --- small reporting-side readers --------------------------------------------

def read_csv_rows(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def coerce_equity(rows: list[dict]) -> list[dict]:
    """In-place float coercion of equity-curve columns used by the metrics."""
    for r in rows:
        for k in ("close", "position", "equity", "event_time_ns"):
            try:
                r[k] = float(r[k])
            except (KeyError, TypeError, ValueError):
                r[k] = float("nan")
    return rows


def read_benchmark_closes(data_root: Path, *, exchange: str, venue_type: str, symbol: str,
                          bar_type: str, start: str, end: str) -> tuple[float, float] | None:
    """First/last close over ``[start, end]`` from the bar parquet (lazy pyarrow).

    Returns ``None`` (caller renders NA) if pyarrow is missing or no bars found.
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


# --- data-quality helpers ---------------------------------------------------

def expected_bars(days: int | None, bar_type: str) -> int | None:
    if not days or days <= 0:
        return None
    per_day = max(1, round(86400 / em.bar_seconds(bar_type)))
    return days * per_day


def data_quality_status(expected: int | None, actual: int | None) -> str:
    if not actual:
        return "missing"
    if expected is None:
        return "unknown"
    if actual == expected:
        return "ok"
    return "partial" if actual < expected else "extra"


# --- per-symbol row assembly ------------------------------------------------

def build_symbol_row(summary: dict, *, equity_rows: list[dict] | None = None,
                     trades: list[dict] | None = None,
                     benchmark_closes: tuple[float, float] | None = None,
                     strategy: str = "VWM", half_ratio: float = 0.5, vip_ratio: float = 0.2,
                     market_type: str | None = None, contract_type: str | None = None,
                     ) -> dict[str, Any]:
    """One successful backtest summary (+ its equity/trades) -> full metric row."""
    equity_rows = coerce_equity(equity_rows or [])
    trades = trades or []
    bar_type = summary.get("bar_type") or "15m"
    bar_seconds = em.bar_seconds(bar_type)
    bars_per_day = max(1, round(86400 / bar_seconds))

    venue = str(summary.get("venue_type") or "")
    mt, ct = _VENUE_MAP.get(venue, ("crypto_perpetual", "USD-M perpetual"))
    mt = market_type or mt
    ct = contract_type or ct

    days = em.days_inclusive(summary.get("start"), summary.get("end"))
    total_return = summary.get("total_return")
    initial_cash = summary.get("initial_cash")
    net_pnl = summary.get("net_pnl")

    # benchmark: explicit closes else first/last close from the equity curve.
    if benchmark_closes is not None:
        bench = em.benchmark_return(*benchmark_closes)
    else:
        closes = [r.get("close") for r in equity_rows if em.is_finite(r.get("close"))]
        bench = em.benchmark_return(closes[0], closes[-1]) if len(closes) >= 2 else None
    excess = (float(total_return) - bench) if (em.is_finite(total_return) and bench is not None) else None

    fees = em.fee_scenarios(net_pnl, summary.get("total_commission"), initial_cash,
                            half_ratio=half_ratio, vip_ratio=vip_ratio)
    zero = fees.get("zero", {}); half = fees.get("half", {}); vip = fees.get("vip", {})
    zero_ret = zero.get("total_return")
    zero_excess = (zero_ret - bench) if (zero and bench is not None) else None

    est = em.equity_stats([r.get("equity") for r in equity_rows], bars_per_day=bars_per_day) if equity_rows else {}
    exp = em.exposure_from_positions([r.get("position") for r in equity_rows]) if equity_rows else {}
    hold = em.holding_from_trades(trades, bar_seconds=bar_seconds)
    gr = em.gross_from_trades(trades)
    ds = em.daily_stats(equity_rows)
    tp = em.trade_pnl_stats(trades)

    ann = em.annualized_return(total_return, days)
    mdd_pct = summary.get("max_drawdown_pct")
    if not em.is_finite(mdd_pct):
        mdd_pct = summary.get("max_drawdown")
    mdd_pct_num = float(mdd_pct) if em.is_finite(mdd_pct) else None

    gross_pnl = gr.get("gross_pnl")
    if gross_pnl is None and em.is_finite(summary.get("gross_realized_pnl")):
        gross_pnl = summary.get("gross_realized_pnl")
    gross_pnl_num = float(gross_pnl) if em.is_finite(gross_pnl) else None

    total_commission = summary.get("total_commission")
    fill_count = summary.get("fill_count")
    long_pct = exp.get("long_exposure_pct")
    short_pct = exp.get("short_exposure_pct")
    zero_num = float(zero_ret) if em.is_finite(zero_ret) else None
    tr_num = float(total_return) if em.is_finite(total_return) else None
    net_num = float(net_pnl) if em.is_finite(net_pnl) else None

    sharpe = summary.get("sharpe") if em.is_finite(summary.get("sharpe")) else est.get("sharpe")
    sortino = summary.get("sortino") if em.is_finite(summary.get("sortino")) else est.get("sortino")
    vol = summary.get("volatility") if em.is_finite(summary.get("volatility")) else est.get("volatility")
    turnover = summary.get("turnover")
    if not em.is_finite(turnover):
        turnover = em.turnover_from_trades(trades, initial_cash)

    exp_bars = expected_bars(days, bar_type)
    act_bars = summary.get("num_bars") if em.is_finite(summary.get("num_bars")) else (len(equity_rows) or None)

    caveats = [_BASE_CAVEAT]
    if days is not None and days < SHORT_SAMPLE_DAYS:
        caveats.append(f"short sample ({days}d): annualized/Sharpe/Sortino/vol indicative only")

    row = {
        "Strategy": strategy, "Market Type": mt, "Exchange": summary.get("exchange") or NA,
        "Symbol": summary.get("symbol") or NA, "Contract Type": ct, "Bar Type": bar_type,
        "Start": summary.get("start") or NA, "End": summary.get("end") or NA, "Days": days,
        "Expected Bars": exp_bars, "Actual Bars": act_bars,
        "Data Quality Status": data_quality_status(exp_bars, act_bars),
        "Backtest Status": summary.get("status") or NA, "Failure Reason": summary.get("error_message") or "",
        "Initial Cash": initial_cash, "Final Equity": summary.get("final_equity"),
        "Net PnL": net_pnl, "Total Return": total_return, "Annualized Return": ann,
        "Benchmark Return": bench, "Excess Return": excess,
        "Zero Fee Return": zero_ret, "Half Fee Return": half.get("total_return"),
        "VIP Fee 20% Return": vip.get("total_return"),
        "Fee Drag": (zero_num - tr_num) if (zero_num is not None and tr_num is not None) else None,
        "Calmar Ratio": (ann / mdd_pct_num) if (ann is not None and mdd_pct_num) else None,
        "Return / Max Drawdown": (tr_num / mdd_pct_num) if (tr_num is not None and mdd_pct_num) else None,
        "Best Day Return": ds["best_day"], "Worst Day Return": ds["worst_day"],
        "Avg Daily Return": ds["avg_day"], "Daily Return Std": ds["std_day"],
        "Max Drawdown": est.get("max_drawdown_abs"), "Max Drawdown %": mdd_pct,
        "Sharpe": sharpe, "Sortino": sortino, "Volatility": vol,
        "Downside Volatility": em.downside_volatility(equity_rows, bars_per_day=bars_per_day),
        "Trade Count": summary.get("trade_count"), "Fill Count": fill_count,
        "Long Trades": summary.get("long_trade_count"), "Short Trades": summary.get("short_trade_count"),
        "Win Rate": summary.get("win_rate"), "Profit Factor": summary.get("profit_factor"),
        "Payoff Ratio": em.payoff_ratio(summary.get("avg_win"), summary.get("avg_loss")),
        "Expectancy": em.expectancy(summary.get("win_rate"), summary.get("avg_win"), summary.get("avg_loss")),
        "Avg Trade PnL": summary.get("avg_trade_pnl"), "Avg Win": summary.get("avg_win"),
        "Avg Loss": summary.get("avg_loss"), "Median Trade PnL": tp["median"],
        "Best Trade": tp["best"], "Worst Trade": tp["worst"],
        "Max Consecutive Wins": tp["max_consec_wins"], "Max Consecutive Losses": tp["max_consec_losses"],
        "Exposure %": exp.get("exposure_pct"), "Long Exposure %": long_pct,
        "Short Exposure %": short_pct, "Flat %": exp.get("flat_pct"),
        "Net Direction Bias": (long_pct - short_pct) if (em.is_finite(long_pct) and em.is_finite(short_pct)) else None,
        "Avg Holding Time": hold.get("avg_holding_minutes"), "Max Holding Time": hold.get("max_holding_minutes"),
        "Avg Holding Bars": hold.get("avg_holding_bars"), "Max Holding Bars": hold.get("max_holding_bars"),
        "Gross PnL": gross_pnl, "Gross Profit": gr.get("gross_profit"), "Gross Loss": gr.get("gross_loss"),
        "Total Commission": total_commission,
        "Commission / Initial Cash": em.safe_div(total_commission, initial_cash),
        "Commission / |Gross PnL|": summary.get("commission_to_gross_pnl")
        if em.is_finite(summary.get("commission_to_gross_pnl"))
        else (em.safe_div(total_commission, abs(gross_pnl_num)) if gross_pnl_num else None),
        "Commission / |Net PnL|": em.safe_div(total_commission, abs(net_num)) if net_num else None,
        "Avg Commission / Trade": summary.get("avg_commission_per_trade"),
        "Avg Commission / Fill": em.safe_div(total_commission, fill_count),
        "Net / Gross Ratio": (net_num / gross_pnl_num) if (net_num is not None and gross_pnl_num) else None,
        "Break-even Commission": max(gross_pnl_num, 0.0) if gross_pnl_num is not None else None,
        "Break-even Fee Ratio": fees.get("break_even_fee_ratio_vs_current"), "Turnover": turnover,
        "Benchmark Direction": em.benchmark_direction(bench),
        "Strategy Direction Bias": em.strategy_direction_bias(long_pct, short_pct),
        "Zero Fee Excess Return": zero_excess,
        "Funding Modeled": "No", "Funding Data Available": "No", "Funding-adjusted Return": NA,
        "Margin Modeled": "No", "Liquidation Modeled": "No",
        "Mark Price Modeled": "No", "Mark Price Data Available": "No",
        "Caveat": "; ".join(caveats),
    }
    return {k: em.fmt_na(v) for k, v in row.items()}


def missing_data_row(symbol: str, *, strategy: str, bar_type: str, start: str, end: str,
                     reason: str, status: str = "missing_data") -> dict[str, Any]:
    """Symbol with no comparable backtest: structural rows + NA, never fabricated."""
    row = {m: NA for m in SYMBOL_METRIC_COLUMNS}
    row.update(_STRUCTURAL)
    row.update({
        "Strategy": strategy, "Symbol": symbol, "Bar Type": bar_type,
        "Start": start, "End": end, "Days": em.days_inclusive(start, end),
        "Expected Bars": expected_bars(em.days_inclusive(start, end), bar_type),
        "Actual Bars": NA, "Data Quality Status": "missing",
        "Backtest Status": status, "Failure Reason": reason,
        "Caveat": "no comparable backtest for this symbol; " + _BASE_CAVEAT,
    })
    return row


def failed_row(summary: dict, *, strategy: str, reason: str | None = None) -> dict[str, Any]:
    """Symbol whose backtest ran but failed: structural + status=failed, NA metrics."""
    row = {m: NA for m in SYMBOL_METRIC_COLUMNS}
    row.update(_STRUCTURAL)
    days = em.days_inclusive(summary.get("start"), summary.get("end"))
    bar_type = summary.get("bar_type") or "15m"
    row.update({
        "Strategy": strategy, "Symbol": summary.get("symbol") or NA,
        "Exchange": summary.get("exchange") or "BINANCE", "Bar Type": bar_type,
        "Start": summary.get("start") or NA, "End": summary.get("end") or NA, "Days": days,
        "Expected Bars": expected_bars(days, bar_type), "Actual Bars": summary.get("num_bars") or NA,
        "Data Quality Status": "unknown", "Backtest Status": summary.get("status") or "failed",
        "Failure Reason": reason or summary.get("error_message") or summary.get("error_type") or "backtest failed",
        "Caveat": "backtest did not complete; " + _BASE_CAVEAT,
    })
    return row


# --- table writers ----------------------------------------------------------

def _cell(v: Any) -> str:
    v = em.fmt_na(v)
    return f"{v:.6g}" if isinstance(v, float) else str(v)


def _md(v: Any) -> str:
    # escape pipes so metric labels like "Commission / |Gross PnL|" don't break MD tables
    return _cell(v).replace("|", "\\|")


def write_table_csv(rows: list[dict], path: Path, columns: list[str] = SYMBOL_METRIC_COLUMNS) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=columns)
        w.writeheader()
        for r in rows:
            w.writerow({c: _cell(r.get(c, NA)) for c in columns})


def write_table_md(rows: list[dict], path: Path, columns: list[str] = MD_CORE_COLUMNS) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["| " + " | ".join(_md(c) for c in columns) + " |",
             "| " + " | ".join("---" for _ in columns) + " |"]
    for r in rows:
        lines.append("| " + " | ".join(_md(r.get(c, NA)) for c in columns) + " |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# --- coverage audit ---------------------------------------------------------

# metric -> (category, status, computed_from, reliability, reason, fallback)
#   status:      implemented | added | planned
#   reliability: reliable | approximate | unavailable
_C_BASIC, _C_RET, _C_REL = "basic", "returns", "relative"
_C_RISK, _C_TRADE, _C_EXP = "risk", "trade_quality", "exposure"
_C_COST, _C_PERP, _C_DQ, _C_RUN = "cost", "perpetual_mechanism", "data_quality", "run_status"

METRIC_AUDIT: dict[str, tuple[str, str, str, str, str, str]] = {
    "Strategy": (_C_BASIC, "implemented", "cli", "reliable", "from --strategy", "-"),
    "Market Type": (_C_BASIC, "implemented", "venue map", "reliable", "venue_type", "-"),
    "Exchange": (_C_BASIC, "implemented", "summary", "reliable", "summary field", "-"),
    "Symbol": (_C_BASIC, "implemented", "summary", "reliable", "summary field", "-"),
    "Contract Type": (_C_BASIC, "implemented", "venue map", "reliable", "venue_type", "-"),
    "Bar Type": (_C_BASIC, "implemented", "summary", "reliable", "summary field", "-"),
    "Start": (_C_BASIC, "implemented", "summary", "reliable", "window start", "-"),
    "End": (_C_BASIC, "implemented", "summary", "reliable", "window end", "-"),
    "Days": (_C_BASIC, "implemented", "computed", "reliable", "inclusive calendar days", "-"),
    "Expected Bars": (_C_DQ, "added", "computed", "reliable", "days x bars/day", "-"),
    "Actual Bars": (_C_DQ, "added", "summary/equity", "reliable", "num_bars or equity rows", "-"),
    "Data Quality Status": (_C_DQ, "added", "computed", "reliable", "expected vs actual", "unknown"),
    "Backtest Status": (_C_RUN, "implemented", "summary", "reliable", "runner status", "-"),
    "Failure Reason": (_C_RUN, "added", "summary", "reliable", "error / missing reason", "-"),
    "Initial Cash": (_C_RET, "implemented", "summary", "reliable", "config", "-"),
    "Final Equity": (_C_RET, "implemented", "summary", "reliable", "backtest", "-"),
    "Net PnL": (_C_RET, "implemented", "summary", "reliable", "backtest", "-"),
    "Total Return": (_C_RET, "implemented", "summary", "reliable", "net/initial", "-"),
    "Annualized Return": (_C_RET, "added", "computed", "approximate", "(1+ret)^(365/days)-1", "NA<=total loss"),
    "Benchmark Return": (_C_REL, "implemented", "bar parquet", "reliable", "close-to-close B&H", "equity close"),
    "Excess Return": (_C_REL, "implemented", "computed", "reliable", "total - benchmark", "-"),
    "Zero Fee Return": (_C_RET, "implemented", "fee scenarios", "reliable", "gross/initial", "-"),
    "Half Fee Return": (_C_RET, "implemented", "fee scenarios", "reliable", "half commission", "-"),
    "VIP Fee 20% Return": (_C_RET, "implemented", "fee scenarios", "approximate", "illustrative 20% fee", "-"),
    "Fee Drag": (_C_RET, "added", "computed", "reliable", "zero-fee - actual", "-"),
    "Calmar Ratio": (_C_RET, "added", "computed", "approximate", "annualized / maxdd%", "NA if maxdd=0"),
    "Return / Max Drawdown": (_C_RET, "added", "computed", "approximate", "total / maxdd%", "NA if maxdd=0"),
    "Best Day Return": (_C_RET, "added", "equity_curve", "reliable", "daily resample", "-"),
    "Worst Day Return": (_C_RET, "added", "equity_curve", "reliable", "daily resample", "-"),
    "Avg Daily Return": (_C_RET, "added", "equity_curve", "reliable", "daily resample", "-"),
    "Daily Return Std": (_C_RET, "added", "equity_curve", "reliable", "daily resample", "-"),
    "Max Drawdown": (_C_RISK, "added", "equity_curve", "reliable", "abs peak-trough", "-"),
    "Max Drawdown %": (_C_RISK, "implemented", "summary", "reliable", "backtest", "-"),
    "Sharpe": (_C_RISK, "implemented", "summary/equity", "approximate", "annualized", "equity_stats"),
    "Sortino": (_C_RISK, "implemented", "summary/equity", "approximate", "annualized downside", "equity_stats"),
    "Volatility": (_C_RISK, "implemented", "summary/equity", "approximate", "annualized std", "equity_stats"),
    "Downside Volatility": (_C_RISK, "added", "equity_curve", "approximate", "annualized downside std", "-"),
    "Trade Count": (_C_TRADE, "implemented", "summary", "reliable", "closed trades", "-"),
    "Fill Count": (_C_TRADE, "implemented", "summary", "reliable", "fills", "-"),
    "Long Trades": (_C_TRADE, "implemented", "summary", "reliable", "long entries", "-"),
    "Short Trades": (_C_TRADE, "implemented", "summary", "reliable", "short entries", "-"),
    "Win Rate": (_C_TRADE, "implemented", "summary", "reliable", "wins/trades", "-"),
    "Profit Factor": (_C_TRADE, "implemented", "summary", "reliable", "gprofit/|gloss|", "-"),
    "Payoff Ratio": (_C_TRADE, "added", "summary", "reliable", "avgWin/|avgLoss|", "-"),
    "Expectancy": (_C_TRADE, "added", "summary", "reliable", "wr*avgWin+lr*avgLoss", "-"),
    "Avg Trade PnL": (_C_TRADE, "implemented", "summary", "reliable", "mean realized", "-"),
    "Avg Win": (_C_TRADE, "implemented", "summary", "reliable", "mean win", "-"),
    "Avg Loss": (_C_TRADE, "implemented", "summary", "reliable", "mean loss", "-"),
    "Median Trade PnL": (_C_TRADE, "added", "trades", "reliable", "median realized", "-"),
    "Best Trade": (_C_TRADE, "added", "trades", "reliable", "max realized", "-"),
    "Worst Trade": (_C_TRADE, "added", "trades", "reliable", "min realized", "-"),
    "Max Consecutive Wins": (_C_TRADE, "added", "trades", "reliable", "by pnl sign", "-"),
    "Max Consecutive Losses": (_C_TRADE, "added", "trades", "reliable", "by pnl sign", "-"),
    "Exposure %": (_C_EXP, "implemented", "equity_curve", "reliable", "non-flat bars", "-"),
    "Long Exposure %": (_C_EXP, "implemented", "equity_curve", "reliable", "long bars", "-"),
    "Short Exposure %": (_C_EXP, "implemented", "equity_curve", "reliable", "short bars", "-"),
    "Flat %": (_C_EXP, "implemented", "equity_curve", "reliable", "flat bars", "-"),
    "Net Direction Bias": (_C_EXP, "added", "computed", "reliable", "long% - short%", "-"),
    "Avg Holding Time": (_C_EXP, "implemented", "trades", "reliable", "mean hold minutes", "-"),
    "Max Holding Time": (_C_EXP, "implemented", "trades", "reliable", "max hold minutes", "-"),
    "Avg Holding Bars": (_C_EXP, "implemented", "trades", "reliable", "mean hold bars", "-"),
    "Max Holding Bars": (_C_EXP, "implemented", "trades", "reliable", "max hold bars", "-"),
    "Gross PnL": (_C_COST, "implemented", "trades/summary", "reliable", "pre-commission pnl", "-"),
    "Gross Profit": (_C_COST, "implemented", "trades", "reliable", "sum wins", "-"),
    "Gross Loss": (_C_COST, "implemented", "trades", "reliable", "sum losses", "-"),
    "Total Commission": (_C_COST, "implemented", "summary", "reliable", "fees paid", "-"),
    "Commission / Initial Cash": (_C_COST, "implemented", "computed", "reliable", "fees/initial", "-"),
    "Commission / |Gross PnL|": (_C_COST, "implemented", "summary/computed", "reliable", "fees/|gross|", "-"),
    "Commission / |Net PnL|": (_C_COST, "implemented", "computed", "reliable", "fees/|net|", "-"),
    "Avg Commission / Trade": (_C_COST, "implemented", "summary", "reliable", "fees/trades", "-"),
    "Avg Commission / Fill": (_C_COST, "implemented", "computed", "reliable", "fees/fills", "-"),
    "Net / Gross Ratio": (_C_COST, "added", "computed", "reliable", "net/gross pnl", "-"),
    "Break-even Commission": (_C_COST, "added", "computed", "reliable", "gross pnl affordable", "-"),
    "Break-even Fee Ratio": (_C_COST, "implemented", "fee scenarios", "reliable", "gross/commission", "0 if gross<=0"),
    "Turnover": (_C_COST, "implemented", "summary/trades", "approximate", "notional/initial", "trade notional"),
    "Benchmark Direction": (_C_REL, "added", "computed", "reliable", "sign of benchmark", "-"),
    "Strategy Direction Bias": (_C_REL, "added", "computed", "reliable", "long vs short exposure", "-"),
    "Zero Fee Excess Return": (_C_REL, "implemented", "computed", "reliable", "zero-fee - benchmark", "-"),
    "Funding Modeled": (_C_PERP, "implemented", "static", "reliable", "not modeled (No)", "-"),
    "Funding Data Available": (_C_PERP, "planned", "n/a", "unavailable", "funding rate not ingested", "No"),
    "Funding-adjusted Return": (_C_PERP, "planned", "n/a", "unavailable", "funding not in PnL", "NA"),
    "Margin Modeled": (_C_PERP, "implemented", "static", "reliable", "not modeled (No)", "-"),
    "Liquidation Modeled": (_C_PERP, "implemented", "static", "reliable", "not modeled (No)", "-"),
    "Mark Price Modeled": (_C_PERP, "implemented", "static", "reliable", "not modeled (No)", "-"),
    "Mark Price Data Available": (_C_PERP, "planned", "n/a", "unavailable", "mark price not ingested", "No"),
    "Caveat": (_C_RUN, "implemented", "computed", "reliable", "perp + short-sample notes", "-"),
    "Sizing Method": (_C_EXP, "added", "sizing file", "reliable", "initial-close target notional", "-"),
    "Target Notional USDT": (_C_EXP, "added", "sizing file", "reliable", "normalization target", "-"),
    "Order Quantity": (_C_EXP, "added", "sizing file", "reliable", "target / initial price", "-"),
    "Actual Initial Notional": (_C_EXP, "added", "sizing file", "reliable", "quantity x initial price", "-"),
}

COVERAGE_COLUMNS = ["Metric", "Category", "Status", "Computed From", "Reliability",
                    "Reason", "Fallback", "Included In CSV", "Included In MD", "Available"]


def build_coverage_rows(rows: list[dict], *, primary_symbol: str | None = None,
                        columns: list[str] | None = None) -> list[dict]:
    """Per-metric coverage audit. ``Available`` = primary symbol has a non-NA value."""
    columns = columns or SYMBOL_METRIC_COLUMNS
    primary = None
    if primary_symbol is not None:
        primary = next((r for r in rows if r.get("Symbol") == primary_symbol), None)
    if primary is None:
        primary = next((r for r in rows if r.get("Backtest Status") == "success"), rows[0] if rows else {})
    out = []
    for m in columns:
        cat, status, src, rel, reason, fb = METRIC_AUDIT.get(
            m, (_C_BASIC, "implemented", "summary", "reliable", "pre-existing metric", "-"))
        val = primary.get(m, NA)
        out.append({
            "Metric": m, "Category": cat, "Status": status, "Computed From": src,
            "Reliability": rel, "Reason": reason, "Fallback": fb,
            "Included In CSV": "yes", "Included In MD": "yes" if m in MD_CORE_COLUMNS else "no",
            "Available": "yes" if val not in (NA, "", None) else "no",
        })
    return out


def write_coverage_csv(cov_rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COVERAGE_COLUMNS)
        w.writeheader()
        for r in cov_rows:
            w.writerow(r)


def write_coverage_md(cov_rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["| " + " | ".join(COVERAGE_COLUMNS) + " |",
             "| " + " | ".join("---" for _ in COVERAGE_COLUMNS) + " |"]
    for r in cov_rows:
        lines.append("| " + " | ".join(_md(r[c]) for c in COVERAGE_COLUMNS) + " |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# --- notional-normalization sizing + fixed-vs-normalized comparison ----------

def attach_sizing(row: dict, sizing: dict | None) -> dict:
    """Append the four sizing columns to a per-symbol row (NA when no sizing)."""
    row["Sizing Method"] = (sizing or {}).get("sizing_method", NA) or NA
    row["Target Notional USDT"] = (sizing or {}).get("target_notional_usdt", NA)
    row["Order Quantity"] = (sizing or {}).get("order_quantity", NA)
    row["Actual Initial Notional"] = (sizing or {}).get("actual_initial_notional", NA)
    return row


COMPARISON_COLUMNS = [
    "symbol", "fixed_quantity_total_return", "normalized_total_return",
    "fixed_quantity_max_drawdown_pct", "normalized_max_drawdown_pct",
    "fixed_quantity_excess_return", "normalized_excess_return",
    "fixed_quantity_total_commission", "normalized_total_commission",
    "fixed_quantity_commission_to_abs_gross_pnl", "normalized_commission_to_abs_gross_pnl",
    "interpretation",
]


def read_table_csv(path: Path) -> dict[str, dict]:
    """Read a rows=symbol batch_evaluation_table.csv keyed by Symbol."""
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8") as fh:
        return {r.get("Symbol", ""): r for r in csv.DictReader(fh)}


def _g(row: dict, key: str) -> str:
    v = row.get(key, NA)
    return NA if v in (None, "") else str(v)


def build_comparison_rows(normalized_rows: list[dict], fixed_by_symbol: dict[str, dict]) -> list[dict]:
    """One row per symbol comparing fixed-quantity vs notional-normalized metrics."""
    out = []
    for nr in normalized_rows:
        sym = nr.get("Symbol")
        fx = fixed_by_symbol.get(sym, {})

        def num(v):
            try:
                return float(v)
            except (TypeError, ValueError):
                return None
        f_dd, n_dd = num(_g(fx, "Max Drawdown %")), num(nr.get("Max Drawdown %"))
        if f_dd is not None and n_dd is not None:
            interp = (f"fixed maxDD {f_dd:.2%} vs normalized {n_dd:.2%}; normalized notional "
                      "is comparable across symbols (fixed 1-contract over-weights high-price symbols)")
        else:
            interp = "normalized initial notional comparable across symbols; fixed 1-contract is not"
        out.append({
            "symbol": sym,
            "fixed_quantity_total_return": _g(fx, "Total Return"),
            "normalized_total_return": _g(nr, "Total Return"),
            "fixed_quantity_max_drawdown_pct": _g(fx, "Max Drawdown %"),
            "normalized_max_drawdown_pct": _g(nr, "Max Drawdown %"),
            "fixed_quantity_excess_return": _g(fx, "Excess Return"),
            "normalized_excess_return": _g(nr, "Excess Return"),
            "fixed_quantity_total_commission": _g(fx, "Total Commission"),
            "normalized_total_commission": _g(nr, "Total Commission"),
            "fixed_quantity_commission_to_abs_gross_pnl": _g(fx, "Commission / |Gross PnL|"),
            "normalized_commission_to_abs_gross_pnl": _g(nr, "Commission / |Gross PnL|"),
            "interpretation": interp,
        })
    return out


def write_comparison_csv(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COMPARISON_COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, NA) for c in COMPARISON_COLUMNS})
