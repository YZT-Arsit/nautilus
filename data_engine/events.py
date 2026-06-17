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
