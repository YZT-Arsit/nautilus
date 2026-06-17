"""Minimal Binance **public** market-data WebSocket source (Milestone 2).

Market-data ONLY: it connects to Binance's public combined stream, reads a
*bounded* number of messages (by count and wall-clock timeout), normalizes each
via the existing :class:`~data_engine.live.binance_ws.LiveNormalizer`, and
disconnects cleanly.

It carries **no** credentials, no request signing, no account/order endpoint, and
no trading call — and no ``nautilus_trader``.  The actual socket is an **injectable
transport** (``transport_factory``); the default lazily imports a WebSocket
client only when a real connection is opened, so unit tests run fully offline
with a fake transport.

Bounds (all mandatory): ``max_messages`` and ``timeout_seconds``; the transport
is always ``close()``d in a ``finally`` (clean disconnect).
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator

from data_engine.live.binance_ws import LiveNormalizer

_PUBLIC_BASE_URL = "wss://stream.binance.com:9443"


class LiveTransportTimeout(Exception):
    """Raised by a transport's ``recv()`` when no message arrived in time."""


class LiveTransportClosed(Exception):
    """Raised by a transport's ``recv()`` when the stream has closed."""


def build_combined_stream_url(symbol: str, streams, *, base_url: str = _PUBLIC_BASE_URL) -> str:
    """Binance **combined** stream URL, e.g.
    ``wss://stream.binance.com:9443/stream?streams=btcusdt@aggTrade/btcusdt@bookTicker``.

    Combined-stream frames are ``{"stream": ..., "data": {...}}`` envelopes, which
    ``LiveNormalizer`` already unwraps.
    """
    if isinstance(streams, str):
        streams = [s.strip() for s in streams.split(",") if s.strip()]
    if not streams:
        raise ValueError("at least one stream is required (e.g. aggTrade, bookTicker)")
    sym = symbol.lower()
    parts = "/".join(f"{sym}@{s}" for s in streams)
    return f"{base_url}/stream?streams={parts}"


class _WebsocketClientTransport:
    """Adapts the synchronous ``websocket-client`` connection to our interface."""

    def __init__(self, ws) -> None:
        self._ws = ws

    def recv(self) -> str:
        try:
            return self._ws.recv()
        except Exception as exc:  # map library errors to our transport contract
            if "timeout" in type(exc).__name__.lower():
                raise LiveTransportTimeout() from exc
            raise LiveTransportClosed(str(exc)) from exc

    def close(self) -> None:
        try:
            self._ws.close()
        except Exception:
            pass


def default_transport_factory(url: str, *, timeout_seconds: float):
    """Open a real public WS connection (lazy import; market-data only).

    Raises a clear, actionable error if the optional ``websocket-client`` package
    is not installed — installation is **gated**, never automatic.
    """
    try:
        from websocket import create_connection  # websocket-client; lazy on purpose
    except ImportError as exc:
        raise RuntimeError(
            "real WebSocket smoke needs the 'websocket-client' package (module "
            "'websocket'); it is not installed. Installation is gated — do not "
            "auto-install."
        ) from exc
    ws = create_connection(url, timeout=timeout_seconds)
    return _WebsocketClientTransport(ws)


@dataclass
class LiveSmokeResult:
    connected_url: str
    raw_received: int = 0
    events: list = field(default_factory=list)
    trade_count: int = 0
    quote_count: int = 0
    dropped_count: int = 0
    first_trade: Any = None
    first_quote: Any = None
    disconnect_reason: str = ""
    elapsed_seconds: float = 0.0


class BinancePublicWebSocketSource:
    """Bounded reader over a Binance public combined market-data stream."""

    def __init__(
        self,
        symbol: str,
        streams,
        *,
        base_url: str = _PUBLIC_BASE_URL,
        url: str | None = None,
        transport_factory: Callable[..., Any] | None = None,
        instrument_id: str | None = None,
        clock: Callable[[], int] | None = None,
    ) -> None:
        self._symbol = symbol
        self._url = url or build_combined_stream_url(symbol, streams, base_url=base_url)
        self._transport_factory = transport_factory or default_transport_factory
        self._normalizer = LiveNormalizer(instrument_id=instrument_id)
        self._clock = clock or time.time_ns

    @property
    def url(self) -> str:
        return self._url

    def _recv_loop(self, *, max_messages: int, timeout_seconds: float):
        """Yield raw message strings, bounded by count + timeout; always closes.

        Returns (via ``StopIteration.value``) the disconnect reason.
        """
        start = self._clock()
        deadline = start + int(timeout_seconds * 1_000_000_000)
        transport = self._transport_factory(self._url, timeout_seconds=timeout_seconds)
        reason = "max_messages"
        raw = 0
        try:
            while raw < max_messages:
                if self._clock() >= deadline:
                    reason = "timeout"
                    return reason
                try:
                    msg = transport.recv()
                except LiveTransportTimeout:
                    if self._clock() >= deadline:
                        reason = "timeout"
                        return reason
                    continue
                except LiveTransportClosed:
                    reason = "stream_closed"
                    return reason
                if not msg:
                    continue
                raw += 1
                yield msg
            return "max_messages"
        finally:
            transport.close()

    def iter_messages(self, *, max_messages: int, timeout_seconds: float) -> Iterator[dict]:
        """Yield raw parsed message dicts (bounded)."""
        gen = self._recv_loop(max_messages=max_messages, timeout_seconds=timeout_seconds)
        for raw in gen:
            yield json.loads(raw)

    def run_until(self, *, max_messages: int, timeout_seconds: float) -> LiveSmokeResult:
        """Connect, read up to ``max_messages`` (or until ``timeout_seconds``),
        normalize each, and disconnect.  Returns a :class:`LiveSmokeResult`."""
        result = LiveSmokeResult(connected_url=self._url)
        start = self._clock()
        gen = self._recv_loop(max_messages=max_messages, timeout_seconds=timeout_seconds)
        reason = "max_messages"
        try:
            while True:
                try:
                    raw = next(gen)
                except StopIteration as stop:
                    reason = stop.value or reason
                    break
                result.raw_received += 1
                msg = json.loads(raw)
                ev = self._normalizer.normalize(msg, receive_time_ns=self._clock())
                if ev is None:
                    result.dropped_count += 1
                    continue
                result.events.append(ev)
                if ev.event_type == "trade":
                    result.trade_count += 1
                    if result.first_trade is None:
                        result.first_trade = ev
                elif ev.event_type == "quote":
                    result.quote_count += 1
                    if result.first_quote is None:
                        result.first_quote = ev
        finally:
            gen.close()
        result.disconnect_reason = reason
        result.elapsed_seconds = (self._clock() - start) / 1_000_000_000
        return result
