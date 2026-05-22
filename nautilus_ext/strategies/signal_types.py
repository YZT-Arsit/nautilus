from __future__ import annotations
from dataclasses import dataclass
@dataclass(frozen=True)
class BarInput:
    open: float
    high: float
    low: float
    close: float
    volume: float
@dataclass(frozen=True)
class SignalResult:
    entry_side: str | None = None
    entry_order_type: str | None = None
    entry_price: float | None = None
    exit_side: str | None = None
    cancel_entry: bool = False
    reason: str | None = None
    debug: dict | None = None