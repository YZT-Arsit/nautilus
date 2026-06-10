"""Display formatting for the shared strategy runner.

Keeps all printing out of ``run_strategy.py``. Event attribute access is
defensive: events that lack ``event_time_ns`` or ``close`` render as ``"-"``
instead of raising, so non-bar event types still print.
"""
from __future__ import annotations

from typing import Any

from feature_engine.examples.synthetic_bars import ONE_SECOND_NS


def _fmt_value(value: float | None, width: int = 10) -> str:
    return f"{value:>{width}.4f}" if value is not None else f"{'—':>{width}}"


def _fmt_time(event: Any, width: int = 6) -> str:
    ts = getattr(event, "event_time_ns", None)
    return f"{ts // ONE_SECOND_NS:>{width}}" if ts is not None else f"{'-':>{width}}"


def _fmt_close(event: Any, width: int = 8) -> str:
    close = getattr(event, "close", None)
    return f"{close:>{width}.2f}" if close is not None else f"{'-':>{width}}"


def print_warmup_summary(strategy_name: str, warmup_count: int, runner: Any, spec_names: list[str]) -> None:
    ready = ", ".join(f"{n}={runner.is_ready(n)}" for n in spec_names)
    print(f"[{strategy_name}] warmed up on {warmup_count} bars; ready: {{{ready}}}\n")


def print_event_table_header(spec_names: list[str]) -> None:
    header = f"{'t(s)':>6}  {'close':>8}  " + "  ".join(f"{n:>10}" for n in spec_names) + "  signal"
    print(header)
    print("-" * len(header))


def print_event_row(event: Any, snapshot: Any, signal: Any, spec_names: list[str]) -> None:
    values = "  ".join(_fmt_value(snapshot.value(n)) for n in spec_names)
    print(f"{_fmt_time(event)}  {_fmt_close(event)}  {values}  {signal}")


# Common trading signals shown first; any other signals follow, sorted.
_PRIMARY_SIGNALS = ("BUY", "SELL", "HOLD")


def print_signal_summary(signal_counts: dict[str, int]) -> None:
    """Print ``signal counts: BUY=X SELL=Y HOLD=Z`` plus any extra signals."""
    ordered = [s for s in _PRIMARY_SIGNALS if s in signal_counts]
    ordered += sorted(s for s in signal_counts if s not in _PRIMARY_SIGNALS)
    body = " ".join(f"{s}={signal_counts[s]}" for s in ordered) or "(none)"
    print(f"\nsignal counts: {body}")
