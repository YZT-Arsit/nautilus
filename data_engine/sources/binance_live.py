"""Loader adapter for the Binance public market-data WebSocket source.

Wires :class:`data_engine.live.binance_ws_client.BinancePublicWebSocketSource`
into the canonical ``load_events`` dispatch as ``data.mode: binance_ws``.

Framework-agnostic: this stays inside ``data_engine`` and imports no
``nautilus_trader`` — it yields the same neutral :class:`TradeEvent` /
:class:`QuoteEvent` objects the historical Binance sources produce, so live and
historical normalize into one event model.

Network is opened **lazily** only when the returned ``live`` iterator is
consumed (the WS transport, and the optional ``websocket-client`` dependency, are
imported on first connect — never at import time).

Config (``data:`` section)::

    data:
      mode: binance_ws
      symbol: btcusdt                # or derived from instrument_id
      instrument_id: BTCUSDT.BINANCE # optional; overrides the derived id
      streams: aggTrade,bookTicker   # comma-separated Binance stream names
      base_url: wss://data-stream.binance.vision:9443   # default (see below)
      max_messages: 100              # bound: stop after N messages
      timeout_seconds: 30            # bound: stop after T wall-clock seconds

``base_url`` defaults to Binance's **market-data-only** mirror
``data-stream.binance.vision`` (same origin as the Vision historical data)
because the primary ``stream.binance.com`` host is unreachable from some
networks (e.g. the project's server); override it for other environments.
"""
from __future__ import annotations

from typing import Any, Iterable

# Market-data-only mirror; reachable where stream.binance.com is geo-blocked.
_DEFAULT_BASE_URL = "wss://data-stream.binance.vision:9443"


def _derive_symbol(data_config: dict[str, Any]) -> str:
    symbol = data_config.get("symbol")
    if symbol:
        return str(symbol)
    instrument_id = data_config.get("instrument_id")
    if instrument_id:
        # "BTCUSDT.BINANCE" -> "btcusdt"
        return str(instrument_id).split(".", 1)[0].lower()
    raise ValueError("binance_ws mode requires 'symbol' or 'instrument_id'")


def load_binance_ws(data_config: dict[str, Any]) -> tuple[list[Any], Iterable[Any]]:
    """Build a bounded live Binance WS stream; return ``(warmup, live)``.

    ``warmup`` is always empty (a WS feed carries no history — pair with a
    ``parquet_bars``/``parquet_trades`` warmup separately when needed). ``live``
    is a lazy generator of normalized events; connecting happens on first
    iteration.
    """
    # Local import keeps loader import cheap and network-free; the WS client
    # itself only imports the socket library on actual connect.
    from data_engine.live.binance_ws_client import BinancePublicWebSocketSource

    symbol = _derive_symbol(data_config)
    streams = data_config.get("streams", "aggTrade,bookTicker")
    base_url = str(data_config.get("base_url", _DEFAULT_BASE_URL))
    instrument_id = data_config.get("instrument_id")
    max_messages = int(data_config.get("max_messages", 100))
    timeout_seconds = float(data_config.get("timeout_seconds", 30.0))

    source = BinancePublicWebSocketSource(
        symbol, streams, base_url=base_url, instrument_id=instrument_id,
    )
    live = source.iter_events(max_messages=max_messages, timeout_seconds=timeout_seconds)
    return [], live
