# Data Type Support Matrix

D1a defines a declarative adapter layer for VWM batch research. VWM still
consumes canonical bars only. Raw market data must first pass through an
adapter or converter, while contract definitions feed instrument metadata.
This is the lower-layer raw data adapter matrix, not the market coverage
matrix; market type / asset class coverage is tracked separately in
`docs/market_type_support_matrix.md`.

## Canonical Schemas

### canonical_trade_bar

Required fields: `ts`, `instrument_id`, `open`, `high`, `low`, `close`,
`volume`, `quote_volume`, `trade_count`, `source`, `bar_source`,
`ingested_at`.

`bar_source`: `trade_bar`.

Caveat: real traded OHLCV.

### canonical_mid_bar

Required fields: `ts`, `instrument_id`, `open`, `high`, `low`, `close`,
`volume`, `quote_volume`, `trade_count`, `source`, `bar_source`,
`volume_policy`, `is_trade_bar`, `ingested_at`.

`bar_source`: `quote_mid` or `depth_mid`.

`volume_policy`: `quote_update_count`, `depth_update_count`, `zero`, or
`unknown`.

Caveat: derived price-path bar, not real traded OHLCV. Strategy performance on
this data is pipeline smoke evidence only unless matching trade bars exist.

### canonical_instrument_metadata

Required fields: `instrument_id`, `exchange`, `venue_type`, `symbol`,
`asset_class`, `tick_size`, `lot_size`, `multiplier`, `currency`, `expiry`,
`metadata_source`, `caveat`.

`metadata_source`: `native_catalog`, `deterministic_mvp`, or `manual_config`.

## Adapter Registry

| raw_type | adapter | output | VWM compatibility | confidence | status |
| --- | --- | --- | --- | --- | --- |
| `ohlcv_bar` | `direct_bar_adapter` | `canonical_trade_bar` | `true` | high | available |
| `aggTrades` | `aggtrades_to_ohlcv_bar` | `canonical_trade_bar` | `true` | high | planned |
| `trade_tick` | `trades_to_ohlcv_bar` | `canonical_trade_bar` | `true` | high | planned |
| `quote_tick` | `quote_tick_to_mid_bar` | `canonical_mid_bar` | `smoke_only` | medium | available |
| `order_book_depth` | `depth_to_mid_bar` | `canonical_mid_bar` | `smoke_only` | medium | available |
| `futures_contract` | `contract_to_instrument_metadata` | `canonical_instrument_metadata` | `metadata_only` | high if catalog-backed, medium if deterministic MVP | partial |

## Support Matrix

| raw_data_type | current_source | current_symbols | direct_vwm_compatible | adapter_required | adapter_name | output_type | reliability | caveat | current_status | next_action |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| OHLCV bar | Hive historical_data/market_data | BTCUSDT | yes | no | direct_bar_adapter | canonical_trade_bar | high | real traded OHLCV | usable for BTCUSDT 1m/5m smoke | expand inventory and report packaging |
| quote_tick | CFFEX native catalog | IC/IF/IH/IM 2301/2302/2303/2306 | no | yes | quote_tick_to_mid_bar | canonical_mid_bar | medium | not trade OHLCV; smoke-only strategy evidence | small IF2303 conversion smoke passed | C2d multi-contract derived conversion smoke |
| order_book_depth | CFFEX native catalog | IC/IF/IH/IM 2301/2302/2303/2306 | no | yes | depth_to_mid_bar / depth_to_features | canonical_mid_bar / feature_frame | medium | not trade OHLCV; top-of-book mid or derived book features | synthetic converter tests only | design feature-frame path before strategy use |
| futures_contract | CFFEX native catalog | IC/IF/IH/IM 2301/2302/2303/2306 | no | yes | contract_to_instrument_metadata | canonical_instrument_metadata | high if catalog-backed; current MVP deterministic | metadata only; does not enter VWM data feed | deterministic MVP mapping tests passed | replace MVP fields with catalog-backed metadata |
| aggTrades / trade ticks | sparse BTCUSDT aggTrades | BTCUSDT | no | yes | trades_to_ohlcv_bar | canonical_trade_bar | high | coverage insufficient for train/validation until date coverage is checked | planned registry entry | add coverage-aware trade-to-bar conversion smoke |
