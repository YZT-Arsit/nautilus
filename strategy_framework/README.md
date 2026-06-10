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

## Legacy

The old `feature_strategies/` package has been **removed**. Its entry point,
registry, data loaders, output, backtest recorder, and live sources now live at
the top level (`run_strategy.py` + `strategy_framework/`), and the strategy +
configs live in `strategies/ma_crossover/`. The only retained legacy shim is
`scripts/run_ma_crossover_demo.py`, which forwards to the top-level runner.
