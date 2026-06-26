# Crypto Perpetual VWM Smoke Report

Date: 2026-06-25

## Goal

Extend the VWM batch backtest path from BTCUSDT spot to Binance crypto
perpetual multi-symbol data using public read-only market data.

## Data Source

- Source: Binance Vision public archive, USD-M futures daily klines.
- Public read-only: yes.
- API key: no.
- Credentials or user/trading endpoints: no.
- URLs:
  - `https://data.binance.vision/data/futures/um/daily/klines/BTCUSDT/5m/BTCUSDT-5m-2024-06-01.zip`
  - `https://data.binance.vision/data/futures/um/daily/klines/ETHUSDT/5m/ETHUSDT-5m-2024-06-01.zip`

## Ingestion

Command:

```powershell
uv run --no-sync python scripts\ingest_crypto_perpetual_bars.py --symbols BTCUSDT,ETHUSDT --bar-type 5m --start 2024-06-01 --end 2024-06-01 --out-root historical_data\market_data
```

Output:

| Symbol | Market type | Date | Bar type | Rows | Output |
| --- | --- | --- | --- | ---: | --- |
| BTCUSDT | crypto_perpetual / futures_um | 2024-06-01 | 5m | 288 | `historical_data/market_data/exchange=BINANCE/venue_type=futures_um/symbol=BTCUSDT/bar_type=5m/date=2024-06-01/part-0.parquet` |
| ETHUSDT | crypto_perpetual / futures_um | 2024-06-01 | 5m | 288 | `historical_data/market_data/exchange=BINANCE/venue_type=futures_um/symbol=ETHUSDT/bar_type=5m/date=2024-06-01/part-0.parquet` |

Manifest:

`outputs/ingestion_manifests/crypto_perpetual_bars_smoke_manifest.json`

## Schema Validation

Validation passed for both symbols:

- Required columns present: `ts`, `instrument_id`, `open`, `high`, `low`,
  `close`, `volume`, `quote_volume`, `trade_count`, `source`, `bar_source`,
  `ingested_at`.
- `bar_source = trade_bar`.
- `is_trade_bar = true`.
- `source = binance_vision_futures_um_klines`.
- Rows > 0, timestamps monotonic, no duplicate timestamps, 5m aligned.
- OHLC finite and bounded, volume / quote_volume / trade_count non-negative.

## Instrument Mapping

- `BTCUSDT-PERP.BINANCE` -> Nautilus TestInstrumentProvider `btcusdt_perp_binance`.
- `ETHUSDT-PERP.BINANCE` -> Nautilus TestInstrumentProvider `ethusdt_perp_binance`.
- Spot mappings remain unchanged.

## VWM Batch Smoke

Command:

```powershell
uv run --no-sync python scripts\run_vwm_batch_backtests.py --config configs\backtests\vwm_crypto_perpetual_smoke.yaml --out outputs\backtests\crypto_perpetual_vwm_smoke --fail-fast
```

Output root:

`outputs/backtests/crypto_perpetual_vwm_smoke`

Summary paths:

- `outputs/backtests/crypto_perpetual_vwm_smoke/summary.csv`
- `outputs/backtests/crypto_perpetual_vwm_smoke/summary.json`
- `outputs/backtests/crypto_perpetual_vwm_smoke/summary.md`
- `outputs/backtests/crypto_perpetual_vwm_smoke/failures.csv`

Result: 2 jobs, 2 executed, 0 failures.

| Symbol | Bars | Final equity | Total return | Max drawdown | Trades | Fills | Status |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| BTCUSDT | 288 | 99084.4251 | -0.00915575 | 0.00915575 | 5 | 11 | success |
| ETHUSDT | 288 | 99971.43001 | -0.0002857 | 0.00030959 | 2 | 4 | success |

`failures.csv` contains only the header.

## Caveat

These crypto perpetual backtests ignore funding, liquidation, margin, and
mark/index price effects unless explicitly modeled. They are pipeline-smoke
evidence that the market type and data source are integrated, not final strategy
performance evidence.

这些 crypto perpetual 回测暂未建模 funding、强平、保证金、mark/index price
等机制。当前结果用于证明数据源和市场类型接入链路走通，不是最终策略收益证据。

## Next Steps

E3 adds a public metadata adapter. Funding_rate and mark/index price passed a
small Binance Vision archive smoke; exchange_info remains REST-audited but
network-blocked in the current test environment. Metadata is collected as a
sidecar layer and still does not alter VWM PnL.

1. Add funding-aware evaluation before making performance claims.
2. Rerun exchange_info when the REST endpoint is reachable, then use it as a metadata-backed instrument mapping source if needed.
3. Expand controlled smoke to SOLUSDT/BNBUSDT after metadata path is stable.
4. Add slippage and margin model before performance claims.
