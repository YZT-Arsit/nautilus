"""Synthetic bar events for demos and tests.

A :class:`BarEvent` carries the minimal surface the feature engine routes on:
``event_type == "bar"`` (so specs with ``input_type="bar"`` match), an
``instrument_id``, an ``event_time_ns`` timestamp, and the OHLCV fields that
specs read via ``input_field``.
"""
from __future__ import annotations

from dataclasses import dataclass

# 1 second expressed in nanoseconds — the default spacing between bars.
ONE_SECOND_NS = 1_000_000_000


@dataclass
class BarEvent:
    """A minimal bar event understood by ``SpecFeatureEngine``."""

    close: float
    open: float
    high: float
    low: float
    volume: float
    instrument_id: str
    event_time_ns: int
    event_type: str = "bar"


def make_bars(
    closes: list[float],
    instrument_id: str = "BTC/USDT",
    start_ns: int = 0,
    step_ns: int = ONE_SECOND_NS,
) -> list[BarEvent]:
    """Build a list of evenly spaced :class:`BarEvent` from close prices.

    ``open``/``high``/``low`` are derived from ``close`` purely so the bars look
    plausible; only ``close`` matters to the MA crossover demo.
    """
    return [
        BarEvent(
            close=close,
            open=close - 0.5,
            high=close + 1.0,
            low=close - 1.0,
            volume=100.0,
            instrument_id=instrument_id,
            event_time_ns=start_ns + i * step_ns,
        )
        for i, close in enumerate(closes)
    ]
