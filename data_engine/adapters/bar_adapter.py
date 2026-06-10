"""Construct :class:`BarEvent` objects from raw values.

``make_bar_event`` fills missing OHLV fields with sensible defaults (open/high/low
default to ``close``, volume to ``0.0``). ``make_bars`` builds an evenly spaced
list of bars from a list of closes — the synthetic/demo helper.
"""
from __future__ import annotations

from data_engine.events import BarEvent
from data_engine.time import ONE_SECOND_NS


def make_bar_event(
    *,
    close: float,
    open: float | None = None,
    high: float | None = None,
    low: float | None = None,
    volume: float | None = None,
    instrument_id: str,
    event_time_ns: int,
) -> BarEvent:
    """Build one bar, defaulting open/high/low to ``close`` and volume to ``0.0``."""
    return BarEvent(
        close=close,
        open=close if open is None else open,
        high=close if high is None else high,
        low=close if low is None else low,
        volume=0.0 if volume is None else volume,
        instrument_id=instrument_id,
        event_time_ns=event_time_ns,
    )


def make_bars(
    closes: list[float],
    instrument_id: str = "BTC/USDT",
    start_ns: int = 0,
    step_ns: int = ONE_SECOND_NS,
) -> list[BarEvent]:
    """Build a list of evenly spaced bars from close prices."""
    return [
        make_bar_event(
            close=close,
            instrument_id=instrument_id,
            event_time_ns=start_ns + i * step_ns,
        )
        for i, close in enumerate(closes)
    ]
