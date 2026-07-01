"""Trade-frequency + fee-before/after metric derivations (pure stdlib).

Reads already-computed values (per-run summary.json + the evaluation-table row +
the raw trades.csv/fills.csv) and derives display metrics: how often the strategy
trades, and the gross-vs-net (fee) impact on one row. No network, no backtest, no
strategy import, no fabrication -- fields that cannot be computed reliably are
returned as ``NA`` with a note.
"""
from __future__ import annotations

import csv
import math
from pathlib import Path
from typing import Any

NA = "NA"
_DAYS_PER_MONTH = 30.4375
_DAYS_PER_YEAR = 365.25
_NS_PER_MIN = 60_000_000_000


def _num(v: Any) -> float | None:
    try:
        x = float(v)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


_UNIT_MIN = {"m": 1.0, "min": 1.0, "h": 60.0, "d": 1440.0, "w": 10080.0}


def bar_minutes(bar_type: str) -> float | None:
    """``1m``->1, ``5m``->5, ``15m``->15, ``1h``->60, ``4h``->240, ``1d``->1440."""
    s = str(bar_type).strip().lower()
    if not s or not s[0].isdigit():
        return None
    num = ""
    for ch in s:
        if ch.isdigit():
            num += ch
        else:
            break
    unit = s[len(num):] or "m"
    if unit not in _UNIT_MIN:
        return None
    try:
        return int(num) * _UNIT_MIN[unit]
    except ValueError:
        return None


def days_inclusive(start: str, end: str) -> int | None:
    from datetime import date  # noqa: PLC0415
    try:
        a = date.fromisoformat(str(start)[:10])
        b = date.fromisoformat(str(end)[:10])
        return (b - a).days + 1 if b >= a else None
    except ValueError:
        return None


def _e(eval_row: dict, *names: str) -> Any:
    """First present, non-empty eval-table cell (by display column name)."""
    for n in names:
        if n in eval_row and str(eval_row[n]).strip() not in ("", "NA", "nan"):
            return eval_row[n]
    return NA


def read_trades(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


# --- trade frequency --------------------------------------------------------

TRADE_FREQUENCY_FIELDS = [
    "bars_count", "trading_days", "trade_count", "fill_count", "entry_count", "exit_count",
    "trades_per_day", "trades_per_month", "trades_per_year", "fills_per_day",
    "avg_bars_between_trades", "avg_minutes_between_trades", "avg_holding_minutes",
    "max_holding_minutes", "exposure_pct", "long_exposure_pct", "short_exposure_pct",
    "flat_pct", "turnover", "turnover_per_day", "turnover_per_month",
]


def _round(x, n=4):
    return round(x, n) if isinstance(x, float) else x


def trade_frequency(*, summary: dict, eval_row: dict, bar_type: str,
                    trades_rows: list[dict] | None = None) -> dict:
    trades_rows = trades_rows or []
    bars = _num(summary.get("num_bars")) or _num(_e(eval_row, "Actual Bars"))
    tdays = days_inclusive(summary.get("start") or _e(eval_row, "Start"),
                           summary.get("end") or _e(eval_row, "End"))
    bar_min = bar_minutes(bar_type)
    tcount = _num(summary.get("trade_count")) or _num(_e(eval_row, "Trade Count"))
    fcount = _num(summary.get("fill_count")) or _num(_e(eval_row, "Fill Count"))
    tested_min = (bars * bar_min) if (bars is not None and bar_min is not None) else None

    # entry/exit + holding from trades.csv (one row per round-trip -> precise)
    entry_count = exit_count = NA
    avg_hold_min = max_hold_min = NA
    if trades_rows:
        # presence, not truthiness (ns=0 is a valid, falsy timestamp)
        entry_count = sum(1 for r in trades_rows if str(r.get("entry_time_ns", "")).strip() not in ("", "None"))
        exit_count = sum(1 for r in trades_rows if str(r.get("exit_time_ns", "")).strip() not in ("", "None"))
        holds = []
        for r in trades_rows:
            a, b = _num(r.get("entry_time_ns")), _num(r.get("exit_time_ns"))
            if a is not None and b is not None and b >= a:
                holds.append((b - a) / _NS_PER_MIN)
        if holds:
            avg_hold_min = _round(sum(holds) / len(holds), 3)
            max_hold_min = _round(max(holds), 3)
    if avg_hold_min == NA:  # fall back to bar-based holding from the eval table
        ahb, mhb = _num(_e(eval_row, "Avg Holding Bars")), _num(_e(eval_row, "Max Holding Bars"))
        if bar_min is not None and ahb is not None:
            avg_hold_min = _round(ahb * bar_min, 3)
        if bar_min is not None and mhb is not None:
            max_hold_min = _round(mhb * bar_min, 3)

    def per(x, per_days):
        return _round(x / per_days, 4) if (x is not None and per_days) else NA

    turnover = _num(_e(eval_row, "Turnover"))
    return {
        "bars_count": int(bars) if bars is not None else NA,
        "trading_days": tdays if tdays is not None else NA,
        "trade_count": int(tcount) if tcount is not None else NA,
        "fill_count": int(fcount) if fcount is not None else NA,
        "entry_count": entry_count, "exit_count": exit_count,
        "trades_per_day": per(tcount, tdays),
        "trades_per_month": _round(tcount / (tdays / _DAYS_PER_MONTH), 4) if (tcount is not None and tdays) else NA,
        "trades_per_year": _round(tcount / (tdays / _DAYS_PER_YEAR), 4) if (tcount is not None and tdays) else NA,
        "fills_per_day": per(fcount, tdays),
        "avg_bars_between_trades": _round(bars / max(tcount, 1), 3) if (bars is not None and tcount is not None) else NA,
        "avg_minutes_between_trades": _round(tested_min / max(tcount, 1), 3) if (tested_min is not None and tcount is not None) else NA,
        "avg_holding_minutes": avg_hold_min, "max_holding_minutes": max_hold_min,
        "exposure_pct": _e(eval_row, "Exposure %"), "long_exposure_pct": _e(eval_row, "Long Exposure %"),
        "short_exposure_pct": _e(eval_row, "Short Exposure %"), "flat_pct": _e(eval_row, "Flat %"),
        "turnover": turnover if turnover is not None else NA,
        "turnover_per_day": per(turnover, tdays),
        "turnover_per_month": _round(turnover / (tdays / _DAYS_PER_MONTH), 6) if (turnover is not None and tdays) else NA,
    }


# --- fee before/after -------------------------------------------------------

FEE_IMPACT_FIELDS = [
    "gross_return", "net_return", "zero_fee_return", "fee_drag_return", "fee_drag_pct_point",
    "half_fee_return", "vip_fee_20pct_return", "benchmark_return", "gross_excess_return",
    "net_excess_return", "zero_fee_excess_return", "half_fee_excess_return",
    "vip_fee_20pct_excess_return", "gross_pnl", "net_pnl", "total_commission",
    "commission_to_initial_cash", "commission_to_abs_gross_pnl", "commission_to_abs_net_pnl",
    "avg_commission_per_trade", "avg_commission_per_fill", "net_to_gross_ratio",
    "break_even_commission", "break_even_fee_ratio", "gross_pnl_source",
]


def fee_impact(*, summary: dict, eval_row: dict) -> dict:
    net_return = _num(_e(eval_row, "Total Return"))
    zero_fee_return = _num(_e(eval_row, "Zero Fee Return"))
    half_fee_return = _num(_e(eval_row, "Half Fee Return"))
    vip_return = _num(_e(eval_row, "VIP Fee 20% Return"))
    bench = _num(_e(eval_row, "Benchmark Return"))
    gross_return = zero_fee_return                      # gross == zero-fee simulation

    net_pnl = _num(summary.get("net_pnl")) or _num(_e(eval_row, "Net PnL"))
    gross_pnl = _num(summary.get("gross_realized_pnl")) or _num(_e(eval_row, "Gross PnL"))
    gross_src = "gross_realized_pnl" if gross_pnl is not None else "unavailable"
    if gross_pnl is None and zero_fee_return is not None:
        ic = _num(summary.get("initial_cash")) or _num(_e(eval_row, "Initial Cash"))
        if ic is not None:
            gross_pnl = round(zero_fee_return * ic, 4)
            gross_src = "zero_fee_simulation"

    def sub(a, b):
        return round(a - b, 6) if (a is not None and b is not None) else NA

    fee_drag = sub(zero_fee_return, net_return)
    return {
        "gross_return": gross_return if gross_return is not None else NA,
        "net_return": net_return if net_return is not None else NA,
        "zero_fee_return": zero_fee_return if zero_fee_return is not None else NA,
        "fee_drag_return": fee_drag, "fee_drag_pct_point": fee_drag,
        "half_fee_return": half_fee_return if half_fee_return is not None else NA,
        "vip_fee_20pct_return": vip_return if vip_return is not None else NA,
        "benchmark_return": bench if bench is not None else NA,
        "gross_excess_return": sub(gross_return, bench),
        "net_excess_return": sub(net_return, bench),
        "zero_fee_excess_return": sub(zero_fee_return, bench),
        "half_fee_excess_return": sub(half_fee_return, bench),
        "vip_fee_20pct_excess_return": sub(vip_return, bench),
        "gross_pnl": gross_pnl if gross_pnl is not None else NA,
        "net_pnl": net_pnl if net_pnl is not None else NA,
        "total_commission": _e(eval_row, "Total Commission"),
        "commission_to_initial_cash": _e(eval_row, "Commission / Initial Cash"),
        "commission_to_abs_gross_pnl": _e(eval_row, "Commission / |Gross PnL|"),
        "commission_to_abs_net_pnl": _e(eval_row, "Commission / |Net PnL|"),
        "avg_commission_per_trade": _e(eval_row, "Avg Commission / Trade"),
        "avg_commission_per_fill": _e(eval_row, "Avg Commission / Fill"),
        "net_to_gross_ratio": _e(eval_row, "Net / Gross Ratio"),
        "break_even_commission": _e(eval_row, "Break-even Commission"),
        "break_even_fee_ratio": _e(eval_row, "Break-even Fee Ratio"),
        "gross_pnl_source": gross_src,
    }


# --- per-bar cumulative commission (from fills, two-pointer over sorted ns) --

def cumulative_commission_by_ns(fills_rows: list[dict], bar_ns: list[int]) -> list[float] | None:
    """Cumulative commission up to each bar timestamp. Returns a list aligned to
    ``bar_ns`` (already time-sorted), or None if fills lack ns/commission."""
    fills = []
    for r in fills_rows:
        ns, com = _num(r.get("event_time_ns")), _num(r.get("commission"))
        if ns is not None and com is not None:
            fills.append((ns, com))
    if not fills:
        return None
    fills.sort(key=lambda x: x[0])
    out = []
    i = running = 0
    n = len(fills)
    for t in bar_ns:
        while i < n and fills[i][0] <= t:
            running += fills[i][1]
            i += 1
        out.append(round(running, 8))
    return out


def read_fills(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


# --- monthly trade counts (from trades.csv exit month) ----------------------

def monthly_trade_counts(trades_rows: list[dict]) -> list[tuple[str, int]]:
    counts: dict[str, int] = {}
    for r in trades_rows:
        t = r.get("exit_time") or r.get("entry_time") or ""
        month = str(t)[:7]                              # YYYY-MM
        if len(month) == 7:
            counts[month] = counts.get(month, 0) + 1
    return sorted(counts.items())


# --- fee_impact_table row (Target C) ----------------------------------------

FEE_IMPACT_TABLE_COLUMNS = [
    "run_uid", "strategy_name", "symbol", "bar_type", "actual_start", "actual_end",
    "sizing_mode", "bars_count", "trading_days", "trade_count", "trades_per_day",
    "trades_per_month", "gross_return", "net_return", "zero_fee_return",
    "fee_drag_return", "benchmark_return", "net_excess_return", "zero_fee_excess_return",
    "gross_pnl", "net_pnl", "total_commission", "commission_to_initial_cash",
    "commission_to_abs_gross_pnl", "avg_commission_per_trade", "avg_commission_per_fill",
    "max_drawdown_pct", "profit_factor", "win_rate", "artifact_status",
]


def fee_impact_table_row(*, identity_fields: dict, freq: dict, fee: dict,
                         eval_row: dict, artifact_status: str) -> dict:
    return {
        "run_uid": identity_fields["run_uid"], "strategy_name": identity_fields["strategy_name"],
        "symbol": identity_fields["symbol"], "bar_type": identity_fields["bar_type"],
        "actual_start": identity_fields["start"], "actual_end": identity_fields["end"],
        "sizing_mode": identity_fields["sizing_mode"],
        "bars_count": freq["bars_count"], "trading_days": freq["trading_days"],
        "trade_count": freq["trade_count"], "trades_per_day": freq["trades_per_day"],
        "trades_per_month": freq["trades_per_month"],
        "gross_return": fee["gross_return"], "net_return": fee["net_return"],
        "zero_fee_return": fee["zero_fee_return"], "fee_drag_return": fee["fee_drag_return"],
        "benchmark_return": fee["benchmark_return"], "net_excess_return": fee["net_excess_return"],
        "zero_fee_excess_return": fee["zero_fee_excess_return"], "gross_pnl": fee["gross_pnl"],
        "net_pnl": fee["net_pnl"], "total_commission": fee["total_commission"],
        "commission_to_initial_cash": fee["commission_to_initial_cash"],
        "commission_to_abs_gross_pnl": fee["commission_to_abs_gross_pnl"],
        "avg_commission_per_trade": fee["avg_commission_per_trade"],
        "avg_commission_per_fill": fee["avg_commission_per_fill"],
        "max_drawdown_pct": _e(eval_row, "Max Drawdown %"),
        "profit_factor": _e(eval_row, "Profit Factor"), "win_rate": _e(eval_row, "Win Rate"),
        "artifact_status": artifact_status,
    }
