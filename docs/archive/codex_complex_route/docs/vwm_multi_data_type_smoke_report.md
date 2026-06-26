# VWM Multi-Data-Type Smoke Report

Date: 2026-06-25

## Goal

Extend the VWM batch research pipeline from ready-made OHLCV bars to multiple
data sources and data types without changing the VWM strategy core:

raw bars / trade ticks / quote ticks / order book depth / futures contract
metadata -> adapter / converter -> canonical bars or instrument metadata ->
VWM batch smoke -> summary / ranking / evaluation report.

## Support Matrix

| Raw data type | Adapter | Canonical output | VWM compatibility | Status |
| --- | --- | --- | --- | --- |
| OHLCV bar | `direct_bar_adapter` | `canonical_trade_bar` | `true` | BTCUSDT 1m/5m smoke passed |
| aggTrades / trade tick | `trades_to_ohlcv_bar` | `canonical_trade_bar` | `true` | registry/design entry, future conversion smoke |
| quote_tick | `quote_tick_to_mid_bar` | `canonical_mid_bar` | `smoke_only` | CFFEX IF/IH/IC/IM 2303 one-day smoke passed |
| order_book_depth | `depth_to_mid_bar` / `depth_to_features` | `canonical_mid_bar` / feature frame | `smoke_only` | synthetic converter coverage only |
| futures_contract | `contract_to_instrument_metadata` | `canonical_instrument_metadata` | `metadata_only` | deterministic MVP mapping tested |

## Validated Links

- BTCUSDT OHLCV bar -> VWM batch smoke: previously passed for 1m and 5m.
- CFFEX quote_tick -> quote-mid `canonical_mid_bar`: IF/IH/IC/IM 2303, 2023-01-03, 1m bars.
- CFFEX futures deterministic MVP instrument mapping: IF/IH multipliers 300, IC/IM multipliers 200, tick size 0.2, CNY, lot size 1.
- CFFEX IF2303 quote-mid bar -> VWM smoke: single-contract smoke passed.
- CFFEX IF/IH/IC/IM 2303 quote-mid bars -> VWM batch smoke: 4 jobs passed, 0 failures.

## Validation

- Python: `D:\nautilus\.venv\Scripts\python.exe`
- Pytest: `7.4.4`
- D1a `py_compile`: passed.
- D1a targeted pytest: `11 passed`.
- Related regression: `48 passed`.
- Source scan: clean for D1a adapter/docs/tests and CFFEX converter files. The native backend still contains expected `BacktestEngine`, order, and account terms because it is the existing native backtest implementation, not a live trading connector.
- `pyproject.toml` unchanged: `91e5a2221c0820ea3bab665949fe8f2281682d206c84acecb2827ccebddee2b3`.
- `uv.lock` unchanged: `59842e2d4d111e1fd80699ee010bdf7b1dd97f52d86d074c7e84b6de919861b8`.

## C2c IF2303 Smoke

Output root:

`outputs/backtests/cffex_vwm_midbar_smoke_mapped_2`

Summary paths:

- `outputs/backtests/cffex_vwm_midbar_smoke_mapped_2/summary.csv`
- `outputs/backtests/cffex_vwm_midbar_smoke_mapped_2/summary.json`
- `outputs/backtests/cffex_vwm_midbar_smoke_mapped_2/summary.md`
- `outputs/backtests/cffex_vwm_midbar_smoke_mapped_2/failures.csv`

Result: 1 job, 1 executed, 0 failures.

| Symbol | Bars | Final equity | Total return | Max drawdown | Trades | Fills | Status |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| IF2303 | 242 | 95897.85 | -0.0410215 | 0.0411495 | 3 | 7 | success |

## C2d Multi-Contract Smoke

Derived bar output:

`outputs/derived_market_data/cffex_mid_bars_c2d_smoke`

Backtest output root:

`outputs/backtests/cffex_vwm_midbar_smoke_c2d`

The original requested backtest name `cffex_vwm_midbar_c2d_smoke` does not match
the existing runner smoke-output guard. To avoid changing batch-runner execution
isolation or guard behavior, the smoke was run under the already approved
`cffex_vwm_midbar_smoke*` prefix.

Summary paths:

- `outputs/backtests/cffex_vwm_midbar_smoke_c2d/summary.csv`
- `outputs/backtests/cffex_vwm_midbar_smoke_c2d/summary.json`
- `outputs/backtests/cffex_vwm_midbar_smoke_c2d/summary.md`
- `outputs/backtests/cffex_vwm_midbar_smoke_c2d/failures.csv`

Result: 4 jobs, 4 executed, 0 failures.

| Symbol | Bars | Final equity | Total return | Max drawdown | Trades | Fills | Status |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| IC2303 | 242 | 99418.22 | -0.0058178 | 0.0059938 | 0 | 1 | success |
| IF2303 | 242 | 95897.85 | -0.0410215 | 0.0411495 | 3 | 7 | success |
| IH2303 | 241 | 98808.48 | -0.0119152 | 0.0119382 | 1 | 3 | success |
| IM2303 | 242 | 98092.53 | -0.0190747 | 0.0192087 | 1 | 3 | success |

`failures.csv` contains only the header for both C2c and C2d.

## Caveats

These CFFEX bars are quote-mid derived bars. They are not real trade OHLCV bars.
The volume field is quote update count, not traded volume. The resulting
backtests are pipeline-smoke evidence for data-type integration, not strategy
performance evidence.

CFFEX bar 是 quote-mid 派生 bar，不是真实成交 OHLCV。volume 是 quote 更新次数，
不是成交量。因此这些回测结果只能说明数据类型接入链路走通，不能作为策略收益有效性的证据。

The CFFEX instrument mapping is still deterministic MVP metadata, not yet
catalog-backed from `futures_contract`.

## Next Steps

1. Replace deterministic CFFEX MVP mapping with catalog-backed `futures_contract` metadata.
2. Add trade tick / aggTrades -> real OHLCV conversion smoke.
3. Add order book depth feature-frame adapter and decide how strategy evaluation consumes it.
4. Expand from one-day smoke to controlled multi-symbol / multi-date evaluation.
