#!/usr/bin/env python3
"""Pure strategy-evaluation metric functions (stdlib only).

This is the single home for the *math* behind the VWM evaluation tables. It is a
library, not a CLI: no argparse, no disk reads, no network, no pyarrow. Every
function takes already-parsed numbers / row-dicts and returns numbers (or
``None`` when a value cannot be computed reliably — callers render that as
``NA``; nothing is ever fabricated).

Deliberately imports **nothing** from ``strategy`` / ``feature_engine`` /
``data_engine`` / ``nautilus_trader``. The reporting CLIs
(``scripts/build_*_eval_table.py``) and :mod:`research.evaluation_tables` compose
these functions; keeping them here means the metric system is unit-testable
without a backtest, a server, or pandas/pyarrow.

Categories (mirrors the coverage audit):

* returns      - annualized_return, benchmark_return, fee_scenarios, daily_stats
* risk         - equity_stats (vol/sharpe/sortino/abs-dd), downside_volatility
* trade quality- gross_from_trades, trade_pnl_stats, payoff_ratio, expectancy
* exposure     - exposure_from_positions, holding_from_trades, strategy_direction_bias
* cost         - turnover_from_trades, (fee_scenarios break-even)
* relative     - benchmark_direction
"""
from __future__ import annotations

import math
import statistics
from datetime import date
from typing import Any

NA = "NA"
_DAY_NS = 86_400_000_000_000


# --- primitives -------------------------------------------------------------

def is_finite(x: Any) -> bool:
    """True iff ``x`` is a real, finite number (bool excluded)."""
    try:
        return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(float(x))
    except (TypeError, ValueError):
        return False


def fmt_na(x: Any) -> Any:
    """Render ``None`` / non-finite floats as the literal ``NA`` token."""
    if x is None or (isinstance(x, float) and not math.isfinite(x)):
        return NA
    return x


def safe_div(num: Any, den: Any) -> float | None:
    """``num/den`` or ``None`` when either side is non-finite or ``den == 0``."""
    if not is_finite(num) or not is_finite(den) or float(den) == 0.0:
        return None
    return float(num) / float(den)


def days_inclusive(start: str, end: str) -> int | None:
    """Inclusive calendar-day span ``end - start + 1`` (None on bad dates)."""
    try:
        return (date.fromisoformat(str(end)) - date.fromisoformat(str(start))).days + 1
    except (TypeError, ValueError):
        return None


def bar_seconds(bar_type: str) -> int:
    """Seconds-per-bar from a ``"15m"`` / ``"1h"`` style label (default 300)."""
    s = str(bar_type).strip().lower()
    unit = s[-1]
    try:
        n = int(s[:-1])
    except ValueError:
        return 300
    return {"s": 1, "m": 60, "h": 3600, "d": 86400}.get(unit, 60) * n


# --- returns ----------------------------------------------------------------

def annualized_return(total_return: Any, days: int | None) -> float | None:
    if not is_finite(total_return) or not days or days <= 0:
        return None
    base = 1.0 + float(total_return)
    if base <= 0.0:                       # total loss; annualized power undefined
        return None
    return base ** (365.0 / days) - 1.0


def benchmark_return(first_close: Any, last_close: Any) -> float | None:
    """Close-to-close buy-and-hold return."""
    if not is_finite(first_close) or not is_finite(last_close) or float(first_close) == 0.0:
        return None
    return float(last_close) / float(first_close) - 1.0


def fee_scenarios(raw_net_pnl: Any, total_commission: Any, initial_cash: Any,
                  *, half_ratio: float = 0.5, vip_ratio: float = 0.2) -> dict[str, Any]:
    """Fee sensitivity: returns under actual / zero / half / VIP fees + break-even.

    ``gross = raw_net_pnl + total_commission`` is the pre-commission (gross
    realized) PnL. Each scenario nets the scaled commission off that gross.
    """
    if not is_finite(raw_net_pnl) or not is_finite(total_commission) or not is_finite(initial_cash) \
            or float(initial_cash) == 0.0:
        return {}
    raw_net_pnl = float(raw_net_pnl); total_commission = float(total_commission)
    initial_cash = float(initial_cash)
    gross = raw_net_pnl + total_commission

    def scen(comm: float) -> dict[str, float]:
        npnl = gross - comm
        return {"net_pnl": npnl, "final_equity": initial_cash + npnl,
                "total_return": npnl / initial_cash}

    zero = scen(0.0)
    zero_profitable = zero["net_pnl"] > 0.0
    # break-even commission = gross; ratio vs current fee. Not meaningful if gross<=0.
    if gross > 0.0 and total_commission > 0.0:
        break_even_ratio: float | None = gross / total_commission
    else:
        break_even_ratio = 0.0            # cannot tolerate any fee (gross<=0)

    if not zero_profitable:
        note = ("zero-fee still unprofitable (gross PnL <= 0): likely a "
                "signal-quality issue, not pure cost")
    elif raw_net_pnl <= 0.0:
        note = ("profitable only below the current fee level: strategy is "
                "highly cost-sensitive")
    else:
        note = "profitable even at current fee level"

    return {
        "gross": gross,
        "actual": scen(total_commission),
        "zero": zero,
        "half": scen(total_commission * half_ratio),
        "vip": scen(total_commission * vip_ratio),
        "break_even_fee_ratio_vs_current": break_even_ratio,
        "zero_fee_profitable": zero_profitable,
        "net_without_commission": gross,
        "fee_sensitivity_note": note,
    }


def daily_returns(equity_rows: list[dict]) -> list[float]:
    """Resample bar equity to one point per UTC day, then daily simple returns.

    Each row needs ``event_time_ns`` and ``equity``; the last bar of each UTC day
    wins. Rows missing either field are skipped.
    """
    by_day: dict[int, float] = {}
    for r in equity_rows:
        ns = r.get("event_time_ns")
        eq = r.get("equity")
        if not is_finite(ns) or not is_finite(eq):
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


# --- risk -------------------------------------------------------------------

def equity_stats(equity: list[float], *, bars_per_day: int) -> dict[str, float | None]:
    """Annualized volatility / Sharpe / Sortino + absolute max drawdown."""
    out: dict[str, float | None] = {"volatility": None, "sharpe": None,
                                    "sortino": None, "max_drawdown_abs": None}
    eq = [float(x) for x in equity if is_finite(x)]
    if len(eq) >= 2:
        peak = eq[0]; max_dd = 0.0
        for v in eq:
            peak = max(peak, v)
            max_dd = max(max_dd, peak - v)
        out["max_drawdown_abs"] = max_dd
    rets = [eq[i] / eq[i - 1] - 1.0 for i in range(1, len(eq)) if eq[i - 1] != 0.0]
    n = len(rets)
    if n >= 2:
        mean = sum(rets) / n
        std = math.sqrt(sum((r - mean) ** 2 for r in rets) / (n - 1))
        ann = math.sqrt(bars_per_day * 365.0)
        out["volatility"] = std * ann
        if std > 0.0:
            out["sharpe"] = (mean / std) * ann
        downside = [r for r in rets if r < 0.0]
        if downside:
            dstd = math.sqrt(sum(r * r for r in downside) / len(downside))
            if dstd > 0.0:
                out["sortino"] = (mean / dstd) * ann
    return out


def downside_volatility(equity_rows: list[dict], *, bars_per_day: int) -> float | None:
    """Annualized downside std from bar-level returns (matches equity_stats units)."""
    eq = [float(r["equity"]) for r in equity_rows if is_finite(r.get("equity"))]
    rets = [eq[i] / eq[i - 1] - 1.0 for i in range(1, len(eq)) if eq[i - 1] != 0.0]
    downside = [r for r in rets if r < 0.0]
    if len(downside) < 2:
        return None
    dstd = math.sqrt(sum(r * r for r in downside) / len(downside))
    return dstd * math.sqrt(bars_per_day * 365.0)


# --- trade quality ----------------------------------------------------------

def gross_from_trades(trades: list[dict]) -> dict[str, float | None]:
    """Gross PnL / profit / loss from per-trade realized PnL (pre-commission)."""
    pnls = []
    for t in trades:
        try:
            pnls.append(float(t["realized_pnl"]))
        except (KeyError, TypeError, ValueError):
            continue
    if not pnls:
        return {"gross_pnl": None, "gross_profit": None, "gross_loss": None}
    return {"gross_pnl": sum(pnls),
            "gross_profit": sum(p for p in pnls if p > 0.0),
            "gross_loss": sum(p for p in pnls if p < 0.0)}


def trade_pnl_stats(trades: list[dict]) -> dict[str, float | int | None]:
    """Median/best/worst realized PnL + max consecutive win/loss streaks."""
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
    if not is_finite(avg_win) or not is_finite(avg_loss) or float(avg_loss) == 0.0:
        return None
    return float(avg_win) / abs(float(avg_loss))


def expectancy(win_rate: Any, avg_win: Any, avg_loss: Any) -> float | None:
    if not all(is_finite(x) for x in (win_rate, avg_win, avg_loss)):
        return None
    wr = float(win_rate)
    return wr * float(avg_win) + (1.0 - wr) * float(avg_loss)


# --- exposure / direction ---------------------------------------------------

def exposure_from_positions(positions: list[float]) -> dict[str, float | None]:
    """Time share long / short / flat from per-bar position sizes."""
    vals = [float(p) for p in positions if is_finite(p)]
    n = len(vals)
    if n == 0:
        return {"exposure_pct": None, "long_exposure_pct": None,
                "short_exposure_pct": None, "flat_pct": None}
    longs = sum(1 for p in vals if p > 0.0)
    shorts = sum(1 for p in vals if p < 0.0)
    flat = n - longs - shorts
    return {"exposure_pct": (longs + shorts) / n, "long_exposure_pct": longs / n,
            "short_exposure_pct": shorts / n, "flat_pct": flat / n}


def holding_from_trades(trades: list[dict], *, bar_seconds: int) -> dict[str, float | None]:
    """Average / max holding duration (minutes and bars) from trade timestamps."""
    durs = []
    for t in trades:
        try:
            ein = float(t["entry_time_ns"]); xin = float(t["exit_time_ns"])
        except (KeyError, TypeError, ValueError):
            continue
        if xin >= ein:
            durs.append((xin - ein) / 1e9)        # seconds
    if not durs:
        return {"avg_holding_minutes": None, "avg_holding_bars": None,
                "max_holding_minutes": None, "max_holding_bars": None}
    avg_s = sum(durs) / len(durs)
    max_s = max(durs)
    return {"avg_holding_minutes": avg_s / 60.0, "avg_holding_bars": avg_s / bar_seconds,
            "max_holding_minutes": max_s / 60.0, "max_holding_bars": max_s / bar_seconds}


def strategy_direction_bias(long_pct: Any, short_pct: Any) -> str:
    if not is_finite(long_pct) or not is_finite(short_pct):
        return NA
    d = float(long_pct) - float(short_pct)
    return "long" if d > 1e-9 else ("short" if d < -1e-9 else "neutral")


# --- cost -------------------------------------------------------------------

def turnover_from_trades(trades: list[dict], initial_cash: Any) -> float | None:
    if not trades or not is_finite(initial_cash) or float(initial_cash) == 0.0:
        return None
    notional = 0.0; ok = False
    for t in trades:
        try:
            q = abs(float(t["quantity"])); ep = abs(float(t["entry_price"]))
            xp = abs(float(t["exit_price"]))
        except (KeyError, TypeError, ValueError):
            continue
        notional += q * ep + q * xp; ok = True
    return notional / float(initial_cash) if ok else None


# --- relative ---------------------------------------------------------------

def benchmark_direction(bench: Any) -> str:
    if not is_finite(bench):
        return NA
    b = float(bench)
    return "up" if b > 1e-9 else ("down" if b < -1e-9 else "flat")


# --- back-compat aliases (legacy private names used by the script layer) -----
_finite = is_finite
_fmt = fmt_na
_days = days_inclusive
_bar_seconds = bar_seconds
_annualized_return = annualized_return
