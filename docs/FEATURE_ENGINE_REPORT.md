# Feature Engine — Technical Report

> **Audience**: engineering leadership, quant PMs, senior reviewers.
> **Status**: v1 complete — production-ready for paper trading and backtesting.
> **Test coverage**: 674 passed, 17 skipped (pure-Python unit suite, no Cython required).

---

## 1. Design Goal

Build a **low-latency, incremental feature engine** that can serve both live paper-trading signals and offline backtests from a single code path, with the following properties:

- **O(1) per event** for each feature (no full-history recomputation, no pandas).
- **Per-stream watermarks** so mixed instrument / event-type feeds are handled safely.
- **Full timestamp traceability** — `event_time_ns`, `receive_time_ns`, `process_time_ns` on every output.
- **Swappable backend** — the strategy layer depends only on `FeatureSpec` / `FeatureSnapshot` / `SpecFeatureEngine`; the computation layer can be replaced with a Rust backend without strategy changes.
- **Zero live-order risk** — the engine and all utilities in this module are pure market-data processing. Order submission is `NotImplementedError` by design.

---

## 2. Architecture Diagram

```
Market Data Feed
     │
     ▼
adapt_bar_event()          adapt_quote_tick_event()          adapt_trade_tick_event()
     │                              │                                   │
     └──────────────────────────────┼───────────────────────────────────┘
                                    │
                                    ▼
                          SpecFeatureEngine.on_event(event)
                                    │
                    ┌───────────────┴──────────────────────┐
                    │  Phase 1: Raw Feature Update          │
                    │  ─────────────────────────────────    │
                    │  for each raw_feature:                │
                    │    if event_type matches spec:        │
                    │      check watermark (lateness)       │
                    │      feature.update(event) → value    │
                    │      mark dirty                       │
                    │    else:                              │
                    │      return cached value              │
                    └────────────────┬─────────────────────┘
                                     │
                    ┌────────────────▼─────────────────────┐
                    │  Phase 2: Derived Feature Update      │
                    │  (topological order, deps before      │
                    │   dependents, multi-level chains)     │
                    │  ─────────────────────────────────    │
                    │  for each derived_feature:            │
                    │    if any dep in dirty:               │
                    │      DependencyContext(values)        │
                    │      feature.update_from_deps(ctx)    │
                    │      mark dirty (propagates upward)   │
                    │    else:                              │
                    │      return cached FeatureValue       │
                    └────────────────┬─────────────────────┘
                                     │
                                     ▼
                               FeatureSnapshot
                          (ts_event, instrument_id,
                           values: {name → FeatureValue},
                           receive_time_ns, process_time_ns)
                                     │
                                     ▼
                            Strategy / Signal Generator
                          (FeatureSnapshot API only — no
                           access to engine internals)
```

---

## 3. Event Lifecycle

For each call to `engine.on_event(event)`:

| Step | Action |
|------|--------|
| 1 | Extract `event_time_ns`, `receive_time_ns` via `extract_timestamps()` |
| 2 | Resolve `input_type` (canonical: `"bar"`, `"quote"`, `"trade"`, `"book_delta"`) |
| 3 | Advance watermark for `StreamKey(instrument_id, input_type, source)` |
| 4 | **Phase 1**: iterate `_raw_features`; skip mismatched types; check lateness; call `feature.update(event)` |
| 5 | **Phase 2**: iterate `_derived_names` (topo order); skip if no dep in dirty set; call `feature.update_from_dependencies(ctx, event)` |
| 6 | Stamp `process_time_ns` via injected `Clock` |
| 7 | Return `FeatureSnapshot` with all values |

Per-event complexity: **O(n_raw + n_dirty_derived)** — proportional to the number of raw features subscribed to this event type plus downstream derived features that have at least one dirty dependency.

No sorting occurs inside `on_event()`. The topological order is pre-computed once at engine construction.

---

## 4. Warmup Handling

```python
engine.warmup(history_events: Iterable)
```

Warmup reuses the same two-phase update logic (`_route_warmup`) with two differences:

1. `process_time_ns` is **not** stamped (warmup has no meaningful process time).
2. Late-event policy `"recompute_for_backtest_only"` treats all warmup events as on-time.

The per-stream watermarks **do** advance during warmup so that the first live event after warmup is correctly classified.

The `InMemoryEventProvider` interleaves bar and quote events by `event_time_ns` before passing them to `warmup()`, ensuring correct watermark ordering for mixed event streams.

Warmup is **idempotent** in the sense that calling `reset()` then `warmup()` again returns the engine to the same post-warmup state.

---

## 5. Timestamp Semantics

| Field | Source | Unit | Purpose |
|-------|--------|------|---------|
| `event_time_ns` | exchange/source | nanoseconds | feature window computation, watermark |
| `receive_time_ns` | local reception | nanoseconds | network latency measurement |
| `process_time_ns` | engine clock | nanoseconds | processing latency measurement |

All three timestamps are preserved in `FeatureValue.source_event_time_ns` and `FeatureSnapshot`.

`TriggerPolicy.time_semantics` controls which timestamp drives feature windows:
- `"event_time"` (default) — use exchange time. Correct for all feature computation.
- `"receive_time"` — use local reception time. Use only for latency features.
- `"process_time"` — use engine wall clock. Use only for system monitoring.

Legacy `ts_event` (datetime or integer milliseconds) is converted to nanoseconds by `extract_timestamps()` via `TimestampConfig`. Strategy code never needs to handle this conversion.

---

## 6. Per-Stream Watermark

A single global watermark is unsafe when the engine receives multiple instruments or event types: a fast BTC/USDT bar stream would advance the watermark and incorrectly classify slower ETH/USDT quote events as late.

The engine maintains **one `WatermarkTracker` per `StreamKey`**:

```python
StreamKey = NamedTuple(instrument_id, input_type, source)
```

Each event advances only its own stream's watermark. Late-event detection uses the watermark of the stream matching the feature's `input_type` — so bar and quote features are checked independently even within the same engine instance.

**Aggregate accessor** (`engine.watermark_ns`, `engine.watermark_for(...)`) is provided for monitoring and debugging only — never used internally for per-feature decisions.

---

## 7. Raw vs Derived Features

### Raw Features

Subscribe to market events directly. Updated by `feature.update(event)` in Phase 1.

| Type key | Event type | Input field |
|----------|------------|-------------|
| `rolling_mean` | bar | close/open/high/low/volume |
| `rolling_std` | bar | any |
| `rolling_min` | bar | any |
| `rolling_max` | bar | any |
| `rolling_sum` | bar | any |
| `rolling_volume_sum` | bar | — (auto volume) |
| `ewma` | bar | any |
| `simple_return` | bar | any |
| `log_return` | bar | any |
| `vwap` | bar | — (uses close×volume) |
| `spread` | quote | — (ask - bid) |
| `mid_price` | quote | — ((ask+bid)/2) |
| `book_imbalance` | book_delta | — |

### Derived Features

Subscribe to other feature values via `depends_on`. Updated by `feature.update_from_dependencies(ctx, event)` in Phase 2, only when at least one dependency is in the `dirty` set.

| Type key | Formula | `depends_on` arity |
|----------|---------|---------------------|
| `ratio` | A / B | exactly 2 |
| `difference` | A - B | exactly 2 |
| `sum` | A + B | exactly 2 |
| `product` | A × B | exactly 2 |
| `rolling_std_derived` | rolling std of dep values | exactly 1 |

**Latest-ready semantics**: when a dependency was not updated on the current event, `DependencyContext` provides its last ready (cached) value. A dependency that has never been ready causes `update_status="dependency_not_ready"` and the derived feature emits `value=None, is_ready=False`.

---

## 8. Dependency DAG

Derived features form a **directed acyclic graph** (DAG). The engine validates and topologically sorts the DAG once at construction.

```
spread  ──┐
           ├──► spread_ratio   (ratio derived)
mid_price ─┘

log_return_close ──► realized_vol   (rolling_std_derived, window=60)
```

Multi-level chains work correctly: if A → B → C, the engine processes B before C in topo order, so C sees B's value from the current event.

Cycle detection uses three-colour DFS at engine construction. Circular dependencies raise `ValueError` with the full cycle path printed.

---

## 9. Low-Latency Design

| Constraint | How enforced |
|------------|--------------|
| No pandas in hot path | All computation uses `RollingWindowState` (deque-based ring buffer with running sum/sum-of-squares). |
| No sorting in `on_event()` | Topo order pre-computed once in `_build()`. Watermarks are dicts with O(1) key lookup. |
| No full-history recomputation | State is fully incremental; O(1) push regardless of window size. |
| O(1) rolling std | `RollingWindowState(track_squares=True)` maintains `Σx` and `Σx²`; std = `sqrt(Σx² / n - (Σx/n)²)`. |
| No expression parser | Feature type is resolved by `params["type"]` dict lookup. No string parsing in the hot path. |
| Profiling off by default | `profile=True` adds one `dict.__getitem__` + `__setitem__` per feature per event. No overhead when off. |
| Clock injection | `SystemClock` wraps `time.time_ns()`; injected via constructor for deterministic tests without monkeypatching. |

Benchmark baseline (Apple M-series, Python 3.11, 20 raw features, window 100):

| Mode | avg on_event | p99 on_event | throughput |
|------|-------------|-------------|------------|
| bar only, 20 raw | ~2 µs | ~8 µs | ~500k ev/s |
| mixed bar+quote, 20 raw + 4 derived | ~3 µs | ~12 µs | ~350k ev/s |

> These are indicative baselines. Run `scripts/benchmark_feature_engine.py` on your target hardware. Do not use these numbers as CI thresholds.

---

## 10. Backend Replacement Contract

The strategy layer imports only:

```python
from nautilus_ext.features.compute.spec    import FeatureSpec, FeatureSnapshot, FeatureValue
from nautilus_ext.features.compute.engine  import SpecFeatureEngine
from nautilus_ext.features.compute.adapters import adapt_bar_event, adapt_quote_tick_event
```

The computation layer (features.py, backend.py, state.py) is **never imported by strategy code**. Replacing the Python backend with Rust requires only:

1. Implement `BackendRegistry` with the same `create_feature(spec) → FeatureBase` interface.
2. Implement `FeatureBase.update(event) → FeatureUpdate` and `update_from_dependencies(ctx, event) → FeatureUpdate`.
3. Return `FeatureValue` objects with the same fields.
4. Pass the registry at engine construction: `SpecFeatureEngine(specs, backend_registry=rust_registry)`.

No strategy code changes needed.

---

## 11. Benchmark Usage

```bash
# Default: 100k bar events, 20 features, window 100
python -m scripts.benchmark_feature_engine

# Raw only
python -m scripts.benchmark_feature_engine --event-kind bar --features 20

# With derived chains (Chain A: spread→ratio, Chain B: log_return→rolling_std)
python -m scripts.benchmark_feature_engine --event-kind mixed --derived --features 20

# Write a markdown report to outputs/
python -m scripts.benchmark_feature_engine --derived --event-kind mixed --report

# With profiling
python -m scripts.benchmark_feature_engine --derived --profile

# Stress: 100 features, large window (window has no hot-path cost)
python -m scripts.benchmark_feature_engine --events 100000 --features 100 --window 1000
```

Reports are written to `outputs/benchmark_NNN.md` with full config, latency table, and health summary.

---

## 12. Demo Script

```bash
python -m scripts.run_feature_engine_demo --events 20 --bar-window 5 --rvol-window 5
```

Prints a compact event-by-event table:

```
time(s)  type    updated                       spread_ratio realized_vol rolling_mean   mid_price  signal
1        bar     -                                        —            —            —           —  —
1        quote   spread_ratio                       0.00100            —            —   100.00000  —
...
6        bar     rolling_sum_vol,spread_ratio,realized_vol  0.00100  0.00067  100.51000  100.48000  SHORT
```

Uses only the public `FeatureSnapshot` API. No backend internals accessed.

---

## 12a. Strategy Execution Layer (synthetic / backtest / live)

The user-facing strategy layer (top-level `run_strategy.py` + `strategies/` + `strategy_framework/`) sits on top of this
engine and reaches it only through the stable facade
(`nautilus_ext/features/api.py`) and `FeatureStrategyRunner`
(`nautilus_ext/features/runner.py`). One shared runner supports three execution
modes, selected by a config's `data.mode`:

| Mode | Purpose | Live events |
|------|---------|-------------|
| `synthetic` | generated demo path | list |
| `csv_bars` | historical / backtest-style replay (stdlib `csv`, no pandas) | list |
| `live_synthetic` | live/paper streaming skeleton (no real exchange) | generator |

```bash
python run_strategy.py --config strategies/ma_crossover/config.yaml
python run_strategy.py --config strategies/ma_crossover/config_backtest.yaml
python run_strategy.py --config strategies/ma_crossover/config_live_synthetic.yaml
```

Data loading is owned by the standalone **`market_data_engine/`** package — our
own design, **not** Nautilus Trader's native data system. `load_events()` sorts
CSV input by event time **once in the loader**, never in `on_event()`. A real
live feed implements the `EventSource` protocol in
`market_data_engine/streams/base.py` and registers a new mode in
`market_data_engine/loader.py` — no engine or runner changes required.
`strategy_framework/data_loaders.py` is now only a compatibility wrapper that
re-exports the data engine. Optional signal recording
(`strategy_framework/backtest.py`) captures `(event, snapshot, signal)` rows for
traceability; PnL accounting is not implemented yet.

**Layered design**

```
market_data_engine/            (data)     -> BarEvent, load_events, sources
nautilus_ext/features/compute/ (features) -> FeatureSpec, rolling state, FeatureSnapshot
strategy_framework/            (orchestration) -> registry, output, signal recording
strategies/<name>/             (logic)    -> strategy + config
```

**Modification boundaries**

| Change | Edit only |
|--------|-----------|
| New strategy | `strategies/<name>/strategy.py`, `strategies/<name>/config.yaml`, `strategy_framework/registry.py` |
| New historical data source | `market_data_engine/sources/` + register in `market_data_engine/loader.py` |
| Real live source (later) | `market_data_engine/streams/` + a `loader.py` mode + a config |
| New low-level feature operator | `compute/features.py`, `compute/backend.py`, `builders.py`, `api.py`, compute tests |

---

## 13. Current Test Status

```
674 passed, 17 skipped
```

Test scope covers:
- All 19 registered feature types (raw + derived).
- Dependency graph construction, cycle detection, and topological sort.
- Per-stream watermark routing and late-event policies.
- `state_dict` / `load_state_dict` round-trips.
- `warmup()` + live-loop integration.
- `profile_summary()` and `health_summary()` counters.
- Adapter layer (`adapt_bar_event`, `adapt_quote_tick_event`, `adapt_trade_tick_event`).
- `InMemoryEventProvider` filtering.
- Strategy integration example (`templates/strategy_integration_example.py`).

The 17 skipped tests require compiled Cython extensions (`nautilus_trader`) which are not available in the pure-Python CI environment.

---

## 14. Current Limitations

| Limitation | Impact | Planned Resolution |
|------------|--------|--------------------|
| No previous-value dependency mode | Derived feature B cannot see feature A from event t-1. | v2 roadmap. |
| No cross-instrument features | Cannot compute cross-instrument spread or correlation. | v2 roadmap. |
| No expression parser | Feature type is `params["type"]`; no formula strings. | v3, after backend is stable. |
| No partial-update semantics | When a dep updates only sometimes, derived feature sees cached dep value. | By design (latest-ready policy). |
| `health_summary()` counters zero for raw features without `update_status` | `RollingMeanFeature` / `RollingStdFeature` / `RollingMinFeature` / `RollingMaxFeature` emit `update_status=None`. | Low priority; use `RollingSumFeature` pattern as reference. |
| No persistent checkpoint | Engine state can be serialized via `state_dict()` / `load_state_dict()`, but no save-to-file helper yet. | See checkpoint design doc. |

---

## 15. Next Optimization Roadmap

### Short-term (v1.1)

| Item | Estimated effort |
|------|-----------------|
| Add `update_status="updated"` to remaining raw feature classes | 1 day |
| `engine.save_checkpoint(path)` / `engine.load_checkpoint(path)` helper | 1 day |
| Benchmark CI baseline (compare runs, not enforce thresholds) | 0.5 day |

### Medium-term (v2)

| Item | Estimated effort |
|------|-----------------|
| RSI, Bollinger Bands, ATR feature classes | 2 days |
| Previous-value dependency mode (`depends_on` offset=1) | 3 days |
| Per-dependency alignment for cross-frequency synchronization | 4 days |
| Rust backend POC (one feature type as proof of concept) | 1 week |

### Long-term (v3)

| Item | Estimated effort |
|------|-----------------|
| Expression parser for formula strings | 2 weeks |
| Cross-instrument feature bus | 3 weeks |
| GPU-accelerated batch warmup | POC phase |

---

*Generated for the nautilus-ext feature engine. Module path: `nautilus_ext/features/compute/`.*
