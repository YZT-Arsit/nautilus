# feature_strategies — user-facing strategy layer

This is where **strategy authors work**. Every strategy shares **one** runner;
you never write a `run_xxx.py` per strategy. The heavy lifting (rolling-mean
maths, backends, watermarks) lives under `nautilus_ext/features/compute/` and you
normally never touch it.

## Layout

```
feature_strategies/
├── README.md                  ← this file
├── run_strategy.py            ← the ONE shared runner for every strategy
├── registry.py                ← explicit name → strategy mapping
├── configs/
│   └── ma_crossover.yaml       ← per-strategy parameters
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
| **Shared runner** | `feature_strategies/run_strategy.py` | no — shared by all strategies |
| **Public API** | `nautilus_ext/features/api.py` | no — stable import surface |
| **Execution helper** | `nautilus_ext/features/runner.py` | no — `FeatureStrategyRunner` |
| **Demo data** | `nautilus_ext/features/examples/synthetic_bars.py` | for demos/tests |
| **Compute engine** | `nautilus_ext/features/compute/` | **only to add a new operator** |

## Running

```bash
# Pick a strategy by config file
python -m feature_strategies.run_strategy --config feature_strategies/configs/ma_crossover.yaml

# Or by registered name (uses config defaults + synthetic data)
python -m feature_strategies.run_strategy --strategy ma_crossover
```

The historical entry point `python -m scripts.run_ma_crossover_demo` still works
— it's now a thin wrapper that calls `feature_strategies.run_strategy.main` with
the MA crossover config.

## Adding a new strategy (3 steps, no new run script)

**1. Write the strategy** — `feature_strategies/strategies/my_strat.py`:

```python
from dataclasses import dataclass
from nautilus_ext.features.api import FeatureSnapshot, FeatureSpec

@dataclass(frozen=True)
class MyStratConfig:
    window: int = 10
    name: str = "ma_close"

def build_specs(config: MyStratConfig) -> list[FeatureSpec]:
    return [FeatureSpec(config.name, input_type="bar", input_field="close",
                        window=config.window, params={"type": "rolling_mean"})]

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

## When *do* you edit the compute layer?

Only when you need a brand-new low-level **feature operator** that doesn't exist
yet (e.g. a new rolling statistic): add it to
`nautilus_ext/features/compute/features.py` and register it in
`nautilus_ext/features/compute/backend.py`. Composing *existing* operators into a
new strategy never requires this.
