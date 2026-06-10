"""Canonical, lightweight market event dataclasses.

These are deliberately plain — no dependency on Nautilus Trader native data
objects. They carry the minimal surface the feature engine routes on:
``event_type``, ``instrument_id``, ``event_time_ns``, and OHLCV fields.
"""
from __future__ import annotations

from dataclasses import dataclass


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
