# strategy_framework — shared orchestration glue

Reusable, strategy-agnostic plumbing used by the top-level `run_strategy.py`.
Ordinary strategy authors rarely edit anything here; they work in
`strategies/<name>/`.

## Modules

| Module | Responsibility |
|--------|----------------|
| `plugin.py` | `StrategyPlugin` descriptor (name, config_cls, strategy_cls, build_specs, default_config_path) |
| `registry.py` | Explicit `STRATEGY_REGISTRY` + `get_entry(name)`; imports each strategy's `PLUGIN` |
| `data_loaders.py` | `load_events(data_config)` → `(warmup, live)`; modes: `synthetic`, `csv_bars`, `live_synthetic` |
| `output.py` | Warmup summary, event table, signal summary; defensive about missing `close`/`event_time_ns` |
| `backtest.py` | `SignalRecorder` — captures `(event, snapshot, signal)` rows; counts + plain dicts (no PnL) |
| `live_sources.py` | `LiveEventSource` protocol + `SyntheticLiveEventSource` (extension point for real feeds) |
| `execution/` | Signal→intent layer: `OrderIntent`/`PositionIntent` + `SignalToOrderPolicy` (dependency-free, no Nautilus) |
| `backends/` | Execution backends: `signal_recorder`, `simple_backtest`, `paper`, `nautilus_backtest` (MVP), `nautilus_live` (placeholder) |

## Where things live

```
run_strategy.py                 # the only normal entry point (repo root)
strategies/<name>/              # strategy definition + config + README
strategy_framework/             # this package — shared glue
feature_engine/api.py    # stable public API facade for strategy authors
feature_engine/runner.py # FeatureStrategyRunner
feature_engine/compute/  # low-level feature engine (do not edit for a new strategy)
```

## Extension points

- **New data source** → add a loader and register a `mode` in `data_loaders.py`.
- **Real live feed** → implement `LiveEventSource` in `live_sources.py`, then
  register a `mode` in `data_loaders.py` and add a config.
- **New strategy** → see `strategies/<name>/README.md`; register its `PLUGIN`
  in `registry.py`.
- **New low-level feature operator** → `feature_engine/compute/features.py`
  + `compute/backend.py` + `builders.py` + `api.py` (+ compute tests).
- **New execution backend** → implement `ExecutionBackend` in `backends/`, reuse
  `execution.SignalToOrderPolicy` for the signal→intent mapping, and register the
  name in `backends/base.py:build_backend`.

## Execution flow

```
data_engine -> feature_engine -> strategy_framework -> strategies -> execution backend
```

- `data_engine` owns data loading; `feature_engine` owns feature computation;
  `strategies` own signal logic (BUY/SELL/HOLD).
- `strategy_framework.execution` maps signals to **order intents**
  (`SignalToOrderPolicy`); strategies never create orders directly.
- Backends consume intents. `run_strategy.py` just calls `build_backend(...)`,
  then `backend.on_signal(...)` per event and `backend.close()` at the end.
- Configure via an `execution:` block:

  ```yaml
  execution:
    backend: nautilus_backtest   # or: signal_recorder | simple_backtest | paper
    quantity: 1.0
    sell_means: flat             # "flat" -> PositionIntent(FLAT); "short" -> SELL order
  ```

- **Nautilus Trader is an optional execution/backtest/live backend.** The current
  `nautilus_backtest` backend is an **MVP: it collects order intents and prints a
  summary — no fills or PnL yet.** Full `BacktestEngine` integration is the next
  stage. All Nautilus imports are lazy (inside optional methods only).

## Legacy

The old `feature_strategies/` package has been **removed**. Its entry point,
registry, data loaders, output, backtest recorder, and live sources now live at
the top level (`run_strategy.py` + `strategy_framework/`), and the strategy +
configs live in `strategies/ma_crossover/`. The only retained legacy shim is
`scripts/run_ma_crossover_demo.py`, which forwards to the top-level runner.
