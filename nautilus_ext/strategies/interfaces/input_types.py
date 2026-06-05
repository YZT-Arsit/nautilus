from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class MarketEvent:
    event_type: str = "market"
    ts_event: int | None = None        # milliseconds (legacy field, kept for compatibility)
    ts_init: int | None = None         # milliseconds (legacy field, kept for compatibility)
    instrument_id: str | None = None
    event_time_ns: int | None = None   # nanoseconds — exchange/source timestamp
    receive_time_ns: int | None = None  # nanoseconds — local system reception timestamp


@dataclass(frozen=True)
class BarInput(MarketEvent):
    open: float = 0.0
    high: float = 0.0
    low: float = 0.0
    close: float = 0.0
    volume: float = 0.0
    bar_type: str | None = None
    event_type: str = "bar"


@dataclass(frozen=True)
class TradeTickInput(MarketEvent):
    price: float = 0.0
    size: float = 0.0
    aggressor_side: str | None = None
    trade_id: str | None = None
    event_type: str = "trade_tick"


@dataclass(frozen=True)
class QuoteTickInput(MarketEvent):
    bid_price: float = 0.0
    ask_price: float = 0.0
    bid_size: float | None = None
    ask_size: float | None = None
    event_type: str = "quote_tick"


@dataclass(frozen=True)
class OrderBookInput(MarketEvent):
    bids: list[tuple[float, float]] | None = None
    asks: list[tuple[float, float]] | None = None
    depth: int = 0
    event_type: str = "orderbook"


@dataclass(frozen=True)
class FundingRateInput(MarketEvent):
    funding_rate: float = 0.0
    next_funding_time: int | None = None
    event_type: str = "funding_rate"


@dataclass(frozen=True)
class FeatureVectorInput(MarketEvent):
    features: dict[str, float | int | str | bool | None] | None = None
    event_type: str = "feature_vector"
