"""Self-built chart rendering from a backtest run's ``equity_curve.csv``.

Pure stdlib to read; matplotlib is **optional** and imported lazily — if it is
absent, ``render_run_charts`` returns ``{}`` (never fabricates a chart, never
crashes a run). Produces PNGs under ``<run_dir>/charts/`` so results can be
viewed locally after the (server-side) run is pulled back.
"""
from __future__ import annotations

import csv
from pathlib import Path

# Candidate column names (defensive: the writer's schema may evolve).
_TIME_COLS = ("event_time_ns", "ts_event", "time_ns", "time", "ts")
_EQUITY_COLS = ("equity", "total_equity")
_PNL_COLS = ("pnl", "net_pnl", "realized_pnl")
_POS_COLS = ("position", "position_qty", "net_position")


def _pick(header: list[str], candidates: tuple[str, ...]) -> str | None:
    for c in candidates:
        if c in header:
            return c
    return None


def _load_series(equity_csv: Path) -> dict[str, list[float]]:
    """Read equity_curve.csv into column->list[float] for the columns we plot."""
    with open(equity_csv, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        header = reader.fieldnames or []
        tcol = _pick(header, _TIME_COLS)
        ecol = _pick(header, _EQUITY_COLS)
        pcol = _pick(header, _PNL_COLS)
        poscol = _pick(header, _POS_COLS)
        cols: dict[str, list[float]] = {"t": [], "equity": [], "pnl": [], "position": []}
        for i, row in enumerate(reader):
            cols["t"].append(_num(row.get(tcol), i))
            cols["equity"].append(_num(row.get(ecol)))
            cols["pnl"].append(_num(row.get(pcol)))
            cols["position"].append(_num(row.get(poscol)))
    return cols


def _num(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _drawdown(equity: list[float]) -> list[float]:
    dd, peak = [], float("-inf")
    for e in equity:
        peak = max(peak, e)
        dd.append((e - peak) / peak if peak not in (0.0, float("-inf")) else 0.0)
    return dd


def render_run_charts(run_dir: str | Path) -> dict[str, str]:
    """Render equity / drawdown / pnl / position PNGs for one run.

    Returns ``{chart_name: relative_png_path}``; empty dict if matplotlib is
    missing or there is no ``equity_curve.csv`` to plot.
    """
    run_dir = Path(run_dir)
    equity_csv = run_dir / "equity_curve.csv"
    if not equity_csv.is_file():
        return {}
    try:
        import matplotlib  # noqa: PLC0415

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt  # noqa: PLC0415
    except ImportError:
        return {}

    s = _load_series(equity_csv)
    if not s["t"]:
        return {}
    charts_dir = run_dir / "charts"
    charts_dir.mkdir(parents=True, exist_ok=True)
    out: dict[str, str] = {}

    panels = [
        ("equity_curve", "Equity", s["equity"]),
        ("drawdown", "Drawdown", _drawdown(s["equity"])),
        ("pnl", "PnL", s["pnl"]),
        ("position", "Position", s["position"]),
    ]
    for name, title, series in panels:
        fig, ax = plt.subplots(figsize=(9, 3.2))
        ax.plot(s["t"], series, linewidth=0.9)
        ax.set_title(title)
        ax.grid(True, alpha=0.3)
        fig.tight_layout()
        png = charts_dir / f"{name}.png"
        fig.savefig(png, dpi=110)
        plt.close(fig)
        out[name] = str(png.relative_to(run_dir))
    return out


__all__ = ["render_run_charts"]
