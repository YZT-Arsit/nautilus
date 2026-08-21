"""Executed-position episode/de-risk break-even cost analytics.

The primary metric follows the research contract: an episode is measured from
an executed-position opening (or the residual position after a prior de-risk)
through the next partial reduction, close, or reversal.  It consumes persisted
gross-return and turnover streams; it does not replay strategy signals.
"""
from __future__ import annotations

import csv
import math
import statistics
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable


EPISODE_COLUMNS = (
    "episode_id",
    "strategy_id",
    "strategy",
    "variant",
    "symbol",
    "timeframe",
    "granularity",
    "lag",
    "premium_mode",
    "side",
    "start_timestamp",
    "completion_timestamp",
    "start_position",
    "maximum_abs_position",
    "end_position",
    "completion_reason",
    "delta_turnover",
    "delta_gross_return",
    "break_even_bps",
)


def _side(position: float) -> str:
    return "LONG" if position > 0.0 else "SHORT"


def _iso(timestamp_ns: int) -> str:
    return datetime.fromtimestamp(timestamp_ns / 1e9, tz=UTC).isoformat()


def build_de_risk_episodes(
    *,
    event_time_ns: Iterable[int],
    executed_position: Iterable[float],
    turnover_increment: Iterable[float],
    gross_return_increment: Iterable[float],
    strategy: str,
    symbol: str,
    granularity: str,
    lag: str,
    premium_mode: str,
    variant: str = "original",
    tolerance: float = 1e-12,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Segment an executed stream at reductions, closes, and reversals.

    Reversal turnover is deterministically split in proportion to the closing
    and opening absolute quantities.  For ``+0.8 -> -0.4``, two thirds of the
    position-change turnover closes the long and one third opens the short.
    Return accrued up to the reversal timestamp belongs to the closing episode;
    the new episode starts from the post-fill cumulative-return boundary.
    """
    times = [int(value) for value in event_time_ns]
    positions = [float(value) for value in executed_position]
    turnover = [float(value) for value in turnover_increment]
    returns = [float(value) for value in gross_return_increment]
    length = len(times)
    if not all(len(values) == length for values in (positions, turnover, returns)):
        raise ValueError("episode source columns must have equal length")
    if any(right < left for left, right in zip(times, times[1:])):
        raise ValueError("episode timestamps must be non-decreasing")
    if any(value < -tolerance for value in turnover):
        raise ValueError("turnover increments cannot be negative")

    episodes: list[dict[str, Any]] = []
    cumulative_return = 0.0
    active: dict[str, Any] | None = None
    previous_position = 0.0
    unmatched_turnover = 0.0

    def start_episode(timestamp: int, position: float, opening_turnover: float) -> dict[str, Any]:
        return {
            "side": _side(position),
            "start_timestamp": _iso(timestamp),
            "start_position": position,
            "maximum_abs_position": abs(position),
            "start_cumulative_return": cumulative_return,
            "turnover": opening_turnover,
        }

    def complete(
        timestamp: int,
        end_position: float,
        reason: str,
        closing_turnover: float,
    ) -> None:
        nonlocal active
        if active is None:
            return
        delta_turnover = float(active["turnover"]) + closing_turnover
        delta_return = cumulative_return - float(active["start_cumulative_return"])
        if delta_turnover <= tolerance:
            active = None
            return
        break_even = delta_return * 10_000.0 / delta_turnover
        episodes.append(
            {
                "episode_id": len(episodes) + 1,
                "strategy_id": strategy,
                "strategy": strategy,
                "variant": variant,
                "symbol": symbol,
                "timeframe": granularity,
                "granularity": granularity,
                "lag": lag,
                "premium_mode": premium_mode,
                "side": active["side"],
                "start_timestamp": active["start_timestamp"],
                "completion_timestamp": _iso(timestamp),
                "start_position": active["start_position"],
                "maximum_abs_position": active["maximum_abs_position"],
                "end_position": end_position,
                "completion_reason": reason,
                "delta_turnover": delta_turnover,
                "delta_gross_return": delta_return,
                "break_even_bps": break_even,
            }
        )
        active = None

    for timestamp, position, turnover_delta, return_delta in zip(
        times, positions, turnover, returns, strict=True
    ):
        cumulative_return += return_delta
        previous_abs = abs(previous_position)
        current_abs = abs(position)
        reversed_position = (
            previous_abs > tolerance
            and current_abs > tolerance
            and math.copysign(1.0, previous_position) != math.copysign(1.0, position)
        )

        if reversed_position:
            denominator = previous_abs + current_abs
            closing_turnover = turnover_delta * previous_abs / denominator
            opening_turnover = turnover_delta - closing_turnover
            complete(timestamp, position, "reversal", closing_turnover)
            active = start_episode(timestamp, position, opening_turnover)
        elif previous_abs <= tolerance and current_abs > tolerance:
            active = start_episode(timestamp, position, turnover_delta)
        elif previous_abs > tolerance and current_abs <= tolerance:
            complete(timestamp, position, "close", turnover_delta)
        elif previous_abs > tolerance and current_abs < previous_abs - tolerance:
            complete(timestamp, position, "partial_reduce", turnover_delta)
            # The residual exposure becomes an explicit continuation episode.
            # It starts after the completed de-risk event with no invented
            # turnover; subsequent real turnover remains fully auditable.
            if current_abs > tolerance:
                active = start_episode(timestamp, position, 0.0)
        elif active is not None:
            active["turnover"] = float(active["turnover"]) + turnover_delta
            active["maximum_abs_position"] = max(
                float(active["maximum_abs_position"]), current_abs
            )
        else:
            unmatched_turnover += turnover_delta
        previous_position = position

    completed_turnover = sum(float(row["delta_turnover"]) for row in episodes)
    open_turnover = float(active["turnover"]) if active is not None else 0.0
    total_turnover = sum(turnover)
    residual = total_turnover - completed_turnover - open_turnover - unmatched_turnover
    maximum_be_residual = max(
        (
            abs(
                float(row["delta_gross_return"])
                - float(row["delta_turnover"]) * float(row["break_even_bps"]) / 10_000.0
            )
            for row in episodes
        ),
        default=0.0,
    )
    break_even_values = [float(row["break_even_bps"]) for row in episodes]
    summary = {
        "completed_episode_count": len(episodes),
        "partial_reduce_count": sum(row["completion_reason"] == "partial_reduce" for row in episodes),
        "close_count": sum(row["completion_reason"] == "close" for row in episodes),
        "reversal_count": sum(row["completion_reason"] == "reversal" for row in episodes),
        "open_unfinished_episode_count": int(active is not None),
        "completed_turnover": completed_turnover,
        "open_episode_turnover": open_turnover,
        "unmatched_turnover": unmatched_turnover,
        "turnover_reconciliation_residual": residual,
        "maximum_break_even_residual": maximum_be_residual,
        "break_even_bps_min": min(break_even_values, default=None),
        "break_even_bps_median": (
            statistics.median(break_even_values) if break_even_values else None
        ),
        "break_even_bps_mean": (
            statistics.fmean(break_even_values) if break_even_values else None
        ),
        "break_even_bps_max": max(break_even_values, default=None),
    }
    return episodes, summary


def write_episode_csv(path: str | Path, rows: list[dict[str, Any]]) -> str:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    with temporary.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=EPISODE_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(destination)
    return str(destination)


def render_episode_break_even(
    rows: list[dict[str, Any]], *, destination: str | Path, title: str
) -> str:
    """Render signed episode break-even points without interpolation."""
    import matplotlib as mpl

    mpl.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt

    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    figure, axis = plt.subplots(figsize=(15, 5.5))
    colors = {"included": "#2563eb", "excluded": "#dd7a0c"}
    for premium_mode in ("included", "excluded"):
        selected = [row for row in rows if row["premium_mode"] == premium_mode]
        axis.scatter(
            [datetime.fromisoformat(row["completion_timestamp"]) for row in selected],
            [float(row["break_even_bps"]) for row in selected],
            s=12,
            alpha=0.65,
            color=colors[premium_mode],
            label=("With premium" if premium_mode == "included" else "Without premium"),
        )
    axis.axhline(0.0, color="black", linewidth=0.8, alpha=0.7)
    axis.set_title(title)
    axis.set_xlabel("Episode completion / de-risk time (UTC)")
    axis.set_ylabel("Break-even Cost (bps, signed)")
    axis.grid(alpha=0.22)
    axis.legend(loc="best")
    locator = mdates.AutoDateLocator(minticks=5, maxticks=10)
    axis.xaxis.set_major_locator(locator)
    axis.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator))
    figure.tight_layout()
    temporary = path.with_suffix(path.suffix + ".tmp")
    figure.savefig(temporary, dpi=150, format=path.suffix.lstrip("."))
    plt.close(figure)
    temporary.replace(path)
    return str(path)
