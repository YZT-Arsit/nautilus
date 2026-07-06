# System Data / Feature / Storage / Reuse Architecture (internal audit)

Read-only technical walkthrough of the current `codex/platform-reorg` branch. Every
claim is tied to a concrete file / function / path or a real server data sample.
No strategy performance judgements are made here. Companion machine-readable
inventories live in [`outputs/architecture_inventory/`](../../outputs/architecture_inventory/):
`code_inventory.csv`, `feature_inventory.csv`, `feature_reuse_map.csv`,
`storage_format_inventory.csv`, `module_boundary_check.csv`, `data_storage_inventory.csv`,
`data_schema_samples.json`.

Generators (read-only, added by this audit):
`scripts/inspect_feature_architecture.py` (code/feature/boundary),
`scripts/inspect_data_storage.py` (server parquet inspection).

## 1. Scope

- Layers covered: `data_engine/`, `feature_engine/`, storage layout, `strategy_framework/`,
  `strategies/`, the batch/backtest read path, and evaluation output.
- Server inspected: `quant_data@172.16.112.43` (the reachable host; the `.81` address in
  the task brief is unreachable — see §12), repo `D:\nautilus`, data root
  `historical_data/market_data`.
- Several paths named in the task brief do **not** exist on this branch (pre-reorg):
  `nautilus_ext/`, `research/*.py`, `feature_engine/features/`, `scripts/build_phase1_result_system.py`,
  `scripts/build_strategy_batch_eval_table.py`, `outputs/deliverables/`. The reorg
  ([[project_platform_reorg]]) removed the ML/nautilus_ext/legacy layers.

## 2. Data engine

`data_engine/` is a self-owned, **Nautilus-free** layer (verified: 42 `.py` files, 0
real `nautilus_trader` imports — `module_boundary_check.csv`).

### 2.1 Canonical load entry
- File: `data_engine/loader.py` · Function: `load_events(data_config) -> (warmup_events, live_events)`
- Behavior: dispatches on `data["mode"]` via the `_LOADERS` registry.
- Modes: `synthetic`, `csv_bars`, `parquet_bars`, `hive_parquet_bars` (alias),
  `live_synthetic`, `live_gateway`, `binance_ws`, `synthetic_trades`, `parquet_trades`,
  `hive_parquet_trades`.
- `strategy_framework/data_loaders.py` is only a thin wrapper over this.

### 2.2 Historical bar read (the backtest source)
- File: `data_engine/sources/parquet_bars.py` · Class `ParquetBarSource`, function `load_parquet_bars`.
- Input: a `data:` block (`root`, `instrument_id`, `filters`, `timestamp_column`,
  `timestamp_unit`, `open/high/low/close/volume_column`, `start`, `end`, `warmup_bars`).
- Output: `(warmup, live)` lists of `data_engine.events.BarEvent` (via
  `data_engine/adapters/bar_adapter.py::make_bar_event`), sorted by `event_time_ns`,
  split by `data_engine/split.py::split_warmup_live`.
- Evidence / key mechanics:
  - `data_engine/sources/hive_partitioning.py::matching_fragments` selects only the
    fragments whose Hive `key=value` segments satisfy `filters`, **before** the schema
    guard — so a mixed root (bars + trades) does not confuse the bar loader.
  - `filter_fragments_by_date_range` prunes by the `date=` partition **before** any
    `to_table()` read (physical pruning).
  - `resolve_bar_timestamp_column` resolves `event_time_ns` → falls back to `ts` for the
    Binance-Vision/Hive schema; column pushdown projects only ts+OHLCV.
  - The `BarEvent.instrument_id` is taken from **config**, not from the parquet
    `instrument_id` column (that column is not even projected).

### 2.3 Ingestion (raw → standardized bar)
- File: `scripts/ingest_crypto_perpetual_bars.py` · Functions `build_plan`,
  `_canonicalize_frame`, `_validate_frame`, `execute_plan`.
- Behavior: guarded Binance USD-M perpetual kline smoke ingester (MAX_SYMBOLS=4,
  MAX_DAYS=1 by default), downloads via
  `feature_engine/data_sources/binance_vision.py::BinanceVisionImporter`.
- Standardization: `_canonicalize_frame` emits columns
  `ts, instrument_id, open, high, low, close, volume, quote_volume, trade_count, source,
  bar_source, is_trade_bar, ingested_at`; `instrument_id = "{symbol}-PERP.BINANCE"`;
  `source = "binance_vision_futures_um_klines"`.
- Quality checks: `_validate_frame` asserts non-empty, monotonic ts, no duplicate ts,
  finite OHLC, `high>=max(o,c)` / `low<=min(o,c)`, non-negative volume/quote/trade_count.
  Skip-existing is honored (`no_overwrite`, `status="skipped_existing"`).
- Multi-symbol: yes (guarded ≤4). Multi bar_type: via `--bar-type`.

### 2.4 Two partition-layout conventions (IMPORTANT)
- **Locked layout** (the one the stored data uses):
  `asset_class/exchange/venue_type/symbol/data_type=bar/freq/date` — defined in
  `feature_engine/storage/layout.py::MARKET_DATA_PARTITION_COLS`; produced by
  `feature_engine/services/minute_bar_builder.py` and
  `scripts/migrate_market_data_layout.py` (old→new re-layout, file move only).
- **Legacy layout**: `exchange/venue_type/symbol/bar_type=<freq>/date` — produced by
  `data_engine/historical/catalog.py::partition_relpath` and used by
  `data_engine/historical/*`, `scripts/ingest_crypto_perpetual_bars.py`,
  `scripts/run_vwm_batch_backtests.py`, `scripts/manage_historical_data.py`.
- Consequence (current limitation, §11): the active backtest read path + per-strategy
  configs + `run_batch.py`/`run_2y_batch.py` use the **locked** layout (freq filters);
  the legacy `bar_type=` helpers/`run_vwm_batch_backtests.py` are **not** aligned to the
  stored data.

## 3. Data storage format

Real, server-verified (`data_storage_inventory.csv`, `data_schema_samples.json`).

- Root: `historical_data/market_data/` (4324 date partitions on server).
- Partition keys (locked): `asset_class, exchange, venue_type, symbol, data_type, freq, date`.
- Bar parquet schema (sample `.../futures_um/symbol=BTCUSDT/data_type=bar/freq=1m/date=2024-07-01/part-0.parquet`):

  | column | type |
  |---|---|
  | ts | timestamp[us] |
  | instrument_id | string (`BTCUSDT-PERP.BINANCE`) |
  | open, high, low, close | double |
  | volume, quote_volume | double |
  | trade_count | int64 |
  | source, bar_source | string |
  | is_trade_bar | bool |
  | ingested_at | timestamp[us] |

- Timestamp: `ts` is `timestamp[us]` (not an int ns column); configs declare
  `timestamp_unit: ns` but `data_engine/time.py::to_event_time_ns` converts the datetime.
- `feature_data/`, `instruments/`, `manifests/` peers are **MISSING** on server (defined
  in `layout.py`, never populated).

## 10. Server data examples (real)

From `data_storage_inventory.csv` (group `date=ALL` rows) and per-date detail:

| venue_type | symbol | freq | rows | first | last | per-day | monotonic | dup |
|---|---|---|---:|---|---|---:|---|---:|
| futures_um | BTCUSDT | 1m | 1,049,760 | 2024-07-01 | 2026-06-29 | 1440 | True | 0 |
| futures_um | ETHUSDT | 1m | 1,049,760 | 2024-07-01 | 2026-06-29 | 1440 | True | 0 |
| futures_um | SOLUSDT | 1m | 1,049,760 | 2024-07-01 | 2026-06-29 | 1440 | True | 0 |
| futures_um | BTCUSDT | 15m | 17,472 | 2024-06-01 | 2026-05-31 | 96 | True | 0 |
| futures_um | ETH/SOL/BNB | 15m | 8,832 | 2026-03-01 | 2026-05-31 | 96 | True | 0 |
| spot | BTCUSDT | 1m/5m/tick | — | 2024-06-17 | 2026-06-16 | — | — | — |

- Every inspected futures_um 1m/15m date partition = exactly the theoretical bar count,
  monotonic, zero duplicate ts (status `ok`). BNBUSDT has **no** 1m data (15m only).
- 5m / 1h groups are sparse/short (single-day or ~3-month spans) — see the CSV.

## 4. Feature engine

`feature_engine/` is Nautilus-free (verified: 33 `.py`, 0 real `nautilus_trader` imports;
the string appears only in docstrings such as feature_lib/base.py "No nautilus_trader
import appears here").

- Public facade: `feature_engine/api.py` — strategies import `FeatureSpec`,
  `FeatureSnapshot`, `SpecFeatureEngine`, and the `*_spec` builders **only** from here
  (verified: 0 strategies import `feature_engine.compute/*` internals).
- Contracts: `feature_engine/compute/spec.py` — `FeatureSpec` (name, input_type,
  input_field, window, trigger `TriggerPolicy`, `depends_on` for derived features),
  `FeatureValue`, `FeatureSnapshot` (`snapshot.value(name)` / `is_ready(name)`), all in
  **nanoseconds**.
- Engine: `feature_engine/compute/` (`SpecFeatureEngine`) — turns market events into
  snapshots; watermarks/late-event policy in `spec.py::TriggerPolicy`.
- Runner: `feature_engine/runner.py::FeatureStrategyRunner` — builds the engine from
  specs and drives `warmup()` + `run()` yielding `(event, snapshot, signal)`.
- Offline: `feature_engine/offline.py::HistoricalFeatureBuilder` — runs the **same**
  `SpecFeatureEngine` over history and writes `feature_data` (offline == live parity).

## 5. Feature operators

- Library: `feature_engine/compute/feature_lib/{price_action,returns,volatility,
  normalization,volume,trade}.py` + builders in `feature_engine/builders.py`.
- 30 operators enumerated in `feature_inventory.csv` (rolling_mean + 28 builders):
  price/bar-structure (rolling_range, true_range, candle_body_ratio, shadow ratios),
  trend/momentum (return_n, momentum_n, price_position, drawdown, breakout_up/down),
  volatility (atr, volatility_ratio, bollinger_width, bollinger_percent_b), normalization/
  volume (zscore, volume_zscore, volume_ratio, quote_volume, vwap_distance), and trade/
  order-flow (trade_count, trade_imbalance, trade_vwap, signed_trade_volume, ...).
- All are pure-Python (`depends_on_nautilus = False`), dispatched by `params["type"]`.

## 6. Feature reuse logic

See `feature_reuse_map.csv`. Current state:

- **Registered strategies do not use the feature library.** All 63 plugin-based strategies
  declare only `rolling_mean_spec(..., window=1)` — an identity passthrough of
  open/high/low/close/volume (`strategies/<name>/plugin.py::build_specs`). The actual TB
  indicator maths (ADX/EMA/SMA/ATR/StdDev/Bollinger/Donchian…) live **inside each
  strategy's** `engine.py`, not as `FeatureSpec` operators. They are therefore not
  reusable across strategies and not persisted.
- **feature_library operators are unused** by any registered strategy (`currently_used_by =
  none`) despite being cross-strategy reusable by design.
- **No feature store is active.** The offline store capability exists
  (`HistoricalFeatureBuilder` → `feature_engine/storage/parquet_store.py::ParquetStore` →
  `feature_data/…`), but `historical_data/feature_data` is **absent** on the server. There
  is no feature cache in the run/backtest path.
- **Features are recomputed per run.** `run_strategy.py::run_config` builds a fresh
  `FeatureStrategyRunner` and streams features for every run and every symbol — batch
  backtests recompute features per strategy/symbol; the two fee scenarios reuse the one
  signal pass (`run_config` records the signal stream once and replays it into each fee
  backend).
- Minimal reusable unit today: the raw `BarEvent` (market_data) and the `FeatureSpec`
  operator set; the stable I/O schema is `BarEvent → FeatureSnapshot → "BUY"/"SELL"/"HOLD"`.
- `vwm_short`/`vwm_long` are the exception: their `indicators.py` import `nautilus_trader`
  indicators (2 of 66 strategies).
- External AI/Kronos-style predicted features (if ever added) belong at the
  `feature_data` / `outputs/features` layer as a **producer of a new feature_group**, read
  back through the same `FeatureSnapshot` contract — never inlined into `feature_engine`
  core or into a strategy engine. (Boundary note only; not implemented.)

## 7. Strategy / backtest data flow

Step-by-step, with file+function anchors:

- **A. Config load** — `run_strategy.py::_load_config` / `run_config(cfg)`; strategy looked
  up in `strategy_framework/registry.py::get_entry` → `StrategyPlugin`
  (`strategy_framework/plugin.py`).
- **B. Specs + runner** — `plugin.build_specs(config)` → `FeatureStrategyRunner(specs,
  plugin.strategy_cls(config))`.
- **C. Data read** — `data_engine.loader.load_events(data["..."])` →
  `load_parquet_bars` (§2.2) → `(warmup, live)` BarEvents.
- **D. Warmup + stream** — `runner.warmup(warmup)`; then `runner.run(live)` yields
  `(event, snapshot, signal)`; strategy `on_snapshot` reads `snapshot.value(...)` and
  returns `"BUY"/"SELL"/"HOLD"` (or a `PlannedSignal` carrying `TradeAction`s for
  sized/reversing strategies — `strategy_framework/execution/intents.py`).
- **E. Fee scenarios** — `run_config` computes signals **once**, then for each
  `execution.fee_scenarios` builds a backend and replays the recorded stream.
- **F. Backend** — `strategy_framework/backends/base.py::build_backend` →
  `NautilusBacktestBackend` (`backends/nautilus_backtest.py`), which routes on
  `execution.mode`: `simulated` (dependency-free reference fills) or `nautilus_native`
  (`backends/nautilus_native.py::run_native_backtest`, a real Nautilus `BacktestEngine`).
  `on_signal` maps signal→intent via `execution/signal_policy.py`
  (`sell_means: short|flat`, `allow_short`).
- **G. Report** — both modes feed `strategy_framework/execution/backtest_report.py`, which
  writes `metrics.json`, `trades.csv`, `fills.csv`, `positions.csv`, `intents.csv`,
  `signals.csv`, `equity_curve.csv`, `config.json`, `report.md`.
- **Batch job path** — `scripts/run_vwm_batch_backtests.py` (`load_batch_config` →
  `build_jobs(BatchJob)` → `_resolved_strategy_config` → subprocess `run_strategy main` →
  `aggregate_results` → `summary.csv`). NOTE this runner is VWM-specific and emits legacy
  `bar_type` filters (§2.4). The general N-strategy path is `run_batch.py` (fee_scenarios,
  `evaluation_table.csv`).

## 8. Evaluation / result artifacts flow

- Per-run: `backtest_report.py` → `outputs/backtests/<run>/` or
  `outputs/batches/<batch>/<strategy>/<fee>/` (`metrics.json`, `trades.csv`, `equity_curve.csv`, …).
- Aggregate: `run_batch.py::_write_table` / `run_2y_batch.py` → `evaluation_table.csv`
  (one row per strategy×fee); `run_vwm_batch_backtests.py::aggregate_results` →
  `summary.csv` (+ ranks, `failures.csv`).
- The pre-reorg "result system" (`pnl_timeseries`, `artifact_manifest`, `run_registry`,
  `dashboard_data`) does **not** exist on this branch.

## 9. Module boundaries

From `module_boundary_check.csv` (0 violations):

| rule | result |
|---|---|
| feature_engine/** imports nautilus_trader | clean (33 files, 0) |
| feature_engine/** imports strategy layers | clean (33 files, 0) |
| data_engine/** imports strategy layers | clean (42 files, 0) |
| data_engine/** imports nautilus_trader | clean (42 files, 0) |
| strategies import feature internals (not `.api`) | clean (all via `feature_engine.api`) |
| strategies import nautilus_trader | allowed; 2 files (`vwm_short`, `vwm_long` indicators.py) |

Rules that are process/semantic (evaluation must not modify strategy; result system must
not modify backtest; strategy must not reverse-modify feature_engine) are satisfied by
construction (evaluation reads `metrics.json`; strategies only consume `FeatureSnapshot`)
and are not statically expressible as import edges.

## 11. Current limitations (facts, not recommendations)

- **Dual partition layout** (§2.4): stored data is locked-layout; `data_engine/historical/*`,
  `run_vwm_batch_backtests.py`, `ingest_crypto_perpetual_bars.py` use legacy `bar_type=`
  paths → they do not select the current data.
- **No feature reuse / cache in the run path**: features recomputed per run/symbol;
  `feature_data/` never populated on server; `feature_library` operators unused by any
  registered strategy; TB indicators live inside strategy engines (not reusable).
- **instrument_id divergence**: parquet stores `…-PERP.BINANCE`; configs pass
  `…​.BINANCE`; loader uses the config id (single-instrument backtests unaffected).
- **`timestamp_unit: ns` vs `ts: timestamp[us]`**: cosmetic mismatch; conversion handled in
  `data_engine/time.py`.
- **vwm coupling to Nautilus**: 2 strategies import `nautilus_trader` indicators (others are
  pure Python); those tests need compiled Nautilus.
- **BNBUSDT has no 1m data** (15m only, 3 months); 5m/1h groups are sparse.

## 12. Do-not-touch boundaries

- Do not modify `data_engine/`, `feature_engine/`, `strategies/`, or the backtest runner
  (`strategy_framework/backends/*`, `run_strategy.py`).
- Do not repopulate/relayout `historical_data/market_data` (locked layout; live data).
- Server host is `172.16.112.43` (the `.81` in the brief is unreachable); remote runs use a
  working Python with pyarrow/polars/compiled nautilus.
- This audit added only read-only inspection scripts + `outputs/architecture_inventory/*`
  + this doc; it changed no business logic, installed nothing, downloaded no data.

---

## Diagram A — data → features → strategy → backtest → evaluation

```mermaid
flowchart LR
  BV[Binance Vision klines] -->|BinanceVisionImporter| ING[ingest / minute_bar_builder]
  ING -->|locked Hive layout| MD[(market_data parquet\nasset_class/exchange/venue_type/\nsymbol/data_type=bar/freq/date)]
  MD -->|load_parquet_bars\nhive filters + date prune| BE[BarEvent stream]
  BE -->|FeatureStrategyRunner.warmup/run| FE[SpecFeatureEngine\nfeature_lib operators]
  FE -->|FeatureSnapshot| ST[strategy.on_snapshot\nengine.py TB maths]
  ST -->|BUY/SELL/HOLD or PlannedSignal| SP[signal_policy -> intents]
  SP --> BK{execution.mode}
  BK -->|simulated| RPT[backtest_report.py]
  BK -->|nautilus_native| NB[Nautilus BacktestEngine] --> RPT
  RPT --> OUT[(outputs/backtests|batches/<run>\nmetrics.json, trades.csv, equity_curve.csv)]
  OUT --> EVAL[run_batch / run_2y_batch\nevaluation_table.csv]
  FE -. offline, UNUSED .-> HFB[HistoricalFeatureBuilder]
  HFB -. capability only .-> FD[(feature_data parquet\nABSENT on server)]
```

## Diagram B — storage layout tree

```mermaid
flowchart TD
  H[historical_data/] --> M[market_data/  PRESENT 4324 parts]
  H --> F[feature_data/  ABSENT]
  H --> I[instruments/  ABSENT]
  H --> MN[manifests/  ABSENT]
  M --> A[asset_class=crypto]
  A --> E[exchange=BINANCE]
  E --> V[venue_type=futures_um | spot]
  V --> S[symbol=BTCUSDT | ETHUSDT | SOLUSDT | BNBUSDT]
  S --> DT[data_type=bar]
  DT --> FQ[freq=1m | 15m | 5m | 1h]
  FQ --> D[date=YYYY-MM-DD]
  D --> P[part-0.parquet\nts,instrument_id,open,high,low,close,volume,\nquote_volume,trade_count,source,bar_source,\nis_trade_bar,ingested_at]
  O[outputs/] --> OB[backtests/ 815 files]
  O --> OBT[batches/ evaluation_table.csv + per-run dirs]
  O --> AI[architecture_inventory/ this audit]
```
