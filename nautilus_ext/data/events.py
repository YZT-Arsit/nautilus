from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from nautilus_ext.strategies.signal_types import BarInput


@dataclass(frozen=True)
class QuoteTickEvent:
    instrument_id: str
    bid_price: float
    ask_price: float
    bid_size: float | None
    ask_size: float | None
    ts_event: datetime
    ts_init: datetime | None = None
    source: str | None = None

    @property
    def mid_price(self) -> float:
        return (self.bid_price + self.ask_price) / 2.0


@dataclass(frozen=True)
class BarEvent:
    instrument_id: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    ts_event: datetime
    ts_init: datetime | None = None
    source: str | None = None
    volume_type: str = "unknown"


def bar_event_to_bar_input(bar: BarEvent) -> BarInput:
    return BarInput(
        open=bar.open,
        high=bar.high,
        low=bar.low,
        close=bar.close,
        volume=bar.volume,
    )
