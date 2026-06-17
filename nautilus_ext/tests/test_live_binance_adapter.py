"""Tests for the Binance live market-data adapter (milestone 1: normalization).

Pure stdlib — no network, no pyarrow, no Nautilus.  Raw Binance WS dicts are
normalized to our own TradeEvent / QuoteEvent, driven by an offline mock source.
"""
from __future__ import annotations

import pytest

from data_engine.events import QuoteEvent, TradeEvent
from data_engine.live import (
    LiveNormalizer,
    MockMessageSource,
    normalize_agg_trade,
    normalize_book_ticker,
    normalize_message,
)

_AGG = {"e": "aggTrade", "E": 1718323200500, "s": "BTCUSDT", "a": 111,
        "p": "65000.10", "q": "0.5", "f": 1, "l": 3, "T": 1718323200499, "m": True, "M": True}
_BOOK_SPOT = {"u": 400900217, "s": "BTCUSDT", "b": "64999.90", "B": "1.2",
              "a": "65000.10", "A": "0.8"}
_BOOK_FUT = {"e": "bookTicker", "u": 400, "E": 1718323200600, "T": 1718323200599,
             "s": "BTCUSDT", "b": "64999.0", "B": "2.0", "a": "65001.0", "A": "3.0"}


def test_agg_trade_to_trade_event():
    ev = normalize_agg_trade(_AGG)
    assert isinstance(ev, TradeEvent) and ev.event_type == "trade"
    assert ev.instrument_id == "BTCUSDT.BINANCE"
    assert ev.price == pytest.approx(65000.10) and ev.quantity == pytest.approx(0.5)
    assert ev.quote_quantity == pytest.approx(65000.10 * 0.5)
    assert ev.side == "SELL"                       # m=True -> aggressive SELL
    assert ev.trade_id == 111
    assert ev.event_time_ns == 1718323200499 * 1_000_000   # trade time T (ms->ns)
    assert ev.source == "binance_ws_aggTrade"


def test_agg_trade_side_derivation():
    assert normalize_agg_trade({**_AGG, "m": True}).side == "SELL"
    assert normalize_agg_trade({**_AGG, "m": False}).side == "BUY"


def test_agg_trade_event_time_fallbacks():
    no_t = {k: v for k, v in _AGG.items() if k != "T"}
    assert normalize_agg_trade(no_t).event_time_ns == 1718323200500 * 1_000_000   # E
    no_ts = {k: v for k, v in no_t.items() if k != "E"}
    assert normalize_agg_trade(no_ts, receive_time_ns=42).event_time_ns == 42     # receive time


def test_book_ticker_to_quote_event_spot():
    ev = normalize_book_ticker(_BOOK_SPOT, receive_time_ns=99)
    assert isinstance(ev, QuoteEvent) and ev.event_type == "quote"
    assert ev.instrument_id == "BTCUSDT.BINANCE"
    assert ev.bid_price == pytest.approx(64999.90) and ev.ask_price == pytest.approx(65000.10)
    assert ev.bid_size == pytest.approx(1.2) and ev.ask_size == pytest.approx(0.8)
    assert ev.update_id == 400900217
    assert ev.event_time_ns == 99                  # spot bookTicker has no ts -> receive time
    assert ev.mid_price == pytest.approx((64999.90 + 65000.10) / 2)
    assert ev.spread == pytest.approx(65000.10 - 64999.90)
    assert ev.source == "binance_ws_bookTicker"


def test_book_ticker_futures_uses_event_time():
    ev = normalize_book_ticker(_BOOK_FUT, receive_time_ns=1)
    assert ev.event_time_ns == 1718323200600 * 1_000_000   # E preferred over receive time


def test_dispatch_combined_stream_wrapper():
    agg = normalize_message({"stream": "btcusdt@aggTrade", "data": _AGG})
    assert isinstance(agg, TradeEvent)
    book = normalize_message({"stream": "btcusdt@bookTicker", "data": _BOOK_SPOT}, receive_time_ns=7)
    assert isinstance(book, QuoteEvent) and book.event_time_ns == 7


def test_dispatch_raw_spot_book_ticker_without_event_type():
    ev = normalize_message(_BOOK_SPOT, receive_time_ns=5)
    assert isinstance(ev, QuoteEvent)


def test_dispatch_unknown_returns_none():
    assert normalize_message({"result": None, "id": 1}) is None       # subscription ack
    assert normalize_message({"e": "kline", "s": "BTCUSDT"}) is None   # unsupported stream
    assert normalize_message("not-a-dict") is None
    assert normalize_message(123) is None


def test_instrument_id_override():
    ev = normalize_agg_trade(_AGG, instrument_id="BTC-PERP")
    assert ev.instrument_id == "BTC-PERP"


def test_mock_source_and_live_normalizer_stream():
    msgs = [_AGG, {"result": None, "id": 1}, _BOOK_SPOT, {"e": "kline"}, _BOOK_FUT]
    recv = [10, 11, 12, 13, 14]
    source = MockMessageSource(msgs, receive_times_ns=recv)
    assert len(source) == 5
    out = list(LiveNormalizer(instrument_id="BTCUSDT.BINANCE").stream(source))
    # 2 unknowns (ack, kline) dropped -> 3 events: trade, quote, quote
    assert [type(e).__name__ for e in out] == ["TradeEvent", "QuoteEvent", "QuoteEvent"]
    assert out[1].event_time_ns == 12              # spot quote stamped with receive time
    assert all(e.instrument_id == "BTCUSDT.BINANCE" for e in out)


def test_mock_source_length_mismatch_raises():
    with pytest.raises(ValueError):
        MockMessageSource([_AGG, _BOOK_SPOT], receive_times_ns=[1])


def test_no_nautilus_or_network_import():
    import inspect

    from data_engine.live import binance_ws, mock_source

    for mod in (binance_ws, mock_source):
        src = inspect.getsource(mod)
        assert "import nautilus_trader" not in src
        assert "from nautilus_trader" not in src
        # milestone 1 is offline: no socket/async client here
        for forbidden in ("import websocket", "import websockets", "import asyncio",
                          "from urllib", "import urllib", "import aiohttp"):
            assert forbidden not in src, f"unexpected network import: {forbidden}"
