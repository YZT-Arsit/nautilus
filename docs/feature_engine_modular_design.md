# Modular Incremental Feature Engine — Design Document

## Overview

This document describes `nautilus_ext.features.compute`, a modular, incremental feature computation layer added to the repository.

The core goal is a stable strategy API that does not change when the underlying compute backend changes, and features that update in O(1) time per event rather than recomputing over the full history window.

---

## 1. Why Both Time-Triggered and Count-Triggered Updates Are Supported

Different features have fundamentally different trigger semantics, and forcing one global trigger onto all features is wrong.

| Feature | Natural trigger |
|---|---|
| Rolling 20-bar mean | `on_bar_close` — emit exactly once per new bar |
| Volume-scaled VWAP over 5 minutes | `on_window_close` — emit when the 5-min window rolls |
| Order-book imbalance | `on_event` — emit on every quote or book update |
| Slow aggregate (hourly ATR) | `on_timer` — emit at a fixed wall-clock interval |
| Subsampled indicator (every 5th bar) | `on_n_bars` — emit once every N bars |

A system with only count-based triggers cannot express "every 5 minutes of wall-clock time." A system with only time-based triggers cannot express "every 20 bars." Both are real production requirements.

`TriggerPolicy` encodes this at the spec level, not the engine level:

```python
@dataclass(frozen=True)
class TriggerPolicy:
    kind: str          # "on_event" | "on_bar_close" | "on_timer" | "on_n_events"
                       # | "on_n_bars" | "on_window_close"
    n: int | None       # for on_n_events, on_n_bars
    interval_ms: int | None  # for on_timer, on_window_close
```

Each feature retains `_last_trigger_ts` and `_event_count` and checks the trigger independently. The engine calls `feature.update()` for every matching event; the trigger policy controls whether `triggered=True` or `triggered=False` in the returned `FeatureUpdate`. Strategy code reads `FeatureSnapshot.scalar()` — it never needs to know which trigger fired.

---

## 2. How Historical-Data-Dependent Features Are Initialized

### The warmup / backfill pattern

Features requiring a lookback window (rolling mean over 20 bars, ATR over 14 bars, RSI) cannot emit reliable values until they have seen at least `window` historical events. The correct solution is to replay historical data through the same incremental update path that live data uses — not to load a DataFrame and compute from scratch.

```python
engine = SpecFeatureEngine(specs=[...])

# Pre-heat all features with historical bars
engine.warmup(historical_bars)    # internally calls feature.update() for each bar

# Live path — features continue from where warmup left off
snapshot = engine.on_event(live_bar)
```

`warmup()` is identical in behavior to calling `on_event()` repeatedly. No special cold-start code path exists. The feature's internal state accumulates naturally:

- `RollingMeanFeature`: `_state` (a `RollingWindowState`) fills its ring buffer. When `is_full` becomes True, `is_ready` becomes True.
- `EWMAFeature`: state is ready after the very first event (mandatory=False warmup), since EWMA is defined for all n ≥ 1.
- `SimpleReturnFeature`: ready after seeing two events.

### WarmupRequirement

Each feature declares its requirement explicitly:

```python
def warmup_required(self) -> WarmupRequirement:
    return WarmupRequirement(
        n_events=self._spec.window or 1,
        unit=self._spec.window_unit or "bars",
        mandatory=True,   # is_ready stays False until n_events processed
    )
```

The engine can query this to know the minimum warmup depth needed:

```python
max_warmup = max(f.warmup_required().n_events for f in engine._features.values())
```

### Point-in-time safety

`FeaturePipeline.warmup()` stamps all events with `is_warmup=True` in the resulting `FeatureEvent`. Offline stores and training pipelines filter `is_warmup=False` by default, which prevents look-ahead bias from historical data contaminating the training set.

---

## 3. How the Stable Interface Allows Replacing the Compute Backend

The strategy API consists entirely of types in `spec.py`:

```
FeatureSpec → FeatureValue → FeatureSnapshot
```

Strategy code calls:
```python
snapshot = engine.on_event(event)
value = snapshot.scalar("rolling_mean_close_20")   # float | None
```

The path from spec to concrete computation:

```
FeatureSpec (stable, strategy-facing)
    ↓
BackendRegistry.create_feature(spec)
    ↓  dispatches by spec.backend ("python", "rust", ...)
FeatureBackend.create_feature(spec)  ← the only place that knows the backend
    ↓
FeatureBase  (internal, never seen by strategy)
    ↓
FeatureSnapshot  (stable, strategy-facing)
```

To swap from Python to Rust:

```python
# Before: python backend (default)
registry = build_default_registry()

# After: register rust backend (zero strategy code changes)
from my_rust_ext import RustFeatureBackend
registry.register("rust", RustFeatureBackend())

# Spec changes only the backend field — name, window, trigger stay the same
spec = FeatureSpec(
    name="rolling_mean_close_20",
    input_type="bar",
    input_field="close",
    window=20,
    backend="rust",          # ← only this field changes
)
engine = SpecFeatureEngine(specs=[spec], backend_registry=registry)
```

The `FeatureBackend` protocol is structural (no inheritance required):

```python
@runtime_checkable
class FeatureBackend(Protocol):
    def create_feature(self, spec: FeatureSpec) -> FeatureBase: ...
```

Any class with a `create_feature` method satisfies it. The test `test_backend_swappable_same_api` verifies this with a `ConstantBackend` that produces the same `FeatureSnapshot` type regardless of implementation.

---

## 4. How the Design Reduces Latency

### No full-history recomputation

The most important latency source to eliminate is recomputing a rolling statistic by scanning the entire window on every bar. This turns a constant-time operation into O(window) per event.

#### Rolling mean — O(1) via running sum

```python
class RollingWindowState:
    def push(self, value: float) -> None:
        if len(self._buf) == self._maxlen:
            old = self._buf[0]
            self._sum -= old           # O(1): subtract evicted element
        self._buf.append(value)
        self._sum += value             # O(1): add new element

    @property
    def mean(self) -> float | None:
        return self._sum / len(self._buf)  # O(1): one division
```

After 200 bars with a window of 50, the internal buffer has exactly 50 entries. The sum is maintained exactly — not recomputed. The test `test_running_sum_is_window_only` verifies this:

```python
assert f._state.count == 50
assert f._state.sum == pytest.approx(sum(data[-50:]))
```

#### Rolling std — O(1) via running sum of squares

Variance is maintained as `E[x²] - E[x]²`, which requires only one extra float per push:

```python
self._sum_sq += value * value   # on push
self._sum_sq -= old * old       # on eviction

# At query time:
mean = self._sum / n
pop_var = self._sum_sq / n - mean * mean
sample_var = max(0.0, pop_var) * n / (n - 1)
```

No window scan. The test `test_incremental_matches_reference` verifies this matches the naive full-window computation to 1e-6 relative tolerance.

#### EWMA — O(1) always

```python
self._value = alpha * x + (1 - alpha) * self._value
```

One multiply, one addition. Window size is irrelevant.

#### Time-window eviction — O(amortized 1)

`TimeWindowState` uses a deque. On push, entries older than the window are popped from the front. Each entry is pushed and popped at most once across the lifetime of the state, so the total cost per event is O(1) amortized. The running sum is updated by one subtraction per eviction and one addition per push.

#### No DataFrame on the hot path

The `FeaturePipeline.update()` docstring states this explicitly. `SpecFeatureEngine.on_event()` constructs only a `FeatureSnapshot` dataclass, which contains a plain `dict[str, FeatureValue]`. No pandas DataFrame, no numpy array, no Parquet I/O.

`FeatureEvent` (for offline persistence) is a `frozen dataclass` with `slots=True`, which reduces per-object memory by ~30% compared to a plain dataclass.

### Hot vs cold path

| Path | What happens | Cost |
|---|---|---|
| `on_event(bar)` | Update state containers, check trigger, return FeatureSnapshot | O(n_features) dict ops |
| `feature.update()` | Push to ring buffer, update running sum, check trigger condition | O(1) per feature |
| `OnlineFeatureStore.put()` | Dict assignment + deque append | O(1) |
| `OfflineFeatureStore.append()` | List append (buffer, no flush) | O(1) |
| `OfflineFeatureStore.flush()` | List → DataFrame → Parquet | Amortized O(batch/flush_threshold) |
| `feature.to_row()` | Flatten FeatureEvent dict | O(n_columns), cold path only |

---

## 5. Module Structure

```
nautilus_ext/features/compute/
    __init__.py          — public exports
    spec.py              — FeatureSpec, TriggerPolicy, WarmupRequirement,
                           FeatureValue, FeatureUpdate, FeatureSnapshot
    state.py             — RollingWindowState, TimeWindowState, EWMAState, VWAPState
    feature_base.py      — FeatureBase protocol (structural)
    features.py          — concrete feature classes (pure Python backend)
    backend.py           — FeatureBackend, BackendRegistry, PythonBackend
    engine.py            — SpecFeatureEngine, SpecDrivenFeatureEngine
```

`SpecDrivenFeatureEngine` wraps `SpecFeatureEngine` and implements `FeatureEngineBase`, allowing the spec-driven system to be plugged directly into the existing `FeaturePipeline` with no pipeline changes:

```python
from nautilus_ext.features.compute import SpecDrivenFeatureEngine, FeatureSpec, TriggerPolicy
from nautilus_ext.features.feature_pipeline import FeaturePipeline

specs = [
    FeatureSpec(
        name="rolling_mean_close_20",
        input_type="bar",
        input_field="close",
        window=20,
        window_unit="bars",
        trigger=TriggerPolicy(kind="on_bar_close"),
        params={"type": "rolling_mean"},
    ),
    FeatureSpec(
        name="vwap_session",
        input_type="bar",
        trigger=TriggerPolicy(kind="on_bar_close"),
        params={"type": "vwap"},
    ),
]

engine = SpecDrivenFeatureEngine(specs=specs, feature_set_id="my_features_v1")
pipeline = FeaturePipeline(feature_engines=[engine], online_store=..., offline_store=...)
pipeline.warmup(historical_bars)
feature_events = pipeline.update(live_bar)   # returns list[FeatureEvent]
```

---

## 6. Feature Catalog (PythonBackend)

| `params["type"]` / name prefix | Input type | Fields used | Warmup required |
|---|---|---|---|
| `rolling_mean` | bar | `input_field` | window bars (mandatory) |
| `rolling_std` | bar | `input_field` | window bars (mandatory) |
| `rolling_min` | bar | `input_field` | window bars (mandatory) |
| `rolling_max` | bar | `input_field` | window bars (mandatory) |
| `vwap` | bar | close/volume (configurable via params) | 1 bar (non-mandatory) |
| `simple_return` | bar | `input_field` (default: close) | 2 bars |
| `log_return` | bar | `input_field` (default: close) | 2 bars |
| `ewma` | bar | `input_field` | span bars (non-mandatory) |
| `spread` | quote | bid_price, ask_price | 1 event |
| `mid_price` | quote | bid_price, ask_price | 1 event |
| `book_imbalance` | book_delta | bids/asks lists or bid_volume/ask_volume | 1 event |

---

## 7. Test Coverage

82 tests in `nautilus_ext/tests/test_compute_features.py`:

- State containers: push, eviction, running sum correctness, variance/std formula, reset, state_dict round-trip
- `RollingMeanFeature`: incremental values match naive full-window reference (1e-12 relative tolerance)
- `RollingStdFeature`: incremental std matches naive reference (1e-6 tolerance)
- Trigger policies: `on_event`, `on_n_bars`, `on_timer`
- `SpecFeatureEngine`: routing by input_type, warmup, state_dict, reset
- Backend: dispatch by `params["type"]`, by name prefix, error on unknown type/backend, backend-swappable API test
- `SpecDrivenFeatureEngine`: schema, `update()` → `FeatureEvent`, integration with `FeaturePipeline`, warmup tagging
- `FeatureBase` protocol: all concrete classes satisfy the protocol

---

## 8. Extension Points

**Add a new feature type to PythonBackend:**
```python
from nautilus_ext.features.compute.backend import _FEATURE_CLASSES
from nautilus_ext.features.compute.features import _AbstractFeature

class RSIFeature(_AbstractFeature):
    ...

_FEATURE_CLASSES["rsi"] = RSIFeature
```

**Register a new backend:**
```python
from nautilus_ext.features.compute.backend import BackendRegistry
registry = BackendRegistry()
registry.register("polars", MyPolarsBackend())
```

**Build a custom feature set engine (without changing FeaturePipeline):**
```python
engine = SpecDrivenFeatureEngine(
    specs=[...],
    feature_set_id="my_v2",
    backend_registry=my_registry,
)
pipeline.engines.append(engine)   # or pass to FeaturePipeline constructor
```
