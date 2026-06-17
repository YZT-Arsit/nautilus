# Historical Data Manager / Local Cache

Manages downloading, caching, scanning, planning and validating **historical**
Binance Vision market data into a server-local Hive-Parquet repository, so
backtests read local Parquet instead of re-downloading ZIPs every run.

```
Binance Vision archive (klines / aggTrades ZIP)   <-- historical, NOT live
  -> binance_vision adapter (reuse: build_*_url / read_* / normalize_*)
  -> StandardBar / StandardTrade
  -> historical_data/market_data/  (Hive Parquet; the backtest data repo)
  -> _catalog/manifest.jsonl       (coverage + provenance, sibling of market_data)
```

**Self-owned & Nautilus-free.** Everything here lives in `data_engine`
(`data_engine/historical/`). It imports **no** `nautilus_trader`. Nautilus is
used only downstream for backtest/live execution — never for data download,
normalization, or feature computation. `feature_engine` does not participate in
downloads.

**Historical, not live.** Binance Vision is a daily archive. Backtests read the
local Parquet cache; Binance Vision is contacted **only** on an explicit
`download`. Real-time "current moment" data is **not** built yet — a future live
path needs a separate live adapter (REST/WebSocket → Standard* → `data_engine`
live source → `feature_engine` online update → Nautilus live execution).

## Layout

```
historical_data/
  market_data/                      <-- Parquet dataset root (loaders read this)
    exchange=BINANCE/venue_type=spot/symbol=BTCUSDT/
      bar_type=5m/date=2024-06-01/part-0.parquet
      data_type=aggTrades/date=2024-06-01/part-0.parquet
  _catalog/
    manifest.jsonl                  <-- append-only; sibling, never inside market_data
```

The manifest is deliberately **outside** `market_data` so the Parquet dataset
root stays pure (no non-parquet files to confuse `pyarrow.dataset`).

## Modules (`data_engine/historical/`)

| module | responsibility |
| --- | --- |
| `catalog.py` | `LocalDataCatalog` — read-only scan: `inventory()`, `find_partitions()`, `partition_exists()`; partition path helpers |
| `plan.py` | `build_plan()` → `DownloadPlan` (existing / missing / skipped_existing / planned_downloads); no network |
| `manifest.py` | `Manifest` (append-only JSONL) + `ManifestRecord` |
| `validators.py` | `validate_partition()` — read-only bar/trade parquet checks (pyarrow) |
| `downloader.py` | `BinanceVisionHistoricalDownloader` — per-date fetch+write; injectable `fetcher` seam |

The downloader **reuses** `BinanceVisionImporter.import_period` /
`import_aggtrades_period` and `build_*_url` — no kline/aggTrades parser is
re-implemented. The network fetch is an injectable seam so tests need no network.

## Manifest record fields

`schema_version, status, exchange, venue_type, symbol, data_kind (bar|trade),
bar_type, data_type, date, source (binance_vision), source_url, local_path,
row_count, ts_min, ts_max, file_size_bytes, checksum, overwrite, error,
created_at`. `status ∈ {downloaded, skipped_existing, verified, failed}`.

## Skip-existing / overwrite

- **Default: skip-existing.** Existing partitions are left untouched and recorded
  `status=skipped_existing`.
- `--overwrite` re-downloads existing partitions (`existing_data_behavior=overwrite_or_ignore`).
- Downloads are **per date**: a failed date is isolated (`status=failed`), other
  dates and any existing data are never corrupted.

## CLI (`scripts/manage_historical_data.py`)

| command | network? | behavior |
| --- | --- | --- |
| `inventory --root …` | no | scan local partitions |
| `plan …` | no | classify existing vs missing; never writes |
| `verify …` | no | read-only validate; `--write-manifest` opt-in records verified/failed; exit≠0 if any invalid |
| `download …` | **yes** | download missing (skip-existing default); `--overwrite` to replace; validates then records manifest |

```
python scripts/manage_historical_data.py inventory --root historical_data/market_data

python scripts/manage_historical_data.py plan --exchange BINANCE --venue-type spot \
  --symbol BTCUSDT --data-kind bar --bar-type 5m --start 2024-06-01 --end 2024-06-03 \
  --root historical_data/market_data

python scripts/manage_historical_data.py verify --exchange BINANCE --venue-type spot \
  --symbol BTCUSDT --data-kind trade --data-type aggTrades \
  --start 2024-06-01 --end 2024-06-01 --root historical_data/market_data

python scripts/manage_historical_data.py download --exchange BINANCE --venue-type spot \
  --symbol BTCUSDT --data-kind trade --data-type aggTrades \
  --start 2024-06-01 --end 2024-06-03 --root historical_data/market_data --skip-existing
```

## Tests

`nautilus_ext/tests/test_historical_data_manager.py` — tmp dirs + mock parquet,
no real download. Catalog/plan/manifest/skip+failed-download logic is pure-Python
(testable without pyarrow); validators + the real download write are
pyarrow-gated. Asserts no `nautilus_trader` import anywhere in the package.
