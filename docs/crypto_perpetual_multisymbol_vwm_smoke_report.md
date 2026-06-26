# Crypto Perpetual Multi-Symbol VWM Smoke Report

Date: 2026-06-25

## Goal

E4 expands the Binance USD-M perpetual smoke from BTCUSDT/ETHUSDT to four
symbols: BTCUSDT, ETHUSDT, SOLUSDT, and BNBUSDT.

## Public Data Source

- Source: Binance Vision public archive, USD-M futures daily klines.
- API key: no.
- Credentials or user/trading endpoints: no.
- Date: 2024-06-01.
- Bar type: 5m.

## Ingestion

Command:

```powershell
uv run --no-sync python scripts\ingest_crypto_perpetual_bars.py --symbols BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT --bar-type 5m --start 2024-06-01 --end 2024-06-01 --out-root historical_data\market_data --max-symbols 4
```

Manifest:

`outputs/ingestion_manifests/crypto_perpetual_multisymbol_bars_smoke_manifest.json`

| Symbol | Status | Rows | Output |
| --- | --- | ---: | --- |
| BTCUSDT | skipped_existing | 288 | `historical_data/market_data/exchange=BINANCE/venue_type=futures_um/symbol=BTCUSDT/bar_type=5m/date=2024-06-01/part-0.parquet` |
| ETHUSDT | skipped_existing | 288 | `historical_data/market_data/exchange=BINANCE/venue_type=futures_um/symbol=ETHUSDT/bar_type=5m/date=2024-06-01/part-0.parquet` |
| SOLUSDT | downloaded | 288 | `historical_data/market_data/exchange=BINANCE/venue_type=futures_um/symbol=SOLUSDT/bar_type=5m/date=2024-06-01/part-0.parquet` |
| BNBUSDT | downloaded | 288 | `historical_data/market_data/exchange=BINANCE/venue_type=futures_um/symbol=BNBUSDT/bar_type=5m/date=2024-06-01/part-0.parquet` |

Schema validation passed for all four symbols: required columns present,
`bar_source = trade_bar`, `is_trade_bar = true`, monotonic unique 5m timestamps,
finite bounded OHLC, and non-negative volume, quote_volume, and trade_count.

## Instrument Mapping

- `BTCUSDT-PERP.BINANCE`: TestInstrumentProvider `btcusdt_perp_binance`.
- `ETHUSDT-PERP.BINANCE`: TestInstrumentProvider `ethusdt_perp_binance`.
- `SOLUSDT-PERP.BINANCE`: deterministic MVP `CryptoPerpetual` mapping.
- `BNBUSDT-PERP.BINANCE`: deterministic MVP `CryptoPerpetual` mapping.

The SOL/BNB mappings are smoke-test mappings for market-type integration. They
do not add funding, liquidation, margin, or mark/index price PnL mechanics.

## VWM Batch Smoke

Command:

```powershell
uv run --no-sync python scripts\run_vwm_batch_backtests.py --config configs\backtests\vwm_crypto_perpetual_multisymbol_smoke.yaml --out outputs\backtests\crypto_perpetual_multisymbol_vwm_smoke --fail-fast
```

Output root:

`outputs/backtests/crypto_perpetual_multisymbol_vwm_smoke`

Result: 4 jobs, 4 executed, 0 failures.

| Symbol | Bars | Final equity | Total return | Max drawdown | Trades | Fills | Status |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| BTCUSDT | 288 | 99084.4251 | -0.00915575 | 0.00915575 | 5 | 11 | success |
| ETHUSDT | 288 | 99971.43001 | -0.0002857 | 0.00030959 | 2 | 4 | success |
| SOLUSDT | 288 | 99998.195983 | -0.00001804 | 0.00002979 | 5 | 11 | success |
| BNBUSDT | 288 | 99995.54043 | -0.0000446 | 0.00004461 | 5 | 10 | success |

`failures.csv` contains only the header.

## Caveat

These crypto perpetual backtests ignore funding, liquidation, margin, and
mark/index price effects unless explicitly modeled. They are pipeline-smoke
evidence that the market type and data source are integrated, not final strategy
performance evidence.

这些 crypto perpetual 回测暂未建模 funding、强平、保证金、mark/index price
等机制。当前结果用于证明数据源和市场类型接入链路走通，不是最终策略收益证据。

## Next Step

Rerun exchange_info when the REST endpoint is reachable, then add
funding-aware evaluation before using these runs for performance claims.
