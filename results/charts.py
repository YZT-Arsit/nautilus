"""Self-built chart rendering from a backtest run's ``equity_curve.csv``.

Pure stdlib to read; matplotlib is **optional** and imported lazily — if it is
absent, the render functions return ``{}`` / ``None`` (never fabricate a chart,
never crash a run). Produces PNGs under ``<dir>/charts/`` so results can be
viewed locally after the (server-side) run is pulled back.

Two entry points:

* :func:`render_run_charts` — per-run panels (equity / drawdown / pnl / position)
  for ONE run directory (a ``nofee`` or ``fee_5bps`` leaf).
* :func:`render_fee_compare` — overlays the ``nofee`` vs ``fee_5bps`` equity curves
  of ONE strategy directory on a single chart, so the fee drag is visible at a
  glance (this is the headline comparison).

Presentation: the x-axis is rendered as calendar dates (the stored ``ts_event`` is
epoch-ns), titles carry the run/strategy + end return, axes are labelled, and long
per-bar series (a 2-year 1m run is ~1.05M points) are down-sampled to keep the PNG
crisp and small.
"""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path

# Candidate column names (defensive: the writer's schema may evolve).
_TIME_COLS = ("event_time_ns", "ts_event", "time_ns", "time", "ts")
_EQUITY_COLS = ("equity", "total_equity")
_PNL_COLS = ("net_pnl", "pnl", "realized_pnl")
_POS_COLS = ("position", "position_qty", "net_position")
_PRICE_COLS = ("close", "mark_price", "price")
_LEVERAGE_COLS = ("position_leverage_pct", "signed_leverage_pct")

_MAX_POINTS = 2500  # down-sample target for plotting
_NS_THRESHOLD = 10**17  # values above this are treated as epoch-ns timestamps


def _pick(header: list[str], candidates: tuple[str, ...]) -> str | None:
    for c in candidates:
        if c in header:
            return c
    return None


def _num(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _equity_path(run_dir: Path) -> Path | None:
    """Locate a run's equity series, parquet preferred over csv (hybrid storage)."""
    for name in ("equity_curve.parquet", "equity_curve.csv"):
        p = run_dir / name
        if p.is_file():
            return p
    return None


def _read_parquet(path: Path) -> tuple[list[str], dict[str, list]]:
    """Return (column_names, {col: values}); polars preferred, pyarrow fallback."""
    try:
        import polars as pl  # noqa: PLC0415

        df = pl.read_parquet(path)
        return df.columns, {c: df[c].to_list() for c in df.columns}
    except Exception:
        import pyarrow.parquet as pq  # noqa: PLC0415

        tbl = pq.read_table(path)
        return list(tbl.column_names), {c: tbl.column(c).to_pylist() for c in tbl.column_names}


def _load_series(equity_path: Path) -> dict[str, list[float]]:
    """Read an equity series (parquet or csv) into column->list[float] for plotting."""
    if equity_path.suffix == ".parquet":
        header, data = _read_parquet(equity_path)
        tcol = _pick(header, _TIME_COLS)
        ecol = _pick(header, _EQUITY_COLS)
        pcol = _pick(header, _PNL_COLS)
        poscol = _pick(header, _POS_COLS)
        pricecol = _pick(header, _PRICE_COLS)
        leveragecol = _pick(header, _LEVERAGE_COLS)
        n = len(next(iter(data.values()))) if data else 0
        get = lambda c: data[c] if c in data else [None] * n  # noqa: E731
        return {
            "t": [_num(v, i) for i, v in enumerate(get(tcol))],
            "equity": [_num(v) for v in get(ecol)],
            "pnl": [_num(v) for v in get(pcol)],
            "position": [_num(v) for v in get(poscol)],
            "price": [_num(v) for v in get(pricecol)],
            "leverage": [_num(v) for v in get(leveragecol)] if leveragecol else [],
        }
    with open(equity_path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        header = reader.fieldnames or []
        tcol = _pick(header, _TIME_COLS)
        ecol = _pick(header, _EQUITY_COLS)
        pcol = _pick(header, _PNL_COLS)
        poscol = _pick(header, _POS_COLS)
        pricecol = _pick(header, _PRICE_COLS)
        leveragecol = _pick(header, _LEVERAGE_COLS)
        cols: dict[str, list[float]] = {
            "t": [],
            "equity": [],
            "pnl": [],
            "position": [],
            "price": [],
            "leverage": [],
        }
        for i, row in enumerate(reader):
            cols["t"].append(_num(row.get(tcol), i))
            cols["equity"].append(_num(row.get(ecol)))
            cols["pnl"].append(_num(row.get(pcol)))
            cols["position"].append(_num(row.get(poscol)))
            cols["price"].append(_num(row.get(pricecol)))
            if leveragecol:
                cols["leverage"].append(_num(row.get(leveragecol)))
    return cols


def _read_metrics(run_dir: Path) -> dict:
    p = run_dir / "metrics.json"
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}


def _x_axis(t: list[float]):
    """Return an x series for plotting: datetimes if ``t`` is epoch-ns, else raw."""
    if t and max(t) > _NS_THRESHOLD:
        return [datetime.fromtimestamp(v / 1e9, tz=timezone.utc) for v in t], True
    return t, False


def _downsample(xs: list, *series: list[float]):
    """Evenly thin ``xs`` and each series to ~``_MAX_POINTS`` (keep first & last)."""
    n = len(xs)
    if n <= _MAX_POINTS:
        return xs, list(series)
    step = n // _MAX_POINTS
    idx = list(range(0, n, step))
    if idx[-1] != n - 1:
        idx.append(n - 1)
    return [xs[i] for i in idx], [[s[i] for i in idx] for s in series]


def _drawdown(equity: list[float]) -> list[float]:
    dd, peak = [], float("-inf")
    for e in equity:
        peak = max(peak, e)
        dd.append((e - peak) / peak * 100.0 if peak not in (0.0, float("-inf")) else 0.0)
    return dd


def _ret_label(metrics: dict) -> str:
    r = metrics.get("total_return")
    return f"  (ret {r:+.1%})" if isinstance(r, (int, float)) else ""


def _signed_leverage_percent(series: dict[str, list[float]], metrics: dict) -> list[float]:
    """Return signed notional / fixed initial capital; 100% means one-times leverage."""
    if len(series["leverage"]) == len(series["t"]):
        return series["leverage"]
    capital = _num(metrics.get("initial_cash") or metrics.get("notional_usdt"))
    if capital <= 0:
        raise ValueError("position leverage chart requires positive initial_cash/notional_usdt")
    return [
        position * price / capital * 100.0
        for position, price in zip(series["position"], series["price"], strict=True)
    ]


def _plt():
    """Lazy matplotlib (Agg); returns the pyplot module or ``None`` if absent."""
    try:
        import matplotlib  # noqa: PLC0415

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt  # noqa: PLC0415
    except ImportError:
        return None
    return plt


def render_run_charts(run_dir: str | Path) -> dict[str, str]:
    """Render equity / drawdown / pnl / position PNGs for one run.

    Returns ``{chart_name: relative_png_path}``; empty dict if matplotlib is
    missing or there is no ``equity_curve.csv`` to plot.
    """
    run_dir = Path(run_dir)
    equity_path = _equity_path(run_dir)
    if equity_path is None:
        return {}
    plt = _plt()
    if plt is None:
        return {}

    s = _load_series(equity_path)
    if not s["t"]:
        return {}
    metrics = _read_metrics(run_dir)
    name_prefix = str(metrics.get("run_name", run_dir.name))
    leverage = _signed_leverage_percent(s, metrics)
    x, is_date = _x_axis(s["t"])
    x, (equity, pnl, leverage, dd) = _downsample(
        x, s["equity"], s["pnl"], leverage, _drawdown(s["equity"])
    )

    charts_dir = run_dir / "charts"
    charts_dir.mkdir(parents=True, exist_ok=True)
    out: dict[str, str] = {}
    panels = [
        ("equity_curve", "Equity" + _ret_label(metrics), "Equity (USDT)", equity),
        ("drawdown", "Drawdown", "Drawdown (%)", dd),
        ("pnl", "PnL", "PnL (USDT)", pnl),
        ("position", "Signed leverage (100% = 1x)", "Signed leverage (%)", leverage),
    ]
    for fname, title, ylabel, series in panels:
        fig, ax = plt.subplots(figsize=(11, 3.4))
        ax.plot(x, series, linewidth=0.9)
        ax.set_title(f"{name_prefix} — {title}")
        ax.set_ylabel(ylabel)
        if fname == "position":
            ax.axhline(0.0, color="grey", linewidth=0.7, linestyle="--", alpha=0.6)
        ax.grid(True, alpha=0.3)
        if is_date:
            fig.autofmt_xdate()
        fig.tight_layout()
        png = charts_dir / f"{fname}.png"
        fig.savefig(png, dpi=120)
        plt.close(fig)
        out[fname] = str(png.relative_to(run_dir))
    return out


def render_fee_compare(strategy_dir: str | Path) -> str | None:
    """Overlay the ``nofee`` vs ``fee_5bps`` equity curves of one strategy.

    Writes ``<strategy_dir>/charts/equity_fee_compare.png`` and returns its path
    relative to ``strategy_dir`` (or ``None`` if matplotlib is missing or neither
    scenario has an ``equity_curve.csv``).
    """
    strategy_dir = Path(strategy_dir)
    plt = _plt()
    if plt is None:
        return None  # matplotlib missing

    curves = []
    for sub, label in (("nofee", "no fee"), ("fee_5bps", "fee 5bps")):
        eq_path = _equity_path(strategy_dir / sub)
        if eq_path is not None:
            s = _load_series(eq_path)
            if s["t"]:
                curves.append((label, s, _read_metrics(strategy_dir / sub)))
    if not curves:
        return None

    charts_dir = strategy_dir / "charts"
    charts_dir.mkdir(parents=True, exist_ok=True)
    outputs = []
    for field, title, ylabel, baseline in (
        ("equity", "equity: fee vs no-fee", "Equity (USDT)", 100000.0),
        ("pnl", "net PnL: fee vs no-fee", "Net PnL (USDT)", 0.0),
    ):
        fig, ax = plt.subplots(figsize=(11, 4.2))
        is_date = False
        for label, s, metrics in curves:
            x, is_date = _x_axis(s["t"])
            x, (series,) = _downsample(x, s[field])
            ax.plot(x, series, linewidth=1.0, label=label + _ret_label(metrics))
        ax.set_title(f"{strategy_dir.name} — {title}")
        ax.set_ylabel(ylabel)
        ax.axhline(baseline, color="grey", linewidth=0.7, linestyle="--", alpha=0.6)
        ax.legend(loc="best", fontsize=9)
        ax.grid(True, alpha=0.3)
        if is_date:
            fig.autofmt_xdate()
        fig.tight_layout()
        png = charts_dir / f"{field}_fee_compare.png"
        fig.savefig(png, dpi=120)
        plt.close(fig)
        outputs.append(png)
    return str(outputs[0].relative_to(strategy_dir))


__all__ = ["render_run_charts", "render_fee_compare"]
