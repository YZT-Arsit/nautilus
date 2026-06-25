# Feature Engine Structure Audit

E1 checks that small feature operators live under `feature_engine/features/`
without changing the larger feature_engine architecture.

## Current Directory Classification

| Path group | Classification | Notes |
| --- | --- | --- |
| `feature_engine/features/` | feature_operator_home | Small feature operators and their registration modules live here. |
| `feature_engine/core/` | keep_core | Feature base classes, registry, DAG, schema, state. |
| `feature_engine/compute/` | keep_core | Event/stream computation infrastructure. |
| `feature_engine/storage/` | keep_core | Feature storage and readers. |
| `feature_engine/services/` | keep_core | Historical feature build services. |
| `feature_engine/data_sources/` | keep_bridge | Archive data source adapters such as Binance Vision. |
| root orchestration files | keep_core | `base.py`, `interfaces.py`, `feature_engine.py`, `feature_pipeline.py`, `feature_registry.py`, `feature_schema.py`, `feature_store.py`, `feature_cache.py`, `feature_joiner.py`, `feature_recorder.py`, `runner.py`, `builders.py`. |

## Feature Operator Modules

Confirmed small operators under `feature_engine/features/`:

- `derived.py`
- `macd.py`
- `moving_average.py`
- `rolling_volatility.py`
- `rsi.py`
- `vwm.py`

These modules use `feature_engine.core.*` and Polars. They should not import
the Nautilus trading package.

## Suspected Root-Level Redundancy

| File | Import usage | Nautilus import | Duplicate with `features/` | Decision | Follow-up |
| --- | --- | --- | --- | --- | --- |
| `feature_engine/nautilus_indicators.py` | Imported by `feature_engine/vwm_features.py` and lazy exports in `feature_engine/__init__.py`. | Yes, imports Nautilus indicators. | Overlaps conceptually with moving averages/ATR primitives but wraps Nautilus native indicators for legacy VWM path. | keep_bridge | Do not move into `feature_engine/features/`; keep isolated as optional bridge. Add future pure-Polars/streaming replacement before deprecating. |
| `feature_engine/tradeblazer_features.py` | Imported by `feature_engine/vwm_features.py`, `feature_engine/__init__.py`, docs. | No. | Contains low-level TradeBlazer momentum/crossover primitives, not registered feature operators. | deprecated_compat | Keep public import path; future work can move primitives to `feature_engine/features/` or `feature_engine/core/` with wrapper compatibility. |
| `feature_engine/vwm_adapter.py` | Imported lazily by `feature_engine/feature_registry.py`, tests, strategy bridge code. | Indirectly through `vwm_features.py` / Nautilus indicator wrappers. | Adapter/bridge, not a small feature operator. | keep_bridge | Keep as feature-data-layer adapter for legacy VWM features. |
| `feature_engine/vwm_features.py` | Imported by `feature_engine/vwm_adapter.py`; depends on root legacy primitives. | Indirectly via `nautilus_indicators.py`. | Legacy streaming VWM engine, distinct from `features/vwm.py` registered Polars operator. | remove_later | Keep until strategy/adapter consumers move to canonical `features/` operators. |

## Policy

- New small feature operators must be added under `feature_engine/features/`.
- Root-level feature files are allowed only as core/orchestration modules,
  compatibility wrappers, or bridge adapters.
- Do not delete legacy import paths in E1.
- Do not introduce Nautilus trading package imports into `feature_engine/features/`.
- Do not couple feature_engine to Nautilus beyond existing isolated bridge files.
