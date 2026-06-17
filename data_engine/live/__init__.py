"""Binance live market-data adapter (self-owned, Nautilus-free).

Milestone 1: pure message **normalization** only.  Raw Binance WS messages are
mapped to the same canonical :class:`~data_engine.events.TradeEvent` /
:class:`~data_engine.events.QuoteEvent` the historical loader produces, fed by an
injectable mock source — no network, no account, no orders.

A later milestone adds a real async WS transport behind the same seam; Nautilus
(if used at all) stays strictly downstream for live execution.
"""
from data_engine.live.binance_ws import (
    LiveNormalizer,
    normalize_agg_trade,
    normalize_book_ticker,
    normalize_message,
)
from data_engine.live.mock_source import MockMessageSource

__all__ = [
    "normalize_agg_trade",
    "normalize_book_ticker",
    "normalize_message",
    "LiveNormalizer",
    "MockMessageSource",
]
