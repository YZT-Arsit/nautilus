"""
Strategy evaluation series and chart built from canonical backtest artifacts.

The calculations in this module do not replay signals or invent an accounting
path.  They derive return, funding/premium attribution, turnover, position and
drawdown from the equity rows and fills already produced by the backtest.
"""
from __future__ import annotations

import csv
import json
import math
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from strategy_framework.execution.reports import FillRecord


def signed_break_even_bps(total_return: float, turnover: float) -> float:
    """Solve ``return - turnover * cost_bps / 10_000 == 0`` without sign edits."""
    return total_return / turnover * 10_000.0 if turnover > 0.0 else math.nan


def _drawdown(equity: Iterable[float]) -> list[float]:
    values: list[float] = []
    peak = float("-inf")
    for value in equity:
        peak = max(peak, value)
        values.append(value / peak - 1.0 if peak > 0.0 else 0.0)
    return values


def build_strategy_evaluation(
    equity_rows: list[dict[str, Any]],
    fills: list[FillRecord],
    *,
    initial_cash: float,
) -> tuple[list[dict[str, float | int]], dict[str, dict[str, float]]]:
    """Build premium/no-premium 1x returns and execution-derived diagnostics."""
    if initial_cash <= 0.0:
        raise ValueError("initial_cash must be positive")
    rows = sorted(equity_rows, key=lambda row: int(row["event_time_ns"]))
    ordered_fills = sorted(fills, key=lambda fill: int(fill.event_time_ns or 0))
    fill_index = 0
    turnover_notional = 0.0
    series: list[dict[str, float | int]] = []

    for row in rows:
        timestamp = int(row["event_time_ns"])
        while (
            fill_index < len(ordered_fills)
            and int(ordered_fills[fill_index].event_time_ns or 0) <= timestamp
        ):
            fill = ordered_fills[fill_index]
            turnover_notional += abs(float(fill.quantity) * float(fill.price))
            fill_index += 1
        funding_pnl = float(row.get("funding_pnl") or 0.0)
        net_pnl = float(row.get("net_pnl") or 0.0)
        close = float(row.get("close") or 0.0)
        position = float(row.get("position") or 0.0)
        leverage = row.get("position_leverage_pct")
        if leverage is None:
            leverage = position * close / initial_cash * 100.0
        series.append(
            {
                "event_time_ns": timestamp,
                "return_with_premium": net_pnl / initial_cash,
                "return_without_premium": (net_pnl - funding_pnl) / initial_cash,
                "funding_return": funding_pnl / initial_cash,
                "cumulative_turnover": turnover_notional / initial_cash,
                "position": position,
                "position_leverage_pct": float(leverage),
            }
        )

    with_equity = [initial_cash * (1.0 + float(row["return_with_premium"])) for row in series]
    without_equity = [
        initial_cash * (1.0 + float(row["return_without_premium"])) for row in series
    ]
    with_drawdown = _drawdown(with_equity)
    without_drawdown = _drawdown(without_equity)
    for row, premium_dd, no_premium_dd in zip(
        series, with_drawdown, without_drawdown, strict=True
    ):
        row["drawdown_with_premium"] = premium_dd
        row["drawdown_without_premium"] = no_premium_dd

    turnover = float(series[-1]["cumulative_turnover"]) if series else 0.0
    final_with = float(series[-1]["return_with_premium"]) if series else 0.0
    final_without = float(series[-1]["return_without_premium"]) if series else 0.0
    metrics = {
        "included": {
            "final_return_1x": final_with,
            "turnover": turnover,
            "break_even_bps": signed_break_even_bps(final_with, turnover),
            "max_drawdown": min(with_drawdown, default=0.0),
        },
        "excluded": {
            "final_return_1x": final_without,
            "turnover": turnover,
            "break_even_bps": signed_break_even_bps(final_without, turnover),
            "max_drawdown": min(without_drawdown, default=0.0),
        },
    }
    return series, metrics


def validate_strategy_evaluation(
    series: list[dict[str, float | int]],
    metrics: dict[str, dict[str, float]],
    *,
    tolerance: float = 1e-10,
) -> dict[str, bool]:
    """Validate the report identities without changing their definitions."""
    checks: dict[str, bool] = {}
    for premium, return_column, drawdown_column in (
        ("included", "return_with_premium", "drawdown_with_premium"),
        ("excluded", "return_without_premium", "drawdown_without_premium"),
    ):
        values = metrics[premium]
        if values["turnover"] == 0.0:
            checks[f"break_even_zero_{premium}"] = (
                math.isclose(
                    values["final_return_1x"], 0.0, rel_tol=0.0, abs_tol=tolerance
                )
                and math.isnan(values["break_even_bps"])
            )
        else:
            break_even_net = values["final_return_1x"] - (
                values["turnover"] * values["break_even_bps"] / 10_000.0
            )
            checks[f"break_even_zero_{premium}"] = math.isclose(
                break_even_net, 0.0, rel_tol=0.0, abs_tol=tolerance
            )
        checks[f"break_even_sign_preserved_{premium}"] = (
            values["final_return_1x"] == 0.0
            or math.copysign(1.0, values["break_even_bps"])
            == math.copysign(1.0, values["final_return_1x"])
        )
        final_return = float(series[-1][return_column]) if series else 0.0
        checks[f"final_return_{premium}"] = math.isclose(
            final_return, values["final_return_1x"], rel_tol=0.0, abs_tol=tolerance
        )
        worst = min((float(row[drawdown_column]) for row in series), default=0.0)
        checks[f"max_drawdown_{premium}"] = math.isclose(
            worst, values["max_drawdown"], rel_tol=0.0, abs_tol=tolerance
        )
    checks["premium_identity"] = all(
        math.isclose(
            float(row["return_with_premium"])
            - float(row["return_without_premium"]),
            float(row["funding_return"]),
            rel_tol=0.0,
            abs_tol=tolerance,
        )
        for row in series
    )
    checks["turnover_shared"] = math.isclose(
        metrics["included"]["turnover"],
        metrics["excluded"]["turnover"],
        rel_tol=0.0,
        abs_tol=tolerance,
    )
    if not all(checks.values()):
        failed = [name for name, passed in checks.items() if not passed]
        raise AssertionError(f"strategy evaluation validation failed: {failed}")
    return checks


def build_additive_strategy_evaluation(
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, float | int]], dict[str, dict[str, float]]]:
    """Build the common report from a saved additive-return result stream.

    This path is used by the existing strict-constant-notional bar experiments.
    It consumes their persisted executed direction and accounting components; it
    does not replay signals or alter any strategy/execution formula.
    """
    ordered = sorted(rows, key=lambda row: int(row["event_time_ns"]))
    cumulative_trading = 0.0
    cumulative_funding = 0.0
    cumulative_turnover = 0.0
    series: list[dict[str, float | int]] = []
    premium_equity: list[float] = []
    no_premium_equity: list[float] = []
    for row in ordered:
        cumulative_trading += float(row["trading_return"])
        cumulative_funding += float(row["funding_return"])
        cumulative_turnover += float(row["turnover"])
        with_premium = cumulative_trading + cumulative_funding
        without_premium = cumulative_trading
        premium_equity.append(1.0 + with_premium)
        no_premium_equity.append(1.0 + without_premium)
        direction = float(row["executed_direction"])
        series.append(
            {
                "event_time_ns": int(row["event_time_ns"]),
                "return_with_premium": with_premium,
                "return_without_premium": without_premium,
                "funding_return": cumulative_funding,
                "cumulative_turnover": cumulative_turnover,
                "position": direction,
                "position_leverage_pct": direction * 100.0,
            }
        )

    # Additive 1x accounting starts from equity 1.0 before the first row.
    premium_dd = _drawdown([1.0, *premium_equity])[1:]
    no_premium_dd = _drawdown([1.0, *no_premium_equity])[1:]
    for row, with_dd, without_dd in zip(
        series, premium_dd, no_premium_dd, strict=True
    ):
        row["drawdown_with_premium"] = with_dd
        row["drawdown_without_premium"] = without_dd

    turnover = cumulative_turnover
    final_with = cumulative_trading + cumulative_funding
    final_without = cumulative_trading
    metrics = {
        "included": {
            "final_return_1x": final_with,
            "turnover": turnover,
            "break_even_bps": signed_break_even_bps(final_with, turnover),
            "max_drawdown": min(premium_dd, default=0.0),
        },
        "excluded": {
            "final_return_1x": final_without,
            "turnover": turnover,
            "break_even_bps": signed_break_even_bps(final_without, turnover),
            "max_drawdown": min(no_premium_dd, default=0.0),
        },
    }
    return series, metrics


def build_additive_strategy_evaluation_from_columns(
    *,
    event_time_ns: Any,
    trading_return: Any,
    funding_return: Any,
    turnover: Any,
    executed_direction: Any,
    max_points: int = 5_000,
) -> tuple[list[dict[str, float | int]], dict[str, dict[str, float]]]:
    """Vectorized equivalent for multi-million-row persisted bar runs.

    Metrics are calculated on every source row.  Only the returned plotting
    series is sampled, with both exact drawdown minima and the final row forced
    into the sample so that the displayed and reported extrema agree.
    """
    import numpy as np

    times = np.asarray(event_time_ns, dtype=np.int64)
    trading = np.asarray(trading_return, dtype=np.float64)
    funding = np.asarray(funding_return, dtype=np.float64)
    traded = np.asarray(turnover, dtype=np.float64)
    direction = np.asarray(executed_direction, dtype=np.float64)
    length = len(times)
    if not all(len(value) == length for value in (trading, funding, traded, direction)):
        raise ValueError("saved result columns must have equal length")
    if length == 0:
        return [], {
            "included": {"final_return_1x": 0.0, "turnover": 0.0, "break_even_bps": math.nan, "max_drawdown": 0.0},
            "excluded": {"final_return_1x": 0.0, "turnover": 0.0, "break_even_bps": math.nan, "max_drawdown": 0.0},
        }
    if np.any(np.diff(times) < 0):
        raise ValueError("saved result timestamps are not ordered")
    if np.any(traded < 0.0):
        raise ValueError("turnover cannot be negative")

    cumulative_trading = np.cumsum(trading)
    cumulative_funding = np.cumsum(funding)
    with_premium = cumulative_trading + cumulative_funding
    without_premium = cumulative_trading
    cumulative_turnover = np.cumsum(traded)
    with_equity = 1.0 + with_premium
    without_equity = 1.0 + without_premium
    with_peak = np.maximum.accumulate(np.concatenate(([1.0], with_equity)))[1:]
    without_peak = np.maximum.accumulate(np.concatenate(([1.0], without_equity)))[1:]
    with_drawdown = np.divide(
        with_equity,
        with_peak,
        out=np.zeros_like(with_equity),
        where=with_peak > 0.0,
    ) - 1.0
    without_drawdown = np.divide(
        without_equity,
        without_peak,
        out=np.zeros_like(without_equity),
        where=without_peak > 0.0,
    ) - 1.0

    total_turnover = float(cumulative_turnover[-1])
    final_with = float(with_premium[-1])
    final_without = float(without_premium[-1])
    metrics = {
        "included": {
            "final_return_1x": final_with,
            "turnover": total_turnover,
            "break_even_bps": signed_break_even_bps(final_with, total_turnover),
            "max_drawdown": float(np.min(with_drawdown)),
        },
        "excluded": {
            "final_return_1x": final_without,
            "turnover": total_turnover,
            "break_even_bps": signed_break_even_bps(final_without, total_turnover),
            "max_drawdown": float(np.min(without_drawdown)),
        },
    }
    step = max(1, math.ceil(length / max_points))
    indexes = set(range(0, length, step))
    indexes.update((length - 1, int(np.argmin(with_drawdown)), int(np.argmin(without_drawdown))))
    series = [
        {
            "event_time_ns": int(times[index]),
            "return_with_premium": float(with_premium[index]),
            "return_without_premium": float(without_premium[index]),
            "funding_return": float(cumulative_funding[index]),
            "cumulative_turnover": float(cumulative_turnover[index]),
            "position": float(direction[index]),
            "position_leverage_pct": float(direction[index] * 100.0),
            "drawdown_with_premium": float(with_drawdown[index]),
            "drawdown_without_premium": float(without_drawdown[index]),
        }
        for index in sorted(indexes)
    ]
    return series, metrics


def render_additive_strategy_evaluation(
    series: list[dict[str, float | int]],
    metrics: dict[str, dict[str, float]],
    *,
    destination: str | Path,
    run_name: str,
    lag_label: str,
) -> str:
    """Render one deterministic three-panel figure for a saved bar run."""
    if not series:
        raise ValueError("strategy evaluation requires at least one result row")
    import matplotlib as mpl

    mpl.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt

    count = len(series)
    step = max(1, count // 5_000)
    selected = set(range(0, count, step))
    selected.add(count - 1)
    for column in ("drawdown_with_premium", "drawdown_without_premium"):
        selected.add(min(range(count), key=lambda index: float(series[index][column])))
    indexes = sorted(selected)
    timestamps = [
        datetime.fromtimestamp(float(series[index]["event_time_ns"]) / 1e9, tz=UTC)
        for index in indexes
    ]

    def values(column: str) -> list[float]:
        return [float(series[index][column]) for index in indexes]

    figure, axes = plt.subplots(
        3,
        1,
        figsize=(15, 10),
        sharex=True,
        gridspec_kw={"height_ratios": [2.0, 1.0, 1.0]},
    )
    upper, position_axis, drawdown_axis = axes
    upper.plot(
        timestamps,
        [value * 100.0 for value in values("return_with_premium")],
        linewidth=1.15,
        label="Return — with premium",
    )
    upper.plot(
        timestamps,
        [value * 100.0 for value in values("return_without_premium")],
        linewidth=1.0,
        linestyle="--",
        label="Return — without premium",
    )
    upper.axhline(0.0, color="grey", linewidth=0.7, alpha=0.65)
    upper.set_ylabel("Cumulative Return (1x, %)")
    turnover_axis = upper.twinx()
    turnover_axis.plot(
        timestamps,
        values("cumulative_turnover"),
        color="#6a3d9a",
        linewidth=0.9,
        linestyle=":",
        label="Cumulative turnover",
    )
    turnover_axis.set_ylabel("Cumulative Turnover (x capital)")
    handles, labels = upper.get_legend_handles_labels()
    right_handles, right_labels = turnover_axis.get_legend_handles_labels()
    upper.legend(handles + right_handles, labels + right_labels, loc="best", fontsize=9)

    position_axis.plot(timestamps, values("position_leverage_pct"), linewidth=0.85)
    position_axis.axhline(0.0, color="grey", linewidth=0.7, alpha=0.65)
    position_axis.set_ylabel("Executed Position\n(signed leverage, %)")

    drawdown_axis.plot(
        timestamps,
        [value * 100.0 for value in values("drawdown_with_premium")],
        linewidth=1.0,
        label="Drawdown — with premium",
    )
    drawdown_axis.plot(
        timestamps,
        [value * 100.0 for value in values("drawdown_without_premium")],
        linewidth=0.9,
        linestyle="--",
        label="Drawdown — without premium",
    )
    drawdown_axis.set_ylabel("Drawdown (%)")
    drawdown_axis.set_xlabel("UTC time")
    drawdown_axis.legend(loc="best", fontsize=9)
    locator = mdates.AutoDateLocator(minticks=5, maxticks=9)
    drawdown_axis.xaxis.set_major_locator(locator)
    drawdown_axis.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator))
    for axis in axes:
        axis.grid(alpha=0.22)

    included = metrics["included"]
    excluded = metrics["excluded"]
    figure.suptitle(
        f"{run_name} — Strategy Evaluation\n"
        f"lag={lag_label} | BE bps premium={included['break_even_bps']:.4f}, "
        f"no-premium={excluded['break_even_bps']:.4f}\n"
        f"MDD premium={included['max_drawdown']:.2%}, "
        f"no-premium={excluded['max_drawdown']:.2%}",
        fontsize=13,
    )
    figure.tight_layout(rect=(0, 0, 1, 0.84))
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    figure.savefig(temporary, dpi=150, format=path.suffix.lstrip("."))
    plt.close(figure)
    temporary.replace(path)
    return str(path)


def _write_parquet(path: Path, rows: list[dict[str, float | int]]) -> None:
    """Write the evaluation series using the repository's server dependencies."""
    import polars as pl

    temporary = path.with_suffix(path.suffix + ".tmp")
    pl.DataFrame(rows).write_parquet(temporary)
    temporary.replace(path)


def render_strategy_evaluation(
    series: list[dict[str, float | int]],
    metrics: dict[str, dict[str, float]],
    *,
    output_dir: str | Path,
    run_name: str,
    lag_ns: int,
) -> dict[str, str]:
    """Persist one aligned three-panel evaluation figure and its source series."""
    if not series:
        raise ValueError("strategy evaluation requires at least one equity row")
    import matplotlib as mpl

    mpl.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt

    output = Path(output_dir)
    charts = output / "charts"
    charts.mkdir(parents=True, exist_ok=True)
    series_path = output / "strategy_evaluation.parquet"
    _write_parquet(series_path, series)

    count = len(series)
    step = max(1, count // 5_000)
    selected = set(range(0, count, step))
    selected.add(count - 1)
    for column in ("drawdown_with_premium", "drawdown_without_premium"):
        selected.add(min(range(count), key=lambda index: float(series[index][column])))
    indexes = sorted(selected)
    timestamps = [
        datetime.fromtimestamp(float(series[index]["event_time_ns"]) / 1e9, tz=UTC)
        for index in indexes
    ]
    values = {
        column: [float(series[index][column]) for index in indexes]
        for column in (
            "return_with_premium",
            "return_without_premium",
            "cumulative_turnover",
            "position_leverage_pct",
            "drawdown_with_premium",
            "drawdown_without_premium",
        )
    }

    figure, axes = plt.subplots(
        3,
        1,
        figsize=(15, 10),
        sharex=True,
        gridspec_kw={"height_ratios": [2.0, 1.0, 1.0]},
    )
    upper, position_axis, drawdown_axis = axes
    upper.plot(
        timestamps,
        [value * 100.0 for value in values["return_with_premium"]],
        linewidth=1.15,
        label="Return — with premium",
    )
    upper.plot(
        timestamps,
        [value * 100.0 for value in values["return_without_premium"]],
        linewidth=1.0,
        linestyle="--",
        label="Return — without premium",
    )
    upper.axhline(0.0, color="grey", linewidth=0.7, alpha=0.65)
    upper.set_ylabel("Cumulative Return (1x, %)")
    turnover_axis = upper.twinx()
    turnover_axis.plot(
        timestamps,
        values["cumulative_turnover"],
        color="#6a3d9a",
        linewidth=0.9,
        linestyle=":",
        label="Cumulative turnover",
    )
    turnover_axis.set_ylabel("Cumulative Turnover (x capital)")
    handles, labels = upper.get_legend_handles_labels()
    right_handles, right_labels = turnover_axis.get_legend_handles_labels()
    upper.legend(handles + right_handles, labels + right_labels, loc="best", fontsize=9)

    position_axis.plot(timestamps, values["position_leverage_pct"], linewidth=0.85)
    position_axis.axhline(0.0, color="grey", linewidth=0.7, alpha=0.65)
    position_axis.set_ylabel("Executed Position\n(signed leverage, %)")

    drawdown_axis.plot(
        timestamps,
        [value * 100.0 for value in values["drawdown_with_premium"]],
        linewidth=1.0,
        label="Drawdown — with premium",
    )
    drawdown_axis.plot(
        timestamps,
        [value * 100.0 for value in values["drawdown_without_premium"]],
        linewidth=0.9,
        linestyle="--",
        label="Drawdown — without premium",
    )
    drawdown_axis.set_ylabel("Drawdown (%)")
    drawdown_axis.set_xlabel("UTC time")
    drawdown_axis.legend(loc="best", fontsize=9)
    locator = mdates.AutoDateLocator(minticks=5, maxticks=9)
    drawdown_axis.xaxis.set_major_locator(locator)
    drawdown_axis.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator))

    for axis in axes:
        axis.grid(alpha=0.22)
    lag_label = "0 ns (first following TradeEvent)" if lag_ns == 0 else f"{lag_ns / 1e9:g} s physical-time"
    included = metrics["included"]
    excluded = metrics["excluded"]
    figure.suptitle(
        f"{run_name} — Strategy Evaluation\n"
        f"lag={lag_label} | BE bps premium={included['break_even_bps']:.4f}, "
        f"no-premium={excluded['break_even_bps']:.4f}\n"
        f"MDD premium={included['max_drawdown']:.2%}, "
        f"no-premium={excluded['max_drawdown']:.2%}",
        fontsize=13,
    )
    figure.tight_layout(rect=(0, 0, 1, 0.84))
    chart_path = charts / "strategy_evaluation.png"
    figure.savefig(chart_path, dpi=150)
    pnl_chart_path = charts / "pnl.png"
    figure.savefig(pnl_chart_path, dpi=150)
    plt.close(figure)

    metrics_path = output / "strategy_evaluation_metrics.json"
    metrics_path.write_text(
        json.dumps(
            {
                "run_name": run_name,
                "lag_ns": lag_ns,
                "premium_definition": "funding_pnl",
                "turnover_definition": "sum(abs(fill_quantity * fill_price)) / initial_cash",
                "cost_equation": "net_return = return - turnover * cost_bps / 10000",
                "cases": metrics,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return {
        "chart": str(chart_path),
        "pnl_chart": str(pnl_chart_path),
        "series": str(series_path),
        "metrics": str(metrics_path),
    }


def evaluate_existing_run(
    run_dir: str | Path,
    *,
    lag_ns: int,
) -> tuple[dict[str, dict[str, float]], dict[str, bool], dict[str, str]]:
    """Regenerate an evaluation from one canonical completed run directory."""
    import polars as pl

    run_path = Path(run_dir)
    equity_path = run_path / "equity_curve.parquet"
    if not equity_path.is_file():
        raise FileNotFoundError(equity_path)
    metrics = json.loads((run_path / "metrics.json").read_text(encoding="utf-8"))
    equity_rows = pl.read_parquet(equity_path).to_dicts()
    fills: list[FillRecord] = []
    with (run_path / "fills.csv").open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            fills.append(
                FillRecord(
                    instrument_id=row["instrument_id"],
                    side=row["side"],
                    quantity=float(row["quantity"]),
                    price=float(row["fill_price"]),
                    event_time_ns=int(row["event_time_ns"]),
                    source=row.get("source") or "simulated",
                )
            )
    series, evaluation = build_strategy_evaluation(
        equity_rows,
        fills,
        initial_cash=float(metrics["initial_cash"]),
    )
    validation = validate_strategy_evaluation(series, evaluation)
    outputs = render_strategy_evaluation(
        series,
        evaluation,
        output_dir=run_path,
        run_name=str(metrics.get("run_name") or run_path.name),
        lag_ns=lag_ns,
    )
    (run_path / "strategy_evaluation_validation.json").write_text(
        json.dumps(validation, indent=2), encoding="utf-8"
    )
    return evaluation, validation, outputs


__all__ = [
    "build_additive_strategy_evaluation",
    "build_additive_strategy_evaluation_from_columns",
    "build_strategy_evaluation",
    "evaluate_existing_run",
    "render_additive_strategy_evaluation",
    "render_strategy_evaluation",
    "signed_break_even_bps",
    "validate_strategy_evaluation",
]
