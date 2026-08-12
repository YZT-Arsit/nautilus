"""Construct :class:`TradeEvent` objects from raw values.

``make_trade_event`` explicitly marks whether ``quote_quantity`` was supplied
or had to fall back to ``price * quantity`` and derives ``side`` from
``is_buyer_maker`` when not supplied. ``make_trades`` builds an evenly spaced
list of trades — the synthetic/demo helper.

Side convention (Binance): ``is_buyer_maker=True`` -> aggressive ``SELL``;
``is_buyer_maker=False`` -> aggressive ``BUY``.
"""

from __future__ import annotations

from data_engine.events import TradeEvent
from data_engine.time import ONE_SECOND_NS


BUY, SELL = "BUY", "SELL"


def side_from_is_buyer_maker(is_buyer_maker: bool | None) -> str | None:
    """Map ``is_buyer_maker`` to aggressor side (None when unknown)."""
    if is_buyer_maker is None:
        return None
    return SELL if is_buyer_maker else BUY


def make_trade_event(
    *,
    price: float,
    quantity: float,
    instrument_id: str,
    event_time_ns: int,
    quote_quantity: float | None = None,
    quote_quantity_source: str | None = None,
    side: str | None = None,
    is_buyer_maker: bool | None = None,
    trade_id: int | str | None = None,
    receive_time_ns: int | None = None,
    source: str | None = None,
    raw: dict | None = None,
) -> TradeEvent:
    """Build one :class:`TradeEvent`, filling notional and side when omitted."""
    if quote_quantity is None:
        quote_quantity = price * quantity
        quote_quantity_source = "price_x_quantity_fallback"
    elif quote_quantity_source is None:
        quote_quantity_source = "provided"
    if side is None:
        side = side_from_is_buyer_maker(is_buyer_maker)
    return TradeEvent(
        event_time_ns=event_time_ns,
        instrument_id=instrument_id,
        price=price,
        quantity=quantity,
        quote_quantity=quote_quantity,
        quote_quantity_source=quote_quantity_source,
        side=side,
        is_buyer_maker=is_buyer_maker,
        trade_id=trade_id,
        receive_time_ns=receive_time_ns,
        source=source,
        raw=raw,
    )


def make_trades(
    prices: list[float],
    quantities: list[float] | None = None,
    *,
    sides: list[str] | None = None,
    instrument_id: str = "BTC/USDT",
    start_ns: int = 0,
    step_ns: int = ONE_SECOND_NS,
    source: str | None = "synthetic",
) -> list[TradeEvent]:
    """Build a list of evenly spaced trades from prices (+ optional sizes/sides)."""
    if quantities is None:
        quantities = [1.0] * len(prices)
    out: list[TradeEvent] = []
    for i, price in enumerate(prices):
        side = sides[i] if sides is not None else None
        is_maker = None if side is None else (side == SELL)
        out.append(
            make_trade_event(
                price=price,
                quantity=quantities[i],
                instrument_id=instrument_id,
                event_time_ns=start_ns + i * step_ns,
                side=side,
                is_buyer_maker=is_maker,
                trade_id=i,
                source=source,
            )
        )
    return out
