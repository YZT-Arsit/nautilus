# data_engine — data processing layer

The formal, standalone data layer. **Our own design — it does not depend on
Nautilus Trader's native data system** (Nautilus integration is out of scope).

## Framework-agnostic contract (enforced)

`data_engine` and `feature_engine` **must never import `nautilus_trader`** — both
packages are portable and can be lifted out to plug into any framework. All
Nautilus integration lives in `strategy_framework` (the Nautilus layer); the
dependency direction is one-way: `strategy_framework → data_engine`, never the
reverse. Any Nautilus↔neutral bridge (e.g. reading a Nautilus `ParquetDataCatalog`
into neutral ticks) lives at `strategy_framework/nautilus_catalog.py`, not here.
This is guarded by `tests_platform/test_decoupling.py` (asserts importing the two
layers loads zero `nautilus_trader` modules).

Importable directly:

```python
from data_engine import BarEvent, load_events, make_bar_event, make_bars
```

## Modules

| Module | Responsibility |
|--------|----------------|
| `events.py` | `BarEvent` dataclass (OHLCV + `instrument_id` + `event_time_ns` + `event_type`) |
| `time.py` | `ONE_SECOND_NS`, `to_event_time_ns(value, unit)` for ns/us/ms/s |
| `validation.py` | `require_numeric`, `optional_numeric` (no pandas) |
| `schema.py` | bar field-name constants |
| `split.py` | Warmup/live split for non-windowed sources |
| `adapters/bar_adapter.py` | `make_bar_event(...)`, `make_bars(...)` |
| `streams/base.py` | `EventSource` protocol (`warmup()` / `stream()`) |
| `sources/synthetic.py` | `SyntheticBarSource` + `load_synthetic_bars` |
| `sources/csv_bars.py` | `CsvBarSource` + `load_csv_bars` (stdlib `csv`) |
| `sources/parquet_bars.py` | `ParquetBarSource` + `load_parquet_bars` (Hive Parquet via `pyarrow`) |
| `sources/live_synthetic.py` | `LiveSyntheticBarSource` + `load_live_synthetic` (generator) |
| `loader.py` | `load_events(data_config)` — canonical entry, dispatches by `data.mode` |

## Data modes (`data.mode` in a strategy config)

| Mode | Source | Live events |
|------|--------|-------------|
| `synthetic` | generated flat→rise→fall demo path | list |
| `csv_bars` | historical replay from a local CSV (small demos/tests) | list |
| `hive_parquet_bars` | bars from the locked `market_data` Hive dataset | list |
| `hive_parquet_trades` | one normalized raw trade per row from the locked Hive dataset → `TradeEvent` | list |
| `hive_parquet_funding` | perpetual funding settlements → `FundingRateEvent` | list |
| `live_synthetic` | streaming skeleton (no real feed) | generator |
| `live_gateway` | CTP-like gateway skeleton (`provider: mock` by default) | generator |
| `binance_ws` | **live Binance public market-data WS** (aggTrade→`TradeEvent`, bookTicker→`QuoteEvent`); bounded by `max_messages`+`timeout_seconds` | generator |

### `binance_ws` (live Binance market data)

Yields the **same neutral `TradeEvent`/`QuoteEvent`** as the historical Binance
sources (live/historical parity). Network opens lazily on first iteration; the
optional `websocket-client` dependency is imported only on connect. Example:

```yaml
data:
  mode: binance_ws
  symbol: btcusdt                 # or derived from instrument_id
  instrument_id: BTCUSDT.BINANCE  # optional override
  streams: aggTrade,bookTicker
  base_url: wss://data-stream.binance.vision:9443   # default (market-data-only mirror)
  max_messages: 100               # bound
  timeout_seconds: 30             # bound
```

`base_url` defaults to Binance's **market-data-only** mirror
`data-stream.binance.vision` (same origin as the Vision historical data) because
the primary `stream.binance.com` host is unreachable from some networks (e.g. the
project server, where it is TCP-blocked while the mirror is reachable).

All historical sources sort by event time once. For a bounded Hive query,
`warmup_bars` is taken from partitions immediately before `start`; the live
stream still begins at `start`, so warmup never consumes the requested backtest
window. The locked Parquet schema requires the configured timestamp column.

- **`csv_bars`** — for small demo/test files (stdlib `csv`, no extra deps).
- **`hive_parquet_bars`** — the only historical bar reader. It requires the
  locked partition selector
  `asset_class/exchange/venue_type/symbol/data_type/freq` and optionally `date`.
  It reads with `pyarrow.dataset` using
  **partition pruning** (simple equality `filters`) and **column pushdown**
  (only needed bar columns are read). Example config:

  ```yaml
  data:
    mode: hive_parquet_bars
    root: historical_data/market_data
    instrument_id: BTCUSDT.BINANCE
    warmup_bars: 20
    filters:
      asset_class: crypto
      exchange: BINANCE
      venue_type: futures_um
      symbol: BTCUSDT
      data_type: bar
      freq: 1m
    start: "2024-07-01"
    end: "2024-07-03"
    timestamp_column: ts
    timestamp_unit: ns
  ```

  `pyarrow` is the only added dependency; **no pandas**. The source returns plain
  `BarEvent` objects.

### Trade-tick contract

In this project a tick means one exchange raw trade, not an aggTrade, quote,
order-book update, synthetic observation, or one-second bar. Binance USD-M raw
trade rows are normalized one-to-one to `TradeEvent`, ordered by
`(event_time_ns, trade_id)`, and stored in the same locked layout with
`data_type=trade/freq=tick`. Source `quoteQty` is retained as
`quote_quantity`; `price * quantity` is only an explicitly marked fallback for
sources that genuinely omit quote notional.

Trade features and strategies may consume this irregular stream directly via
`hive_parquet_trades`. Clock-time windows use event timestamps and represent
`(t - window, t]`; they do not first collapse events to bars. Execution lag is
not a data-layer property: `DurationLagTargetAdapter(lag_ns=...)` uses physical
time and fills at the first following trade reaching the due time. Its
`lag_ns=0` means the next observed trade, never an instantaneous fill on the
signal-producing trade.

## Adding a real live source later

Implement `data_engine.streams.base.EventSource` (a `warmup()` + a
`stream()`), add a `load_<mode>` and register it in `loader.py`'s dispatch, then
reference the new `data.mode` from a config. No network/exchange dependency is
present today.

## Layering

`data_engine` (data) → `feature_engine/compute` (features) →
`strategy_framework` (orchestration) → `strategies/<name>` (logic).
`strategy_framework/data_loaders.py` is a **compatibility wrapper** that
re-exports this package; it is not the canonical implementation.
