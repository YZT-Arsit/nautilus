# Archived: Codex multi-market / registry / CFFEX route

These files were moved out of the active tree (2026-06) when the project was
**converged to a single crypto-perpetual backtest loop** (BINANCE USD-M
perpetual `BTCUSDT`). They are preserved here (not deleted) so the work can be
restored if multi-market generalization is resumed.

## Why archived

All modules below were proven to be **off the BTCUSDT-perpetual backtest path**:
the kept loop (`scripts/ingest_crypto_perpetual_bars.py`,
`scripts/run_vwm_batch_backtests.py`,
`strategy_framework/backends/nautilus_native.py`) imports **none** of them. Each
was referenced **only by its own test**, which is why the tests were archived
alongside their modules. No public API (`research/__init__.py` is a pure
docstring) re-exported any of them, so `import research` is unaffected.

## Contents

- `research/` — the over-complex market/adapter registry + metadata sidecar +
  CFFEX bar converter:
  - `crypto_market_registry.py`, `crypto_perpetual_metadata.py`
  - `market_integration_registry.py`, `adapter_registry.py`, `data_type_adapters.py`
  - `cffex_bar_converter.py`
- `scripts/` — `ingest_crypto_perpetual_metadata.py` (metadata sidecar),
  `convert_cffex_catalog_to_bars.py` (CFFEX)
- `configs/` — `vwm_cffex_midbar_*.yaml` (CFFEX quote-mid bar smoke configs)
- `tests/` — the 7 tests that exercised the archived modules
- `docs/` — planning-only support matrices + multi-market/multi-data-type smoke
  reports not used by the BTCUSDT path

## Restoring

`git mv` any file back to its original location (visible in git history) and,
for modules, restore the matching test into `nautilus_ext/tests/`.

## What was deliberately KEPT active

- `scripts/ingest_crypto_perpetual_bars.py` — minimal Binance Vision USD-M
  perpetual kline import
- `scripts/run_vwm_batch_backtests.py` — batch runner
- `strategy_framework/backends/nautilus_native.py` — self-contained instrument
  mapping (already includes `BTCUSDT-PERP.BINANCE`)
- VWM strategy + `feature_engine` (unchanged); small operators remain in
  `feature_engine/features/`
