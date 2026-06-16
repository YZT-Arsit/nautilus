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
data_engine
    -> feature_engine
    -> strategies                       (signal: BUY/SELL/HOLD)
    -> SignalToOrderPolicy              (signal -> intent)
    -> OrderIntent / PositionIntent
    -> NautilusBacktestBackend
        -> mode=simulated:      IntentFillSimulator (dependency-free fills/PnL)
        -> mode=nautilus_native: real Nautilus BacktestEngine (lazy adapter)
        -> shared report writer -> outputs/backtests/<run_name>/
           (signals, intents, trades, positions, equity_curve, metrics.json, report.md)
```

- `data_engine` owns data loading; `feature_engine` owns feature computation;
  `strategies` own signal logic (BUY/SELL/HOLD).
- `strategy_framework.execution` maps signals to **intents** (`SignalToOrderPolicy`)
  and models results (`FillRecord`, `PositionRecord`, `ExecutionReport`);
  strategies never create orders directly.
- Backends consume intents. `run_strategy.py` just calls `build_backend(...)`,
  then `backend.on_signal(...)` per event and `backend.close()` at the end.
- Configure via an `execution:` block:

  ```yaml
  execution:
    backend: nautilus_backtest   # or: signal_recorder | simple_backtest | paper
    mode: simulated              # nautilus_backtest only: "simulated" | "nautilus_native"
    quantity: 1.0
    sell_means: flat             # "flat" -> PositionIntent(FLAT); "short" -> SELL order
    allow_short: false           # simulated mode: permit short positions
    price_field: close           # simulated mode: event attribute used as fill price
  ```

### Nautilus Trader is an optional execution/backtest backend

The `nautilus_backtest` backend provides two fill sources behind one report
shape (see [`docs/nautilus_backtest_backend.md`](../docs/nautilus_backtest_backend.md)):

- **`mode="simulated"`** (default) — a dependency-free reference fill model
  (`IntentFillSimulator`): average-price positions, realized & unrealized PnL.
  No Nautilus required.
- **`mode="nautilus_native"`** — a **real** Nautilus `BacktestEngine` run via the
  lazy `strategy_framework/backends/nautilus_native.py` adapter. Internal bars
  become Nautilus `Bar`s, pre-computed intents are replayed as market orders by a
  thin `Strategy`, and `OrderFilled` events become `FillRecord`s. Requires the
  `nautilus_trader` package (built on the backtest server); when it is absent the
  backend raises a clear `NautilusUnavailableError` — **not** a placeholder
  `NotImplementedError`.

Both modes feed the same dependency-free report writer
(`strategy_framework/execution/backtest_report.py`), so the artifact set
(`signals/intents/trades/positions/equity_curve/metrics.json/report.md`) is
identical regardless of which engine produced the fills.

All `nautilus_trader` imports are **lazy and confined to the native adapter** —
`feature_engine`, `data_engine`, `strategies`, and the rest of
`strategy_framework` never import Nautilus. Run it with:

```bash
python run_strategy.py --config configs/backtests/ma_crossover_nautilus_synthetic.yaml
```

**Native MVP scope:** single instrument (Binance spot pairs mapped in the
adapter), market orders, one bar type; `sell_means="flat"` closes the long.
Multi-instrument, commission/slippage models, and live execution are future
work.

## Legacy

The old `feature_strategies/` package has been **removed**. Its entry point,
registry, data loaders, output, backtest recorder, and live sources now live at
the top level (`run_strategy.py` + `strategy_framework/`), and the strategy +
configs live in `strategies/ma_crossover/`. The only retained legacy shim is
`scripts/run_ma_crossover_demo.py`, which forwards to the top-level runner.
