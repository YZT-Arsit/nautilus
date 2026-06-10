# MA5 / MA20 Moving-Average Crossover Strategy Demo

> **Audience**: engineering reviewers, quant PMs.
> **Purpose**: validate the Feature Engine's warmup path, incremental updates, and public API surface using a concrete, easily-reasoned strategy.

---

## 0. File Layout

The code is split into layers so each file has one job. The **user-facing
entry point and strategies live at the top of the repository**; framework glue
sits in `strategy_framework/`; the low-level engine stays in
`nautilus_ext/features/compute/`.

| File | Responsibility | Who edits it |
|------|----------------|--------------|
| `run_strategy.py` (repo root) | **The** shared user entry point — coordination only | no — shared |
| `strategies/ma_crossover/strategy.py` | Strategy logic: config, `build_specs`, signal rule, `PLUGIN` | **Strategy authors** |
| `strategies/ma_crossover/config.yaml` | Strategy parameters + data + output | strategy authors |
| `strategies/ma_crossover/README.md` | Per-strategy notes | strategy authors |
| `strategy_framework/registry.py` | Explicit name → `StrategyPlugin` mapping | yes — one line per strategy |
| `strategy_framework/plugin.py` | `StrategyPlugin` descriptor | no |
| `strategy_framework/output.py` | Table formatting / printing / signal summary | only to change display |
| `strategy_framework/backtest.py` | `SignalRecorder` — signal traceability (no PnL) | only to extend metrics |
| `strategy_framework/data_loaders.py` | **Compatibility wrapper** → re-exports `market_data_engine` | no |
| `market_data_engine/` | **Canonical data layer** — `BarEvent`, `load_events`, sources (`synthetic`/`csv_bars`/`live_synthetic`), adapters | only to add a data source |
| `strategies/ma_crossover/sample_data/ma_crossover_bars.csv` | Tiny CSV for the backtest config | demo / test authors |
| `nautilus_ext/features/api.py` | **Stable public API** facade (`FeatureSpec`, `FeatureSnapshot`, `rolling_mean_spec`, …) | no — import from it |
| `nautilus_ext/features/runner.py` | `FeatureStrategyRunner` — builds the engine + runs the loop | no |
| `nautilus_ext/features/compute/features.py` | Low-level feature **operator library** (rolling-mean, etc.) | **compute owners only** |
| `scripts/run_ma_crossover_demo.py` | Legacy wrapper → top-level `run_strategy.main` | no |

There is **one shared run script** for every strategy — you do not add a
`run_xxx.py` per strategy. The layers:

1. **Entry point** (`run_strategy.py`, repo root) — *coordination only*: select a
   strategy plugin from the registry via `--config`/`--strategy`, build specs +
   runner, get events from `data_loaders`, run warmup + the live loop, hand each
   row to `output`, optionally record signals. No data construction, no
   formatting, no event-shape assumptions.
2. **Strategy layer** (`strategies/<name>/`) — *what* to trade. Each strategy is
   a package with `strategy.py` (config + `build_specs` + signal logic + `PLUGIN`)
   and its `config.yaml`. User-facing.
3. **Registry** (`strategy_framework/registry.py`) — maps a strategy name to its
   `StrategyPlugin`. Explicit, no auto-discovery.
4. **Data layer** (`market_data_engine/`) — `load_events(data)` returns
   `(warmup_events, live_events)` for the configured `data.mode` (`synthetic`,
   `csv_bars`, `live_synthetic`). This is our **own** design; it does **not** use
   Nautilus Trader's native data system. New sources plug in here.
   `strategy_framework/data_loaders.py` is only a thin re-export wrapper.
5. **Output** (`strategy_framework/output.py`) — warmup summary, table, signal
   summary. Event access is defensive (missing `close` / `event_time_ns` → `-`).
6. **Public API** (`nautilus_ext/features/api.py`) — the stable import surface.
   Strategies do `from nautilus_ext.features.api import FeatureSpec, FeatureSnapshot, rolling_mean_spec`,
   never the deep `compute.*` paths.
7. **Execution helper** (`nautilus_ext/features/runner.py`) —
   `FeatureStrategyRunner(specs, strategy)` builds a `SpecFeatureEngine` and runs
   the warmup / per-event loop, yielding `(event, snapshot, signal)`.
8. **Compute layer** (`nautilus_ext/features/compute/`) — *how* features are
   computed. A **library**. You touch `compute/features.py` (and
   `compute/backend.py`) only to add a genuinely new low-level *operator*.

Canonical flow:

```
config.yaml
   ↓
market_data_engine.load_events()        # data layer (our own design)
   ↓
warmup_events / live_events
   ↓
FeatureStrategyRunner                    # nautilus_ext/features/runner.py
   ↓
FeatureEngine / FeatureSnapshot          # nautilus_ext/features/compute/
   ↓
Strategy.on_snapshot()                   # strategies/<name>/strategy.py
   ↓
BUY / SELL / HOLD
```

**Adding a strategy** = three small edits, no new run script: a package under
`strategies/<name>/` (strategy.py + config.yaml + README.md), one line in
`strategy_framework/registry.py`. See `strategies/ma_crossover/README.md` and
`strategy_framework/README.md`.

Key boundary: the strategy reads features **by name** through the public
`FeatureSnapshot` API (`snapshot.value("ma5_close")`). It does **not** import
`features.py`, `state.py`, or any engine/backend internals — a test
(`test_strategy_imports_only_public_api`) enforces this.

To change the strategy (different windows, new signal rule, an extra feature),
edit **`strategies/ma_crossover/strategy.py`** only.

---

## 1. What Is MA5 / MA20?

A **moving average** (MA) smooths price by averaging the last `N` closing prices.

- **MA5** — 5-bar simple moving average: the mean of the last 5 bar closes.
- **MA20** — 20-bar simple moving average: the mean of the last 20 bar closes.

The **MA crossover** strategy generates signals when the faster average (MA5) changes direction relative to the slower one (MA20):

| Condition | Signal |
|-----------|--------|
| MA5 crosses **above** MA20 | **BUY** — short-term momentum turning bullish |
| MA5 crosses **below** MA20 | **SELL** — short-term momentum turning bearish |
| No crossover | **HOLD** |

The crossover is detected by comparing the **previous** and **current** values of each moving average:

```
BUY  if prev_ma5 ≤ prev_ma20  AND  curr_ma5 > curr_ma20
SELL if prev_ma5 ≥ prev_ma20  AND  curr_ma5 < curr_ma20
HOLD otherwise
```

---

## 2. How MA5 Maps to `rolling_mean`

Both MAs are configured as `rolling_mean` features, built by `build_specs()` in
the strategy module via the `rolling_mean_spec` helper (which hides the
`params={"type": ...}` backend plumbing). Feature names are derived from the
config — `f"ma{window}_{input_field}"` — so they stay in sync with the windows:

```python
# strategies/ma_crossover/strategy.py
from nautilus_ext.features.api import rolling_mean_spec

def build_specs(config: MovingAverageCrossoverConfig) -> list[FeatureSpec]:
    kw = {"input_type": config.input_type, "input_field": config.input_field}
    return [
        rolling_mean_spec(config.fast_name, window=config.fast_window, **kw),  # "ma5_close",  window 5
        rolling_mean_spec(config.slow_name, window=config.slow_window, **kw),  # "ma20_close", window 20
    ]
```

`rolling_mean` maps to `RollingMeanFeature`, which maintains a `RollingWindowState` — a fixed-size ring buffer with a running sum. Each `on_event()` call:

1. Pushes the new `close` value into the ring buffer.
2. Evicts the oldest value when the buffer is full.
3. Returns `sum / count` in **O(1)** — no loop over history.

`ma5_close` and `ma20_close` are the only names the strategy layer sees. The computation backend is invisible.

---

## 3. Why This Validates Warmup and Incremental Feature Update

### Warmup validates state pre-heating

```
engine.warmup(historical_bars)   ←  advances watermarks; fills ring buffer; no process_time stamp
engine.on_event(live_bar)        ←  O(1) update; state continues from warmup
```

A test verifies that:

```
warmup(first_20_bars) + on_event(remaining_bars) == on_event(all_bars)
```

Both paths produce identical `ma5_close` and `ma20_close` values. This proves warmup and the live path share exactly the same incremental state — the ring buffer contents, running sum, and watermark are equivalent.

### Crossover tests validate incremental correctness

The BUY test feeds 20 bars at price 100 (warmup), then one bar at price 200 (live). Expected result:

```
MA5  = (100 + 100 + 100 + 100 + 200) / 5 = 120.0
MA20 = (100 × 19 + 200)             / 20 = 104.75   (wait actually 14.5...)
```

Wait — let me show the actual numbers:
```
After warmup (20 bars at 100):  MA5 = 100.0, MA20 = 100.0
Bar 21 (close = 200):
  MA5  = (100 + 100 + 100 + 100 + 200) / 5  = 120.0
  MA20 = (100 × 19 + 200)              / 20 = 104.75

prev_ma5 (100) ≤ prev_ma20 (100)  AND  curr_ma5 (120) > curr_ma20 (104.75)  →  BUY ✓
```

The correctness of MA5 and MA20 against a pure-Python reference average is verified by `test_ma5_value_matches_reference` and `test_ma20_value_matches_reference`.

---

## 4. Why This Avoids Full-History Recomputation

`RollingMeanFeature` never stores more than `window` values. After bar `N`:

| State | Size |
|-------|------|
| Ring buffer | exactly `min(N, window)` values |
| Running sum | 1 scalar |
| Count | 1 integer |

To compute MA5 on bar `N+1`, the engine:
1. Pops the oldest value from the ring buffer (the value from bar `N - 5`).
2. Subtracts it from the running sum.
3. Pushes the new close, adds it to the running sum.
4. Returns `running_sum / 5`.

**Zero lookback into history.** A 10,000-bar warmup followed by one live bar costs the same as a 5-bar warmup — **O(1) per event**, regardless of history length.

The test `test_require_no_full_history_recomputation` (in the main feature test suite) confirms that the ring buffer never exceeds `window` entries.

---

## 5. Architecture

```
Bar event (bar.close)
       │
       ▼
Phase 1: RollingMeanFeature.update(event)
  ├── ring_buffer.push(event.close)          ← O(1), evicts oldest
  ├── running_sum += new; running_sum -= old ← O(1)
  └── returns FeatureValue(value=sum/count)
       │
       ▼ (no Phase 2: these are raw features, no derived chain)
       │
FeatureSnapshot
  ├── ma5_close  → FeatureValue(value=..., is_ready=True/False)
  └── ma20_close → FeatureValue(value=..., is_ready=True/False)
       │
       ▼
MovingAverageCrossoverStrategy.on_snapshot(snapshot)   (uses only public API)
  ├── snapshot.value("ma5_close")    → float | None
  ├── snapshot.value("ma20_close")   → float | None
  ├── keeps prev fast/slow internally
  └── returns "BUY" / "SELL" / "HOLD"
```

The strategy holds the previous fast/slow values internally, so a crossover is
detected from two consecutive **ready** snapshots. The first ready snapshot
seeds the previous values and therefore returns HOLD.

---

## 6. Usage

The same shared runner supports **three execution modes**, chosen by the
config's `data.mode`:

```bash
# 1. Synthetic demo (generated price path)
python run_strategy.py --config strategies/ma_crossover/config.yaml

# 2. Historical / backtest-style replay from a local CSV (stdlib csv, no pandas)
python run_strategy.py --config strategies/ma_crossover/config_backtest.yaml

# 3. Live/paper-style streaming skeleton (live events are a generator; no real feed)
python run_strategy.py --config strategies/ma_crossover/config_live_synthetic.yaml

# Run by registered name (config defaults + synthetic data)
python run_strategy.py --strategy ma_crossover

# Legacy entry point (thin wrapper, still works)
python scripts/run_ma_crossover_demo.py
```

Parameters (windows, warmup/live bar counts, instrument, data source) live in
the YAML config, not in CLI flags — every strategy is driven the same way. With
`output.record_signals: true`, the run also prints a `signal counts: …` summary
via the dependency-free `strategy_framework/backtest.py` recorder (traceability
only — no PnL).

Example output (default parameters):

```
[ma_crossover] warmed up on 20 bars; ready: {ma5_close=True, ma20_close=True}

  t(s)     close   ma5_close  ma20_close  signal
------------------------------------------------
    20    100.00    100.0000    100.0000  HOLD
    21    110.00    102.0000    100.5000  BUY
    22    110.00    104.0000    101.0000  HOLD
    23    110.00    106.0000    101.5000  HOLD
    24    100.00    106.0000    101.5000  HOLD
    25    100.00    106.0000    101.5000  HOLD
    26    100.00    104.0000    101.5000  HOLD
    27     90.00    100.0000    101.0000  SELL
      ...
```

The table columns are derived from the strategy's spec names, so the shared
runner prints the right header for any strategy.

The first live bar (t=20) is a flat lead-in that seeds the strategy's previous
values, so the BUY fires on the next bar (t=21) when the rise begins.

---

## 7. Test Coverage

File: `nautilus_ext/tests/test_ma_crossover.py`

| Test | What it proves |
|------|---------------|
| `test_ma5_value_matches_reference` | MA5 equals a reference rolling mean over the last 5 closes |
| `test_ma20_value_matches_reference` | MA20 equals a reference rolling mean over the last 20 closes |
| `test_ma5_not_ready_before_window` | Feature not ready with fewer than `window` bars |
| `test_ma20_not_ready_before_window` | Same for MA20 |
| `test_ma5_ready_at_window` | Ready exactly at bar `window` |
| `test_ma20_ready_at_window` | Ready exactly at bar 20 |
| `test_warmup_plus_live_equals_all_on_event` | Warmup + live path is numerically identical to all-on-event replay |
| `test_warmup_advances_watermark` | Watermark is correctly advanced by warmup events |
| `test_buy_signal_on_upward_crossover` | BUY generated when price spike causes MA5 > MA20 |
| `test_sell_signal_on_downward_crossover` | SELL generated when price drop causes MA5 < MA20 |
| `test_hold_when_no_crossover` | HOLD when MAs move together |
| `test_hold_when_not_ready` | HOLD returned when any MA value is None |
| `test_sequential_crossovers_detected` | BUY appears before SELL in a spike-then-drop price series |
| `test_strategy_uses_only_public_api` | All values accessed via `snap.value()`, `snap.is_ready()`, `engine.value()`, `engine.is_ready()` |
| `test_engine_value_returns_none_before_ready` | `engine.value()` returns None before the window is filled |

### Refactored strategy-layer tests

| Test | What it proves |
|------|---------------|
| `test_build_specs_returns_exactly_two_rolling_mean_specs` | `build_ma_crossover_specs()` returns exactly two `rolling_mean` specs |
| `test_build_specs_honours_custom_config` | Custom fast/slow windows flow through to the specs |
| `test_strategy_emits_buy_on_upward_crossover` | `MovingAverageCrossoverStrategy` returns BUY on an upward crossover |
| `test_strategy_emits_sell_on_downward_crossover` | Returns SELL on a downward crossover |
| `test_strategy_emits_hold_when_not_ready` | Returns HOLD while features are not ready |
| `test_strategy_first_ready_snapshot_is_hold` | First ready snapshot seeds prev values → HOLD |
| `test_strategy_uses_only_snapshot_public_api` | Strategy works against an object exposing only `value()` — no internals |
| `test_make_bars_shapes_and_spacing` | `make_bars()` produces correctly spaced `BarEvent`s |
| `test_make_bars_feed_engine` | Synthetic bars drive the engine to a ready MA |
| `test_on_event_returns_snapshot_and_signal` | `FeatureStrategyRunner.on_event()` returns `(snapshot, signal)` |
| `test_run_yields_event_snapshot_signal_in_order` | `runner.run()` yields `(event, snapshot, signal)` in order |
| `test_runner_matches_direct_calls` | Runner output equals driving engine + strategy by hand |
| `test_health_summary_delegates_to_engine` | `runner.health_summary()` returns the engine's diagnostic dict |
| `test_registry_contains_ma_crossover` | `STRATEGY_REGISTRY` has the `ma_crossover` entry |
| `test_entry_wires_config_strategy_and_build_specs` | The registry entry wires config/strategy/build_specs correctly |
| `test_unknown_strategy_raises_helpful_error` | `get_entry()` raises a listing error for unknown names |
| `test_strategy_imports_cleanly_from_top_level` | `strategies.ma_crossover` imports without error |
| `test_strategy_imports_only_public_api` | Strategy source imports `features.api`, never `features.compute.*` |
| `test_build_specs_returns_two_rolling_mean_specs` | `build_specs()` returns two `rolling_mean` `FeatureSpec`s |
| `test_value_and_is_ready_delegate_to_engine` | `runner.value()` / `runner.is_ready()` reflect engine state |
| `test_run_strategy_with_config` | `run_strategy.main(--config …)` runs ma_crossover and prints BUY + SELL |
| `test_run_strategy_with_strategy_flag_only` | `run_strategy.main(--strategy ma_crossover)` runs from defaults |
| `test_run_strategy_requires_a_strategy` | `run_strategy.main([])` exits without a strategy |
| `test_legacy_wrapper_still_works` | `scripts/run_ma_crossover_demo.py` forwards to the shared runner |
| `test_rolling_mean_spec_sets_params_type` | `rolling_mean_spec()` builds a `rolling_mean` spec with the right fields |
| `test_strategy_uses_rolling_mean_spec_not_raw_params` | Strategy uses the builder, not hand-written `params={...}` |
| `test_load_synthetic_bars_returns_warmup_and_live` | `load_synthetic_bars()` returns correctly sized warmup/live bars |
| `test_load_events_synthetic_mode` / `_defaults_to_synthetic` | `load_events()` dispatches the synthetic source |
| `test_load_events_unsupported_mode_raises` | Unknown `data.mode` raises a clear error |
| `test_row_with_close_and_time` | `output.print_event_row` renders close + time for bar events |
| `test_row_without_close_or_time` | `output.print_event_row` renders `-` for events lacking those fields |
| `test_warmup_summary_and_header` | `output` prints the warmup summary and table header |

---

## 8. Constraints Preserved

| Constraint | Status |
|------------|--------|
| No pandas in hot path | ✓ `RollingWindowState` (deque + running sum) |
| No full-history recomputation | ✓ O(1) push; ring buffer never exceeds `window` entries |
| No sorting in `on_event()` | ✓ Topo order pre-computed at construction; this demo has no derived features |
| No backend internals access | ✓ `strategies/ma_crossover/strategy.py` imports only from `nautilus_ext.features.api`; no `features.py`, `state.py`, or engine internals. `test_strategy_imports_only_public_api` + `test_strategy_uses_only_snapshot_public_api` enforce it. |
| No expression parser | ✓ Feature type resolved by `params["type"]` dict lookup |

---

*Entry: `run_strategy.py` · strategy: `strategies/ma_crossover/strategy.py` · config: `strategies/ma_crossover/config.yaml` · registry: `strategy_framework/registry.py` · data loaders: `strategy_framework/data_loaders.py` · output: `strategy_framework/output.py` · public API: `nautilus_ext/features/api.py` · execution helper: `nautilus_ext/features/runner.py`. See also `strategies/ma_crossover/README.md` and `strategy_framework/README.md`.*
