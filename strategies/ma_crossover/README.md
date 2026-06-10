# MA crossover strategy

A two-line moving-average crossover on bar closes.

## What it does

- **Features:** two `rolling_mean` features — a fast MA (`fast_window`, default 5)
  and a slow MA (`slow_window`, default 20) over `close`.
- **Signal rule** (`crossover_signal`):
  - **BUY** when the fast MA crosses *above* the slow MA
    (`prev_fast ≤ prev_slow AND fast > slow`).
  - **SELL** when it crosses *below* (`prev_fast ≥ prev_slow AND fast < slow`).
  - **HOLD** otherwise, or until both MAs are ready.
- State: the strategy keeps the previous fast/slow values; a crossover needs two
  consecutive ready snapshots, so the first ready snapshot is always HOLD.

## Files

| File | Purpose |
|------|---------|
| `strategy.py` | `MovingAverageCrossoverConfig`, `build_specs`, `crossover_signal`, `MovingAverageCrossoverStrategy`, and `PLUGIN` |
| `config.yaml` | default (synthetic) run config |
| `config_backtest.yaml` | `csv_bars` historical replay over `sample_data/ma_crossover_bars.csv` |
| `config_live_synthetic.yaml` | `live_synthetic` streaming skeleton |
| `sample_data/ma_crossover_bars.csv` | tiny deterministic bars for the backtest config |

## How to run

```bash
# By registered name (loads this folder's config.yaml)
python run_strategy.py --strategy ma_crossover

# By explicit config
python run_strategy.py --config strategies/ma_crossover/config.yaml

# Historical replay / live skeleton
python run_strategy.py --config strategies/ma_crossover/config_backtest.yaml
python run_strategy.py --config strategies/ma_crossover/config_live_synthetic.yaml
```

The strategy imports only the public API
(`feature_engine.api`) and `strategy_framework.plugin` — never the
low-level compute engine.
