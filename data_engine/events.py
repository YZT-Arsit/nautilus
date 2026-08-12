"""Canonical, lightweight market event dataclasses.

These are deliberately plain — no dependency on Nautilus Trader native data
objects. They carry the minimal surface the feature engine routes on:
``event_type``, ``instrument_id``, ``event_time_ns``, and the per-type fields.
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


@dataclass
class TradeEvent:
    """A minimal trade (tick) event understood by ``SpecFeatureEngine``.

    This is **our own** event type — not Nautilus ``TradeTick``. It carries the
    fields a trade feature needs: price, quantity, notional, and aggressor side.

    ``side`` follows the Binance convention derived from ``is_buyer_maker``:
    ``is_buyer_maker=True`` means the buyer was the resting maker, so the trade
    was an aggressive **SELL**; ``is_buyer_maker=False`` is an aggressive **BUY**.
    The engine routes this event by ``event_type="trade"`` (input_type ``trade``).
    """

    event_time_ns: int
    instrument_id: str
    price: float
    quantity: float
    quote_quantity: float | None = None
    side: str | None = None
    is_buyer_maker: bool | None = None
    trade_id: int | str | None = None
    receive_time_ns: int | None = None
    source: str | None = None
    raw: dict | None = None
    event_type: str = "trade"
    quote_quantity_source: str | None = None


@dataclass
class QuoteEvent:
    """A minimal top-of-book quote event (best bid/ask) — **our own** type, not
    Nautilus ``QuoteTick``.

    Produced by the live adapter from a Binance ``bookTicker`` message and shaped
    so it can sit beside :class:`TradeEvent`/`BarEvent` in the same self-owned
    event model.  The engine routes it by ``event_type="quote"`` (input_type
    ``quote``).  ``event_time_ns`` falls back to ``receive_time_ns`` when the feed
    carries no exchange timestamp (spot ``bookTicker`` has none).
    """

    event_time_ns: int
    instrument_id: str
    bid_price: float
    ask_price: float
    bid_size: float | None = None
    ask_size: float | None = None
    update_id: int | None = None
    receive_time_ns: int | None = None
    source: str | None = None
    raw: dict | None = None
    event_type: str = "quote"

    @property
    def mid_price(self) -> float:
        return (self.bid_price + self.ask_price) / 2.0

    @property
    def spread(self) -> float:
        return self.ask_price - self.bid_price


@dataclass
class FundingRateEvent:
    """One perpetual-contract funding settlement observation.

    Positive rates mean longs pay shorts. ``mark_price`` is optional because
    Binance Vision's archived funding files contain the settled rate but not the
    mark; the account layer then uses the latest available contract mark.
    """

    event_time_ns: int
    instrument_id: str
    funding_rate: float
    interval_hours: int | None = None
    mark_price: float | None = None
    source: str | None = None
    event_type: str = "funding_rate"
