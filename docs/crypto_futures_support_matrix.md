# Crypto Futures Support Matrix

E1 audits crypto market expansion from the asset/market perspective. This is
not a new parquet schema layer and it does not claim unavailable data as
supported.

## Confirmed Data Sources

- Local workspace: no `historical_data` tree was present.
- Remote `D:\nautilus\historical_data\market_data`: confirmed `BINANCE / spot / BTCUSDT`.
- Confirmed bars:
  - `BTCUSDT` 1m: 2024-06-17..2026-06-16, 730 partitions, 1,051,200 rows, usable.
  - `BTCUSDT` 5m: 2024-06-01..2026-06-16, 733 partitions, 211,104 rows, incomplete due date gaps.
- Confirmed trades:
  - `BTCUSDT` spot aggTrades: 33 date partitions, 2024-06-01..2026-06-16, about 1113 MB.
- No confirmed local/remote historical partitions for Binance `futures_um`, Binance `futures_cm`, OKX, Bybit, CTFX, ETHUSDT, BNBUSDT, SOLUSDT, XRPUSDT, ADAUSDT, or DOGEUSDT.

## Code-Level Sources / Connectors

- `feature_engine.data_sources.binance_vision` supports Binance Vision `spot`, `futures_um`, and `futures_cm` kline/aggTrades parsing and normalization.
- `scripts/ingest_binance_vision.py` exposes `--market spot|futures_um|futures_cm` and `--data-type klines|aggTrades`.
- `nautilus_ext.ccxt_live.polling_config` and connector tests mention `binance`, `okx`, and `bybit`, but E1 found no confirmed historical OKX/Bybit data.

## Support Matrix

| exchange | market_type | symbol | instrument_id | raw_data_available | bar_types_available | trade_data_available | funding_rate_available | mark_price_available | index_price_available | instrument_metadata_available | vwm_compatible | current_status | caveat | next_action |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| BINANCE | crypto_spot | BTCUSDT | BTCUSDT.BINANCE | OHLCV bars; aggTrades | 1m confirmed usable; 5m present but incomplete | aggTrades confirmed: 33 date partitions, 2024-06-01..2026-06-16 | no | no | no | deterministic/TestInstrumentProvider mapping | true_trade_bar | confirmed_available | 5m has date gaps; aggTrades coverage is sparse relative to bar history. | add ETHUSDT/BNBUSDT/SOLUSDT futures data import plan before claiming multi-symbol coverage |
| BINANCE | crypto_spot | ETHUSDT | ETHUSDT.BINANCE | none confirmed in local/remote data tree | none confirmed | none confirmed | no | no | no | deterministic/TestInstrumentProvider mapping exists in backend tests | planned | planned | code-level mapping exists, but no confirmed historical bars in scanned data tree | import or locate ETHUSDT spot/futures bars |
| BINANCE | crypto_perpetual | BTCUSDT | BTCUSDT.BINANCE-PERP | no local futures/perpetual data confirmed | none confirmed | none confirmed | planned | planned | planned | planned | planned | adapter_code_available_data_missing | Binance Vision futures_um adapter code exists, but scanned historical data has no futures_um partitions | E2: controlled import plan for BTC/ETH/BNB/SOL perpetual bars plus metadata |
| BINANCE | crypto_delivery_futures | BTCUSD delivery | BTCUSD_DELIVERY.BINANCE | no local delivery futures data confirmed | none confirmed | none confirmed | not applicable or venue-specific | planned | planned | planned | planned | adapter_code_available_data_missing | Binance Vision futures_cm adapter code exists, but scanned historical data has no futures_cm partitions | defer until perpetual path is validated |
| OKX | crypto_perpetual | BTC-USDT-SWAP | BTC-USDT-SWAP.OKX | none confirmed | none confirmed | none confirmed | planned | planned | planned | ccxt connector tests/mock coverage only | planned | connector_planned_no_data | OKX appears in connector tests/config examples, not as confirmed historical data | inventory or import OKX swap bars only after Binance perp path is validated |
| BYBIT | crypto_perpetual | BTCUSDT | BTCUSDT.BYBIT-PERP | none confirmed | none confirmed | none confirmed | planned | planned | planned | ccxt connector tests/mock coverage only | planned | connector_planned_no_data | Bybit appears in connector tests/config examples, not as confirmed historical data | keep planned until data source is approved |

## Metadata Requirements

Crypto futures and perpetuals require more than extra symbols:

- `contract_type`: `spot`, `perpetual`, or `delivery_future`.
- `base_asset`, `quote_asset`, `settlement_asset`, `margin_asset`.
- `multiplier` / `contract_size`.
- `tick_size`, `lot_size`, `price_precision`, `size_precision`.
- `fee_model`.
- `funding_rate`, `funding_interval` for perpetuals.
- `mark_price`, `index_price`.
- `session_model`: usually 24/7 for crypto.
- liquidation / margin metadata: future work.

## Caveat

VWM compatibility is `true_trade_bar` only when real traded OHLCV bars exist.
Perpetual futures bars need explicit caveats if funding, mark price, margin, or
contract-size effects are ignored.
