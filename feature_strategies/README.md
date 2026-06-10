# feature_strategies — user-facing strategy layer

This is where **strategy authors work**. Every strategy shares **one** runner;
you never write a `run_xxx.py` per strategy. The heavy lifting (rolling-mean
maths, backends, watermarks) lives under `nautilus_ext/features/compute/` and you
normally never touch it.

## Layout

```
feature_strategies/
├── README.md                  ← this file
├── run_strategy.py            ← the ONE shared executor (coordination only)
├── data_loaders.py            ← event source selection (synthetic / csv_bars / live_synthetic)
├── live_sources.py            ← LiveEventSource protocol for real feeds (skeleton)
├── output.py                  ← table formatting / printing
├── backtest.py                ← SignalRecorder (traceability; no PnL)
├── registry.py                ← explicit name → strategy mapping
├── configs/
│   ├── ma_crossover.yaml             ← synthetic demo
│   ├── ma_crossover_backtest.yaml    ← csv_bars historical replay
│   └── ma_crossover_live_synthetic.yaml ← live streaming skeleton
├── sample_data/
│   └── ma_crossover_bars.csv   ← tiny deterministic CSV for the backtest config
└── strategies/
    ├── __init__.py
    └── ma_crossover.py        ← a strategy: config + build_specs + signal logic
```

## The layers

| Layer | Location | You edit it? |
|-------|----------|--------------|
| **Strategy** | `feature_strategies/strategies/<name>.py` | **Yes** — your code |
| **Registry** | `feature_strategies/registry.py` | yes — one line per strategy |
| **Config** | `feature_strategies/configs/<name>.yaml` | yes — choose parameters |
| **Shared executor** | `feature_strategies/run_strategy.py` | no — coordination only |
| **Data loaders** | `feature_strategies/data_loaders.py` | only to add a data source |
| **Live sources** | `feature_strategies/live_sources.py` | only to add a real live feed |
| **Output** | `feature_strategies/output.py` | only to change display |
| **Backtest recorder** | `feature_strategies/backtest.py` | only to extend traceability/metrics |
| **Public API** | `nautilus_ext/features/api.py` | no — stable import surface |
| **Execution helper** | `nautilus_ext/features/runner.py` | no — `FeatureStrategyRunner` |
| **Demo data** | `nautilus_ext/features/examples/synthetic_bars.py` | for demos/tests |
| **Compute engine** | `nautilus_ext/features/compute/` | **only to add a new operator** |

`run_strategy.py` only *coordinates*: load config → registry lookup → build
strategy + runner → `load_events()` → run → hand each row to `output`. Data
construction lives in `data_loaders.py`; all printing lives in `output.py`.

## Running — three execution modes

The same shared runner drives all three; only the config's `data.mode` differs.

```bash
# 1. Synthetic demo (generated price path)
python -m feature_strategies.run_strategy --config feature_strategies/configs/ma_crossover.yaml

# 2. Historical / backtest-style replay from a local CSV
python -m feature_strategies.run_strategy --config feature_strategies/configs/ma_crossover_backtest.yaml

# 3. Live/paper-style streaming skeleton (no real exchange)
python -m feature_strategies.run_strategy --config feature_strategies/configs/ma_crossover_live_synthetic.yaml
```

Or by registered name (synthetic defaults): `python -m feature_strategies.run_strategy --strategy ma_crossover`.

The historical entry point `python -m scripts.run_ma_crossover_demo` still works
— a thin wrapper that calls `feature_strategies.run_strategy.main`.

### `data.mode` options

| Mode | Purpose | Live events | Key config |
|------|---------|-------------|------------|
| `synthetic` | generated demo path | list | `warmup_bars`, `live_bars` |
| `csv_bars` | historical replay (stdlib `csv`, no pandas) | list | `path`, `warmup_bars`, `timestamp_column`, `timestamp_unit` (ns/us/ms/s), `*_column` |
| `live_synthetic` | streaming skeleton | generator | `warmup_bars`, `live_bars`, `delay_seconds` |

`csv_bars` reads one series, sorts by event time **once** in the loader, then
splits the first `warmup_bars` rows as warmup. Missing O/H/L default to `close`,
missing volume to `0.0`, and a missing timestamp column produces monotonic
1-second timestamps. `live_synthetic` returns the live events as a generator (a
stand-in for a real feed — see `live_sources.py`).

### Optional signal recording

Set `output.record_signals: true` in a config to capture each signal during the
run and print a summary (`signal counts: BUY=… SELL=… HOLD=…`). The recorder
(`feature_strategies/backtest.py`) is traceability only — no PnL yet.

## Adding a new strategy (3 steps, no new run script)

**Checklist** — an ordinary strategy author only touches three files:

1. Create `feature_strategies/strategies/<name>.py`, defining:
   - `<Name>Config`
   - `build_specs(config)`
   - `<Name>Strategy.on_snapshot(snapshot)`
2. Add `feature_strategies/configs/<name>.yaml`.
3. Register it in `feature_strategies/registry.py`.
4. Run it through the shared script:
   ```bash
   python -m feature_strategies.run_strategy --config feature_strategies/configs/<name>.yaml
   ```

Do **not** create a new run script unless the strategy needs a special
nonstandard harness. You never edit `run_strategy.py`, `data_loaders.py`,
`output.py`, or anything under `nautilus_ext/features/`.

Worked example:

**1. Write the strategy** — `feature_strategies/strategies/my_strat.py`:

```python
from dataclasses import dataclass
from nautilus_ext.features.api import FeatureSnapshot, FeatureSpec, rolling_mean_spec

@dataclass(frozen=True)
class MyStratConfig:
    window: int = 10
    name: str = "ma_close"

def build_specs(config: MyStratConfig) -> list[FeatureSpec]:
    # rolling_mean_spec hides the params={"type": ...} backend plumbing.
    return [rolling_mean_spec(config.name, input_type="bar",
                              input_field="close", window=config.window)]

class MyStrat:
    def __init__(self, config: MyStratConfig) -> None:
        self._config = config
    def on_snapshot(self, snapshot: FeatureSnapshot) -> str:
        value = snapshot.value(self._config.name)   # public API only
        return "BUY" if value and value > 100 else "HOLD"
```

**2. Register it** — add one entry to `feature_strategies/registry.py`:

```python
from feature_strategies.strategies import my_strat
STRATEGY_REGISTRY = {
    ...,
    "my_strat": StrategyEntry(my_strat.MyStratConfig, my_strat.MyStrat, my_strat.build_specs),
}
```

**3. Add a config** — `feature_strategies/configs/my_strat.yaml`:

```yaml
strategy: my_strat
params: { window: 10 }
data: { mode: synthetic, warmup_bars: 20, live_bars: 20 }
output: { print_table: true }
```

Then `python -m feature_strategies.run_strategy --config feature_strategies/configs/my_strat.yaml`.

## Rules a strategy follows

- Read features only through `snapshot.value(name)` / `snapshot.is_ready(name)`.
- Keep your own state (e.g. previous values) inside the strategy object.
- Import only from `nautilus_ext.features.api`. **Do not** create a
  `SpecFeatureEngine`, parse CLI args, load data, run loops, print, or import
  `compute/*`. The shared runner does all of that.

## Where each kind of change goes

| You want to… | Edit only |
|--------------|-----------|
| Add a new **strategy** | `strategies/<name>.py`, `configs/<name>.yaml`, `registry.py` |
| Add a new **historical data source** | `data_loaders.py` (register a `mode`), optional helper module |
| Add a **real live source** later | `live_sources.py` (implement `LiveEventSource`), register a `mode` in `data_loaders.py`, a config |
| Add a new low-level **feature operator** | `compute/features.py`, `compute/backend.py`, `builders.py`, `api.py`, + compute tests |

## When *do* you edit the compute layer?

Only when you need a brand-new low-level **feature operator** that doesn't exist
yet (e.g. a new rolling statistic): add it to
`nautilus_ext/features/compute/features.py` and register it in
`nautilus_ext/features/compute/backend.py`. Composing *existing* operators into a
new strategy never requires this.
