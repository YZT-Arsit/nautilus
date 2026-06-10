# Nautilus Integration Boundary

This document defines the boundary between **our custom strategy/feature
framework** and the **original Nautilus Trader codebase** that this repository is
forked from.

## TL;DR

- Our framework owns **data processing** and **feature processing** today.
- Nautilus Trader's engine is kept as an **optional, future execution/backtest
  backend** — not the canonical data/feature implementation.
- Strategies and the feature engine must **never** couple directly to Nautilus
  Trader native objects. All Nautilus coupling lives behind an execution backend
  adapter.

## What is our custom framework

| Layer | Package | Owns |
|-------|---------|------|
| Data processing | `data_engine/` | `BarEvent` & lightweight market events, data sources (`synthetic`, `csv_bars`, `parquet_bars`/`hive_parquet_bars`, `live_synthetic`), CSV & Hive-Parquet parsing, timestamp conversion, warmup/live split, adapters, stream abstraction |
| Feature processing | `nautilus_ext/features/` (esp. `features/compute/`) | `FeatureSpec`, `FeatureEngine`, rolling states, update DAG/order, warmup, incremental update, `FeatureSnapshot` |
| Orchestration | `strategy_framework/` | `StrategyPlugin`, registry, output, signal recording, execution **backend abstraction** (`backends/`) |
| User strategies | `strategies/` | strategy definitions + YAML configs |
| Entry point | `run_strategy.py` | the only normal user entry |

## What is the original Nautilus Trader

The upstream `nautilus_trader/` core: backtest engine, live engine, execution,
orders, portfolio, risk, data engine, catalog, adapters, and model objects, plus
its build/package metadata and tests.

## What we use today

- `data_engine` for all data loading (synthetic / CSV / Hive-Parquet / live skeleton).
- `nautilus_ext/features` for all feature computation.
- `strategy_framework` to orchestrate, plus the dependency-free backends
  (`signal_recorder`, `paper`).

## What we do NOT use today

- Nautilus Trader's native **data** system (we use our own `data_engine`).
- Nautilus Trader's backtest/live **execution** engine (the `nautilus_backtest`
  and `nautilus_live` backends are placeholders only).
- No pandas, no network/exchange dependencies in the custom framework.

## Why we keep Nautilus Trader core

Future historical backtesting and live execution may reuse Nautilus Trader's
mature backtest/live/execution engine as an optional backend. Deleting it now
would forfeit that path. It is therefore **preserved**, but removed from the
ordinary user-facing flow.

## Dependency direction

```
data_engine
    -> nautilus_ext/features
    -> strategies
    -> strategy_framework/backends
        -> simple_backtest / paper / future Nautilus backend
```

- `data_engine` is **canonical** for our data processing.
- `nautilus_ext/features` is **canonical** for our feature processing.
- The Nautilus Trader native engine is an **optional future** execution/backtest
  backend, reached only through `strategy_framework/backends/nautilus_*`.

## Independence rules (must hold)

1. `data_engine/` imports no Nautilus Trader native objects, no pandas, no
   network/exchange libs.
2. `nautilus_ext/features/compute/` consumes only lightweight duck-typed events
   (`event_type`, `instrument_id`, `event_time_ns`, `open/high/low/close/volume`).
   If Nautilus `Bar` objects must be supported, add an **adapter outside**
   `compute/` — never make `compute/` depend on Nautilus.
3. `strategies/` import only the public feature API
   (`nautilus_ext.features.api`) and `strategy_framework.plugin` — never compute
   internals and never Nautilus objects.
4. All Nautilus coupling is confined to
   `strategy_framework/backends/nautilus_backtest.py` and
   `strategy_framework/backends/nautilus_live.py`, with Nautilus imported lazily
   inside methods.
