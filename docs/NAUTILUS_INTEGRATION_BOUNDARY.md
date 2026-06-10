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
| Feature processing | `feature_engine/` (esp. `feature_engine/compute/`) | `FeatureSpec`, `FeatureEngine`, rolling states, update DAG/order, warmup, incremental update, `FeatureSnapshot`. `nautilus_ext/features/` is a compatibility shim re-exporting this. |
| Orchestration | `strategy_framework/` | `StrategyPlugin`, registry, output, signal recording, execution **backend abstraction** (`backends/`) |
| User strategies | `strategies/` | strategy definitions + YAML configs |
| Entry point | `run_strategy.py` | the only normal user entry |

## What is the original Nautilus Trader

The upstream `nautilus_trader/` core: backtest engine, live engine, execution,
orders, portfolio, risk, data engine, catalog, adapters, and model objects, plus
its build/package metadata and tests.

## What we use today

- `data_engine` for all data loading (synthetic / CSV / Hive-Parquet / live skeleton).
- `feature_engine` for all feature computation (`nautilus_ext/features` is a compat shim).
- `strategy_framework` to orchestrate, plus the dependency-free backends
  (`signal_recorder`, `paper`).

## What we do NOT use today

- Nautilus Trader's native **data** system (we use our own `data_engine`).
- Nautilus Trader's native backtest/live **execution** engine. The
  `nautilus_backtest` backend's default `mode="simulated"` produces fills/PnL with
  a **dependency-free reference simulator** — it does **not** instantiate a native
  `BacktestEngine`. `mode="nautilus_native"` is a lazy placeholder that raises a
  clear `NotImplementedError` when driven. `nautilus_live` is still a placeholder.
- No pandas, no network/exchange dependencies in the custom framework.

## Execution-intent layer

Between signals and any backend sits `strategy_framework/execution/`:

- `intents.py` — `OrderIntent` / `PositionIntent` (frozen, dependency-free).
- `signal_policy.py` — `SignalToOrderPolicy` maps `BUY`/`SELL`/`HOLD` to an intent
  (`sell_means="flat"` → `PositionIntent(FLAT)`; `"short"` → `SELL` order).
- `reports.py` — `FillRecord` / `PositionRecord` / `ExecutionReport` describe
  execution *results* (still dependency-free, no Nautilus).

Strategies never create orders; the mapping lives here. This layer imports **no**
Nautilus Trader. Backends translate intents:

- `mode="simulated"` (default) uses `backends/nautilus_simulation.py`'s
  `IntentFillSimulator` — average-price positions, realized & unrealized PnL — and
  emits an `ExecutionReport`. No Nautilus dependency.
- `mode="nautilus_native"` is reserved for a real `BacktestEngine`. The Nautilus
  hooks `try_translate_to_nautilus_order` / `try_build_nautilus_backtest_engine`
  import `nautilus_trader` **lazily** and return `None` when unavailable; the mode
  raises `NotImplementedError` when driven until implemented.

## Why we keep Nautilus Trader core

Future historical backtesting and live execution may reuse Nautilus Trader's
mature backtest/live/execution engine as an optional backend. Deleting it now
would forfeit that path. It is therefore **preserved**, but removed from the
ordinary user-facing flow.

## Dependency direction

```
data_engine
    -> feature_engine
    -> strategy_framework
    -> strategies
    -> strategy_framework/execution   (signal -> OrderIntent/PositionIntent -> report)
    -> strategy_framework/backends    (intent -> backend)
        -> signal_recorder / simple_backtest / paper
        -> nautilus_backtest:
               mode=simulated      -> fills / positions / PnL now (no deps)
               mode=nautilus_native -> native BacktestEngine later (lazy placeholder)
        -> nautilus_live (placeholder)
```

- `data_engine` is **canonical** for our data processing.
- `feature_engine` is **canonical** for our feature processing
  (`nautilus_ext/features` is a thin compatibility shim re-exporting it).
- The Nautilus Trader native engine is an **optional future** execution/backtest
  backend, reached only through `strategy_framework/backends/nautilus_*`.

## Independence rules (must hold)

1. `data_engine/` imports no Nautilus Trader native objects, no pandas, no
   network/exchange libs.
2. `feature_engine/compute/` consumes only lightweight duck-typed events
   (`event_type`, `instrument_id`, `event_time_ns`, `open/high/low/close/volume`).
   If Nautilus `Bar` objects must be supported, add an **adapter outside**
   `compute/` — never make `compute/` depend on Nautilus.
3. `strategies/` import only the public feature API
   (`feature_engine.api`) and `strategy_framework.plugin` — never compute
   internals and never Nautilus objects.
4. All Nautilus coupling is confined to
   `strategy_framework/backends/nautilus_backtest.py` and
   `strategy_framework/backends/nautilus_live.py`, with Nautilus imported lazily
   inside methods.
