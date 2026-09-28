"""Binance WebSocket message normalizers (milestone 1: pure normalization).

Maps raw Binance market-data WS messages to our **own** canonical events:

* ``aggTrade``   -> :class:`data_engine.events.TradeEvent`
* ``bookTicker`` -> :class:`data_engine.events.QuoteEvent`

This is the *same* ``TradeEvent`` the historical Binance Vision loader produces,
so a live feed and the historical cache normalize into one event model — enabling
live/historical parity for replay and offline comparison.

There is **no** network here: these are pure dict-in / event-out functions driven
by an injectable message source (see :mod:`data_engine.live.mock_source`).  No
``websocket``/``asyncio``/``urllib`` import; no ``nautilus_trader``; no account,
no orders.
"""
from __future__ import annotations

from typing import Any, Iterable, Iterator

from data_engine.adapters.trade_adapter import make_trade_event
from data_engine.events import FundingRateEvent, QuoteEvent, TradeEvent

_MS_TO_NS = 1_000_000


def _derive_instrument_id(symbol: str | None, override: str | None) -> str | None:
    if override:
        return override
    return f"{symbol}.BINANCE" if symbol else None


def _ms_to_ns(value: Any) -> int | None:
    if value is None:
        return None
    return int(value) * _MS_TO_NS


def _unwrap(msg: Any) -> Any:
    """Unwrap a combined-stream envelope ``{"stream": ..., "data": {...}}``."""
    if isinstance(msg, dict) and "stream" in msg and "data" in msg:
        return msg["data"]
    return msg


def normalize_agg_trade(msg: dict, *, instrument_id: str | None = None,
                        receive_time_ns: int | None = None) -> TradeEvent | None:
    """Binance ``aggTrade`` message -> :class:`TradeEvent`.

    Trade time ``T`` (ms) is preferred for ``event_time_ns``; falls back to event
    time ``E``, then to ``receive_time_ns``.  Side is derived from ``m``
    (is_buyer_maker) by the shared trade adapter (``m=True`` -> SELL).
    """
    symbol = msg.get("s")
    iid = _derive_instrument_id(symbol, instrument_id)
    price = float(msg["p"])
    quantity = float(msg["q"])
    # USD-M may emit status/heartbeat-shaped ``trade`` frames with X="NA"
    # and zero price/quantity.  They are not executable market trades and must
    # never enter bar construction or execution.
    if price <= 0.0 or quantity <= 0.0:
        return None
    is_buyer_maker = bool(msg["m"]) if "m" in msg else None
    event_time_ns = _ms_to_ns(msg.get("T", msg.get("E")))
    if event_time_ns is None:
        event_time_ns = receive_time_ns if receive_time_ns is not None else 0
    return make_trade_event(
        price=price, quantity=quantity, instrument_id=iid, event_time_ns=event_time_ns,
        is_buyer_maker=is_buyer_maker, trade_id=msg.get("a", msg.get("t")),
        receive_time_ns=receive_time_ns,
        source="binance_ws_aggTrade" if msg.get("e") == "aggTrade" else "binance_ws_trade",
        raw=msg,
    )


def normalize_book_ticker(msg: dict, *, instrument_id: str | None = None,
                          receive_time_ns: int | None = None) -> QuoteEvent:
    """Binance ``bookTicker`` message -> :class:`QuoteEvent`.

    Spot ``bookTicker`` carries no exchange timestamp, so ``event_time_ns`` falls
    back to ``receive_time_ns`` (futures ``bookTicker`` has ``E``/``T`` and is
    used when present).
    """
    symbol = msg.get("s")
    iid = _derive_instrument_id(symbol, instrument_id)
    bid_price = float(msg["b"])
    ask_price = float(msg["a"])
    bid_size = float(msg["B"]) if msg.get("B") is not None else None
    ask_size = float(msg["A"]) if msg.get("A") is not None else None
    event_time_ns = _ms_to_ns(msg.get("E", msg.get("T")))
    if event_time_ns is None:
        event_time_ns = receive_time_ns if receive_time_ns is not None else 0
    return QuoteEvent(
        event_time_ns=event_time_ns, instrument_id=iid,
        bid_price=bid_price, ask_price=ask_price, bid_size=bid_size, ask_size=ask_size,
        update_id=msg.get("u"), receive_time_ns=receive_time_ns,
        source="binance_ws_bookTicker", raw=msg,
    )


def normalize_mark_price(msg: dict, *, instrument_id: str | None = None,
                         receive_time_ns: int | None = None) -> FundingRateEvent:
    """USD-M ``markPriceUpdate`` -> next-settlement funding observation.

    Binance publishes the currently applicable funding rate in ``r`` and its
    settlement timestamp in ``T``.  The orchestrator de-duplicates by that
    settlement timestamp and books it only once when the timestamp is reached.
    """
    symbol = msg.get("s")
    iid = _derive_instrument_id(symbol, instrument_id)
    settlement_ns = _ms_to_ns(msg.get("T", msg.get("E")))
    if settlement_ns is None:
        settlement_ns = receive_time_ns if receive_time_ns is not None else 0
    return FundingRateEvent(
        event_time_ns=settlement_ns,
        instrument_id=iid,
        funding_rate=float(msg["r"]),
        mark_price=float(msg["p"]),
        source="binance_ws_markPrice",
    )


_BOOK_TICKER_KEYS = {"b", "a", "B", "A"}


def normalize_message(msg: Any, *, instrument_id: str | None = None,
                      receive_time_ns: int | None = None):
    """Dispatch one raw message to the right normalizer.

    Returns a :class:`TradeEvent`, a :class:`QuoteEvent`, or ``None`` for an
    unrecognised message (control frames, subscription acks, etc.).
    """
    msg = _unwrap(msg)
    if not isinstance(msg, dict):
        return None
    etype = msg.get("e")
    if etype in {"aggTrade", "trade"}:
        return normalize_agg_trade(msg, instrument_id=instrument_id, receive_time_ns=receive_time_ns)
    if etype == "bookTicker" or (etype is None and _BOOK_TICKER_KEYS <= msg.keys()):
        return normalize_book_ticker(msg, instrument_id=instrument_id, receive_time_ns=receive_time_ns)
    if etype == "markPriceUpdate":
        return normalize_mark_price(msg, instrument_id=instrument_id, receive_time_ns=receive_time_ns)
    return None


class LiveNormalizer:
    """Stateless driver: turns a message source into a stream of events.

    The source yields either raw dicts or ``(msg, receive_time_ns)`` tuples
    (as :class:`data_engine.live.mock_source.MockMessageSource` does).
    Unrecognised messages are dropped.
    """

    def __init__(self, *, instrument_id: str | None = None) -> None:
        self._instrument_id = instrument_id

    def normalize(self, msg: Any, *, receive_time_ns: int | None = None):
        return normalize_message(msg, instrument_id=self._instrument_id,
                                 receive_time_ns=receive_time_ns)

    def stream(self, source: Iterable[Any]) -> Iterator[Any]:
        for item in source:
            if isinstance(item, tuple):
                msg, recv = item
            else:
                msg, recv = item, None
            ev = self.normalize(msg, receive_time_ns=recv)
            if ev is not None:
                yield ev
