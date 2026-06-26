# Crypto Perpetual Metadata Adapter

Date: 2026-06-25

## Goal

E3 adds a small Binance USD-M perpetual metadata adapter on top of the E2
multi-symbol kline and VWM smoke. The adapter collects public read-only
exchange info, funding rate, and mark/index price metadata for BTCUSDT and
ETHUSDT.

This metadata is collected and validated, but it is not yet applied to VWM PnL,
funding-aware equity, margin, or liquidation logic.

## Public Data Sources

- Exchange info snapshot:
  `https://fapi.binance.com/fapi/v1/exchangeInfo`
- Funding rate archive:
  `https://data.binance.vision/data/futures/um/monthly/fundingRate/{SYMBOL}/{SYMBOL}-fundingRate-YYYY-MM.zip`
- Mark price archive:
  `https://data.binance.vision/data/futures/um/daily/markPriceKlines/{SYMBOL}/5m/{SYMBOL}-5m-YYYY-MM-DD.zip`
- Index price archive:
  `https://data.binance.vision/data/futures/um/daily/indexPriceKlines/{SYMBOL}/5m/{SYMBOL}-5m-YYYY-MM-DD.zip`

No credentials, signed requests, user streams, or trading endpoints are used.

## Canonical Schemas

`canonical_perpetual_instrument_metadata`

Required fields: `exchange`, `venue_type`, `symbol`, `instrument_id`,
`market_type`, `contract_type`, `base_asset`, `quote_asset`,
`settlement_asset`, `margin_asset`, `tick_size`, `lot_size`,
`price_precision`, `quantity_precision`, `min_qty`, `min_notional`, `status`,
`metadata_source`, `fetched_at`, `caveat`.

`canonical_funding_rate`

Required fields: `ts`, `exchange`, `venue_type`, `symbol`, `instrument_id`,
`funding_rate`, `funding_time`, `funding_interval_hours`, `source`,
`ingested_at`.

`canonical_mark_index_price`

Required fields: `ts`, `exchange`, `venue_type`, `symbol`, `instrument_id`,
`mark_price`, `index_price`, `estimated_settle_price`,
`last_funding_rate`, `next_funding_time`, `source`, `ingested_at`.

## Output Layout

Smoke root:

`outputs/derived_market_data/crypto_perpetual_metadata_smoke`

Per symbol:

- `exchange=BINANCE/venue_type=futures_um/symbol=BTCUSDT/metadata_type=exchange_info/snapshot.json`
- `exchange=BINANCE/venue_type=futures_um/symbol=BTCUSDT/metadata_type=funding_rate/date=2024-06-01/part-0.parquet`
- `exchange=BINANCE/venue_type=futures_um/symbol=BTCUSDT/metadata_type=mark_price/date=2024-06-01/part-0.parquet`

The same layout is used for ETHUSDT. If the requested smoke root already has
content, the CLI chooses a numeric suffix such as
`crypto_perpetual_metadata_smoke_2`.

## Validation Status

- `exchange_info`: REST endpoint audited, but the 2026-06-25 smoke was blocked
  by network timeout from the test environment.
- `funding_rate`: smoke_validated after one-day public archive rows exist.
- `mark/index`: smoke_validated after mark and index kline rows merge by
  timestamp into canonical rows.
- `OHLCV bars`: already smoke_validated in E2 via Binance Vision futures_um 5m
  bars for 2024-06-01.
- `VWM backtest`: already smoke_validated in E2, with funding/mark/index/margin
  caveat still active.

## Integration Status

Metadata is currently an adapter/registry layer. It does not change VWM
strategy math, the Nautilus execution path, feature_engine, or data_engine.

Next step: rerun exchange_info when the REST endpoint is reachable, then either
add a funding-aware evaluation path or use exchange_info as a metadata-backed
instrument mapping source while keeping deterministic fallback mapping for
smoke tests.
