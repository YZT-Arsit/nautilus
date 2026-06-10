"""Dependency-free execution intent model.

An *intent* is what the strategy *wants* to happen, expressed without any
execution-engine coupling. Backends translate intents into concrete orders.

This module must stay dependency-free — **no Nautilus Trader imports**.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass(frozen=True)
class OrderIntent:
    """An intended order, decoupled from any broker/engine."""

    instrument_id: str
    side: Literal["BUY", "SELL"]
    quantity: float
    event_time_ns: int
    reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PositionIntent:
    """An intended target position (used when SELL means 'go flat')."""

    instrument_id: str
    target: Literal["LONG", "SHORT", "FLAT"]
    quantity: float
    event_time_ns: int
    reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
