# Binance Live Market Data Adapter

A self-owned live market-data adapter for Binance, parallel to the historical
Binance Vision pipeline. It lives in `data_engine/live/` and imports **no**
`nautilus_trader`. Nautilus (if used at all) stays strictly downstream for live
execution — never for data ingestion or feature computation.

**Status — Milestone 1: normalization only.** Raw Binance WebSocket messages are
normalized into our **own** canonical events, fed by an injectable **mock**
source. There is **no** network, no account, no orders, and no async WS client
yet — those are later milestones behind the same seam.

```
raw Binance WS JSON (aggTrade / bookTicker dict)
  -> data_engine/live/binance_ws.py  (normalize_message)
  -> TradeEvent / QuoteEvent          (data_engine.events — same model as historical)
  -> [later] data_engine live source -> feature_engine online update -> Nautilus live exec
```

## Why reuse `TradeEvent` / `QuoteEvent`

The live normalizer emits the **same** `TradeEvent` the historical Binance Vision
loader produces (plus a new `QuoteEvent`). One canonical event model across live
and historical means a live feed and the local historical cache are directly
comparable — exactly what's needed to validate a live adapter against a recorded
sample (replay parity).

## Mapping

### `aggTrade` -> `TradeEvent`
| WS field | event field | note |
| --- | --- | --- |
| `p` | `price` | float |
| `q` | `quantity` | float |
| (derived) | `quote_quantity` | `price*quantity` |
| `m` | `side` | `is_buyer_maker` → `m=True` ⇒ SELL, `False` ⇒ BUY |
| `a` | `trade_id` | aggregate trade id |
| `T` (ms) | `event_time_ns` | trade time; falls back to `E`, then `receive_time_ns` |
| `s` | `instrument_id` | `"{symbol}.BINANCE"` (overridable) |
| — | `source` | `"binance_ws_aggTrade"` |

### `bookTicker` -> `QuoteEvent`
| WS field | event field | note |
| --- | --- | --- |
| `b` / `B` | `bid_price` / `bid_size` | |
| `a` / `A` | `ask_price` / `ask_size` | |
| `u` | `update_id` | order-book update id |
| `E`/`T` (ms) | `event_time_ns` | spot has none → falls back to `receive_time_ns` |
| `s` | `instrument_id` | `"{symbol}.BINANCE"` (overridable) |
| — | `source` | `"binance_ws_bookTicker"` |

`QuoteEvent` also exposes `mid_price` and `spread`. Spot `bookTicker` carries no
exchange timestamp, so the adapter stamps `event_time_ns` from the local receive
time supplied by the source.

## Dispatch

`normalize_message(msg, *, instrument_id=None, receive_time_ns=None)`:
- unwraps a combined-stream envelope `{"stream": ..., "data": {...}}`,
- `e == "aggTrade"` → `TradeEvent`,
- `e == "bookTicker"` **or** raw spot bookTicker (no `e`, has `b/a/B/A`) → `QuoteEvent`,
- anything else (subscription acks, `kline`, non-dicts) → `None`.

## Components (`data_engine/live/`)

| file | responsibility |
| --- | --- |
| `binance_ws.py` | `normalize_agg_trade` / `normalize_book_ticker` / `normalize_message`; `LiveNormalizer` (source → event stream) |
| `mock_source.py` | `MockMessageSource` — offline canned feed yielding `(msg, receive_time_ns)`; the injectable seam a real WS transport will replace |

## Tests

`nautilus_ext/tests/test_live_binance_adapter.py` — pure stdlib, offline: field
mapping, side derivation, event-time fallbacks, spot vs futures bookTicker,
combined-stream unwrap, unknown-message dropping, mock-source streaming, and a
scan asserting **no** `nautilus_trader` and **no** network import (`websocket(s)`,
`asyncio`, `urllib`, `aiohttp`) in the milestone-1 modules.

## Milestone 2 — public market-data WebSocket source (bounded, read-only)

`data_engine/live/binance_ws_client.py` adds `BinancePublicWebSocketSource`: a
minimal reader over Binance's **public** combined stream
(`wss://stream.binance.com:9443/stream?streams=btcusdt@aggTrade/btcusdt@bookTicker`).
It is **market-data only** — no API key, signature, account or order endpoint,
and no `nautilus_trader`.

- **Reuses the normalizer.** Every raw frame goes through the same
  `LiveNormalizer` proven equivalent to the historical path (replay parity), so
  live messages become the same `TradeEvent`/`QuoteEvent` model. Unknown/ack
  frames still normalize to `None` and are counted as dropped.
- **Bounded + clean disconnect.** `run_until(max_messages, timeout_seconds)`
  (and `iter_messages(...)`) stop at the message cap **or** the wall-clock
  timeout, whichever first; the transport is always `close()`d in a `finally`.
  `disconnect_reason ∈ {max_messages, timeout, stream_closed}`.
- **Injectable transport.** The socket is a `transport_factory` seam; the default
  lazily imports `websocket-client` (the only network import, confined to this
  module) and raises a clear, **gated** error if it is not installed. Unit tests
  inject a fake transport + a deterministic clock, so they run fully offline.

Smoke script (real network — run only when explicitly approved):

```
python scripts/run_binance_live_smoke.py --symbol BTCUSDT \
  --streams aggTrade,bookTicker --max-messages 20 --timeout-seconds 20
```
prints connected stream, raw/normalized/trade/quote/dropped counts, first
TradeEvent/QuoteEvent summaries, disconnect reason and elapsed seconds.

Tests: `nautilus_ext/tests/test_live_binance_ws_client.py` — URL construction,
max-messages cutoff, timeout cutoff (immediate + recv-timeout-then-deadline),
stream-closed, raw→normalized counts, dropped unknown, clean close, and a scan
asserting no account/order/trading reference and no `nautilus_trader` import.

## Not yet built

A `data_engine` live source feeding a `feature_engine` online-update loop, any
account/order flow, and any Nautilus live-execution wiring. Each is a separate,
later milestone.
