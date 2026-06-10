"""Dependency-free execution report model.

These records describe the *result* of running intents through an execution
backend (simulated or, later, a real engine). They carry no engine coupling —
**no Nautilus Trader imports here**.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass(frozen=True)
class FillRecord:
    """A single (simulated) fill."""

    instrument_id: str
    side: Literal["BUY", "SELL"]
    quantity: float
    price: float
    event_time_ns: int
    source: str = "simulated"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PositionRecord:
    """A position snapshot at report time."""

    instrument_id: str
    quantity: float
    avg_price: float
    market_price: float
    unrealized_pnl: float
    realized_pnl: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ExecutionReport:
    """Aggregate result of an execution run."""

    backend: str
    total_intents: int
    total_fills: int
    fills: list[FillRecord]
    positions: list[PositionRecord]
    realized_pnl: float
    unrealized_pnl: float
    metadata: dict[str, Any] = field(default_factory=dict)
