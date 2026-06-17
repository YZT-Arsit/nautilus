# Trade (aggTrades) data pipeline

This extends the framework beyond bars/klines to **trade (tick) events**, end to
end through our **self-owned** data and feature modules. It mirrors the proven
bar pipeline.

```
Binance Vision aggTrades ZIP/CSV
  -> binance_vision adapter (build_binance_vision_aggtrades_url / read / normalize)
  -> StandardTrade schema
  -> Hive Parquet
  -> data_engine.load_events  (mode: hive_parquet_trades)
  -> TradeEvent
  -> feature_engine (FeatureSpec / BackendRegistry / PythonBackend)
  -> trade feature_lib
  -> strategy / signal layer
  -> SignalToOrderPolicy
  -> Nautilus backend (backtest / live execution)
```

**Independence (important):** data ingestion, normalization, parquet read, and
all feature computation are done by our own modules. `data_engine`,
`feature_engine`, and `feature_lib` import **no** `nautilus_trader`. Nautilus is
used **only downstream** for backtest/live execution (matching, fills, order
lifecycle, account/position) — never for data ingestion or feature computation.

## TradeEvent (`data_engine/events.py`)

| field | type | note |
| --- | --- | --- |
| event_time_ns | int | exchange/source timestamp (ns) |
| instrument_id | str | e.g. `BTCUSDT.BINANCE` |
| price | float | |
| quantity | float | base-asset size |
| quote_quantity | float | notional; `price*quantity` when absent |
| side | str | `BUY` / `SELL` (aggressor) |
| is_buyer_maker | bool | Binance flag (True ⇒ aggressive SELL) |
| trade_id | int/str | aggTrade id |
| receive_time_ns | int | optional local receipt time |
| source | str | provenance |
| raw | dict | optional original row |
| event_type | str | `"trade"` (routes to input_type `trade`) |

Side convention: `is_buyer_maker=True` ⇒ the buyer was the resting maker, so the
trade was an aggressive **SELL**; `False` ⇒ aggressive **BUY**.

## StandardTrade schema (Binance Vision aggTrades)

`ts` (Datetime µs), `exchange`, `venue_type`, `symbol`, `instrument_id`,
`agg_trade_id`, `price`, `quantity`, `quote_quantity`, `first_trade_id`,
`last_trade_id`, `is_buyer_maker`, `side`, `source="binance_vision_aggTrades"`,
`ingested_at`.

Binance aggTrades CSV columns (no guaranteed header):
`aggTradeId, price, quantity, firstTradeId, lastTradeId, transactTime(ms), isBuyerMaker[, isBestMatch]`
(spot carries the trailing `isBestMatch`; futures omit it). A header row, if
present, is detected and skipped.

aggTrades is **archive-backed** on Binance Vision (`/data/spot/daily/aggTrades/...`).
Depth / L2 order-book snapshots are **not** in the Binance Vision archive and are
out of scope for this stage.

## Hive Parquet layout (trades)

Trade data has no `bar_type`; it partitions by `data_type` instead, so it never
collides with the `bar_type=5m` kline directories:

```
historical_data/market_data/
  exchange=BINANCE/venue_type=spot/symbol=BTCUSDT/data_type=aggTrades/date=2024-06-01/part-0.parquet
```

## Ingest CLI

`scripts/ingest_binance_vision.py` gains `--data-type klines|aggTrades`
(default `klines`, so existing kline commands are unchanged). `--interval` is
only required for klines.

```
python scripts/ingest_binance_vision.py \
  --data-type aggTrades --market spot --symbol BTCUSDT --frequency daily \
  --start 2024-06-01 --end 2024-06-01 --output historical_data/market_data --overwrite
```

## data_engine modes

`data_engine/loader.py` adds `synthetic_trades`, `parquet_trades`, and
`hive_parquet_trades` (alias). The bar modes are unchanged. A trade config:

```yaml
data:
  mode: hive_parquet_trades
  root: historical_data/market_data
  instrument_id: BTCUSDT.BINANCE
  timestamp_column: ts
  timestamp_unit: ns
  filters: {exchange: BINANCE, venue_type: spot, symbol: BTCUSDT, data_type: aggTrades}
```

## Trade features (`feature_engine/compute/feature_lib/trade.py`)

All pure Python, incremental, `not_ready`/missing-field/divide-by-zero safe,
`input_type="trade"`. Two windowing styles: count-window (last N trades) and
time-window (last `window` `window_unit`, default seconds).

| type | builder | formula |
| --- | --- | --- |
| `trade_count` | `trade_count_spec` | trades in the trailing time window |
| `trade_volume_sum` | `trade_volume_sum_spec` | rolling `sum(quantity)` over N trades |
| `trade_quote_volume_sum` | `trade_quote_volume_sum_spec` | rolling `sum(quote_quantity)` |
| `avg_trade_size` | `avg_trade_size_spec` | rolling `mean(quantity)` |
| `signed_trade_volume` | `signed_trade_volume_spec` | rolling `sum(+qty BUY, -qty SELL)` |
| `trade_imbalance` | `trade_imbalance_spec` | `(buy_vol-sell_vol)/max(buy_vol+sell_vol, eps)` |
| `trade_vwap` | `trade_vwap_spec` | `sum(price*qty)/sum(qty)` over N trades |
| `large_trade_ratio` | `large_trade_ratio_spec` | fraction of N trades with `qty >= threshold` |
| `trade_intensity` | `trade_intensity_spec` | `trade_count / window_seconds` |

`trade_count` / `trade_intensity` use a time window (`TimeWindowState`); the rest
use a count window of the last N trades (`RollingWindowState` / `VWAPState`).

## Tests

- `nautilus_ext/tests/test_trade_events.py` — TradeEvent + adapter, synthetic
  trades, Hive-parquet trades read (datetime ts → ns, filters, first/last).
- `nautilus_ext/tests/test_feature_lib_trade.py` — 9 features (exact value,
  warmup, missing-field, divide-by-zero), backend exposure, builder buildability,
  input_type routing, state_dict round-trip, no-nautilus source scan.
- `nautilus_ext/tests/test_binance_vision_aggtrades.py` — URL, mock-ZIP reader
  (header skip + 7-field futures variant), StandardTrade normalization.
