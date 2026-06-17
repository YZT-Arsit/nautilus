"""Tests for the Binance public WS market-data source (Milestone 2).

Fully offline: a fake transport replaces the socket, a list-backed clock makes
timeouts deterministic.  No real Binance connection, no account/orders, no
Nautilus.
"""
from __future__ import annotations

import json

import pytest

from data_engine.events import QuoteEvent, TradeEvent
from data_engine.live.binance_ws_client import (
    BinancePublicWebSocketSource,
    LiveSmokeResult,
    LiveTransportClosed,
    LiveTransportTimeout,
    build_combined_stream_url,
)

_AGG = {"e": "aggTrade", "E": 1718323200500, "s": "BTCUSDT", "a": 111,
        "p": "65000.10", "q": "0.5", "f": 1, "l": 3, "T": 1718323200499, "m": True, "M": True}
_BOOK = {"u": 400900217, "s": "BTCUSDT", "b": "64999.90", "B": "1.2", "a": "65000.10", "A": "0.8"}


def _wrap(stream, data):
    return json.dumps({"stream": stream, "data": data})


class _FakeTransport:
    """Yields canned JSON frames; can simulate recv-timeout / stream-close."""

    def __init__(self, frames, *, timeout_after=None, close_after=None):
        self._frames = list(frames)
        self._i = 0
        self._timeout_after = timeout_after
        self._close_after = close_after
        self.closed = False

    def recv(self):
        if self._timeout_after is not None and self._i >= self._timeout_after:
            raise LiveTransportTimeout()
        if self._close_after is not None and self._i >= self._close_after:
            raise LiveTransportClosed("end")
        if self._i >= len(self._frames):
            raise LiveTransportClosed("exhausted")
        frame = self._frames[self._i]
        self._i += 1
        return frame

    def close(self):
        self.closed = True


class _ListClock:
    """Returns successive values, repeating the last forever (deterministic)."""

    def __init__(self, values):
        self._v = list(values)
        self._i = 0

    def __call__(self):
        v = self._v[min(self._i, len(self._v) - 1)]
        self._i += 1
        return v


def _source(frames, *, clock_values=(0,), factory_capture=None, **fake_kwargs):
    transport = _FakeTransport(frames, **fake_kwargs)
    if factory_capture is not None:
        factory_capture["transport"] = transport

    def factory(url, *, timeout_seconds):
        if factory_capture is not None:
            factory_capture["url"] = url
            factory_capture["timeout_seconds"] = timeout_seconds
        return transport

    src = BinancePublicWebSocketSource(
        "BTCUSDT", ["aggTrade", "bookTicker"],
        transport_factory=factory, clock=_ListClock(clock_values),
    )
    return src, transport


# --------------------------------------------------------------------------

def test_build_combined_stream_url():
    assert build_combined_stream_url("BTCUSDT", ["aggTrade", "bookTicker"]) == (
        "wss://stream.binance.com:9443/stream?streams=btcusdt@aggTrade/btcusdt@bookTicker"
    )
    # string form parses the same way
    assert build_combined_stream_url("BTCUSDT", "aggTrade,bookTicker").endswith(
        "streams=btcusdt@aggTrade/btcusdt@bookTicker")
    with pytest.raises(ValueError):
        build_combined_stream_url("BTCUSDT", [])


def test_run_until_normalizes_and_counts():
    frames = [_wrap("btcusdt@aggTrade", _AGG),
              json.dumps({"result": None, "id": 1}),   # ack -> dropped
              _wrap("btcusdt@bookTicker", _BOOK)]
    cap = {}
    src, transport = _source(frames, factory_capture=cap)
    res = src.run_until(max_messages=3, timeout_seconds=100)
    assert isinstance(res, LiveSmokeResult)
    assert res.raw_received == 3
    assert res.trade_count == 1 and res.quote_count == 1 and res.dropped_count == 1
    assert isinstance(res.first_trade, TradeEvent) and isinstance(res.first_quote, QuoteEvent)
    assert res.first_trade.side == "SELL" and res.first_quote.update_id == 400900217
    assert res.disconnect_reason == "max_messages"
    assert transport.closed is True                    # clean disconnect
    assert cap["url"].endswith("streams=btcusdt@aggTrade/btcusdt@bookTicker")


def test_max_messages_cutoff_stops_early():
    frames = [_wrap("btcusdt@aggTrade", _AGG)] * 10
    src, transport = _source(frames)
    res = src.run_until(max_messages=4, timeout_seconds=100)
    assert res.raw_received == 4 and res.trade_count == 4
    assert res.disconnect_reason == "max_messages"
    assert transport.closed is True


def test_timeout_cutoff_immediate():
    # clock step huge vs timeout -> first deadline check trips immediately
    src, transport = _source([_wrap("btcusdt@aggTrade", _AGG)],
                             clock_values=[0, 0, 2_000_000_000])  # 2s >> 1s timeout
    res = src.run_until(max_messages=10, timeout_seconds=1)
    assert res.disconnect_reason == "timeout"
    assert res.raw_received == 0
    assert transport.closed is True


def test_transport_recv_timeout_then_deadline():
    # recv raises LiveTransportTimeout once, then the clock crosses the deadline
    src, transport = _source([], timeout_after=0,
                             clock_values=[0, 0, 0, 0, 2_000_000_000])
    res = src.run_until(max_messages=10, timeout_seconds=1)
    assert res.disconnect_reason == "timeout"
    assert res.raw_received == 0
    assert transport.closed is True


def test_stream_closed_reason():
    frames = [_wrap("btcusdt@aggTrade", _AGG)]
    src, transport = _source(frames, close_after=1)
    res = src.run_until(max_messages=10, timeout_seconds=100)
    assert res.raw_received == 1 and res.trade_count == 1
    assert res.disconnect_reason == "stream_closed"
    assert transport.closed is True


def test_iter_messages_yields_raw_dicts():
    frames = [_wrap("btcusdt@aggTrade", _AGG), _wrap("btcusdt@bookTicker", _BOOK)]
    src, _ = _source(frames)
    msgs = list(src.iter_messages(max_messages=2, timeout_seconds=100))
    assert [m["stream"] for m in msgs] == ["btcusdt@aggTrade", "btcusdt@bookTicker"]


def test_no_account_order_or_nautilus_in_client():
    import inspect

    from data_engine.live import binance_ws_client

    src = inspect.getsource(binance_ws_client)
    assert "import nautilus_trader" not in src
    assert "from nautilus_trader" not in src
    for forbidden in ("api_key", "apiKey", "secret", "signature", "place_order",
                      "new_order", "cancel_order", "/api/v3/order", "/sapi/"):
        assert forbidden not in src, f"unexpected trading reference: {forbidden}"
