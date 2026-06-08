# Modular Incremental Feature Engine — Design Document

## Overview

This document describes `nautilus_ext.features.compute`, a modular, incremental feature computation layer added to the repository.

The core goals are:

1. A stable strategy API that does not change when the underlying compute backend changes.
2. Features that update in O(1) time per event — no full-history recomputation.
3. Correct handling of out-of-order events via partitioned watermarks.
4. Deterministic behaviour in tests via injected clocks.

---

## 1. Timestamp Semantics — event_time vs receive_time vs process_time

Every market event carries three distinct timestamps, each with a different purpose.

| Field | Origin | Primary use |
|---|---|---|
| `event_time_ns` | Exchange / data source | Feature window computation (rolling VWAP, volatility, bar-close features) |
| `receive_time_ns` | Local system at network receipt | Latency measurement; receive-time replay for realistic backtest simulation |
| `process_time_ns` | SpecFeatureEngine at `on_event()` call time | System monitoring; measuring pipeline latency (process − receive) |

**Why three fields?**
Using a single timestamp introduces ambiguity.  `receive_time_ns` is always
`>= event_time_ns` by network delay.  `process_time_ns` adds queuing delay on
top of that.  Feature windows must be computed in exchange time (`event_time_ns`)
for correctness; using `receive_time_ns` or `process_time_ns` would shift window
boundaries by an arbitrary and non-stationary delay.

**`process_time_ns` is for latency monitoring only.**
It is never a valid input to a feature window unless the feature explicitly
declares `time_semantics="process_time"`, which is appropriate only for
infrastructure metrics (e.g. "how long did this event sit in the queue?").

### TriggerPolicy time_semantics

Each feature declares which timestamp governs its time-based trigger and
window eviction:

```python
TriggerPolicy(
    kind="on_timer",
    interval_ns=5_000_000_000,       # 5 seconds
    time_semantics="event_time",     # default — use exchange timestamp
)
```

Valid values: `"event_time"` (default), `"receive_time"`, `"process_time"`.

### Legacy ts_event conversion (TimestampConfig)

Events that predate the ns-precision fields carry a legacy `ts_event` field.
Different data vendors use different units for it.  Configure the conversion via
`TimestampConfig`:

```python
from nautilus_ext.features.compute import TimestampConfig, SpecFeatureEngine

engine = SpecFeatureEngine(
    specs=specs,
    ts_config=TimestampConfig(
        legacy_ts_event_unit="ms",           # "ns" | "us" | "ms" (default)
        require_event_time_ns_for_live=True, # raise if event_time_ns is missing
    ),
)
```

`require_event_time_ns_for_live=True` acts as a production data-quality gate.
When enabled, any live event (passed to `on_event()`) without `event_time_ns`
raises `RuntimeError` immediately, preventing silently wrong feature windows
from a misconfigured feed.  During warmup (`warmup()`) the check is bypassed
so historical data in legacy format still loads correctly.

---

## 2. Partitioned Watermarks — per-stream event-time tracking

### The problem with a single global watermark

A single global watermark is unsafe when the engine receives events from
multiple instruments or multiple event types.  Consider:

- BTC/USDT 1-minute bars arrive at 60-second intervals: watermark advances to 100s.
- ETH/USDT quotes arrive with a 2-second lag from the exchange: their timestamps
  are in the 95–98s range.

With a global watermark at 100s, every ETH quote would be classified as late
(arrival ≈ 97s < watermark 100s), even though they are perfectly on-time for
the ETH/USDT quote stream.

### The solution: StreamKey and partitioned WatermarkTrackers

Each distinct (instrument_id, input_type, source) triple is a `StreamKey`:

```python
@dataclass(frozen=True)
class StreamKey:
    instrument_id: str | None
    input_type: str         # "bar", "trade", "quote", "book_delta", ...
    source: str | None = None
```

`SpecFeatureEngine` maintains `watermarks: dict[StreamKey, WatermarkTracker]`.

When an event arrives:
1. Identify its `StreamKey` from `event.instrument_id` and `event.event_type`.
2. Advance **only that stream's** watermark with `event_time_ns`.
3. For each feature matching the event's `input_type`, check lateness against
   **that same stream's** watermark.

BTC/USDT bars advancing their watermark have zero effect on the ETH/USDT
quote watermark.  Each stream evolves independently.

### Accessing stream watermarks

```python
btc_wm = engine.watermark_for("BTC/USDT", "bar")     # per-stream
all_wm = engine.all_watermarks()                       # dict[StreamKey, int]
global_wm = engine.watermark_ns                        # max across all streams
```

---

## 3. Late Event Handling

### Watermark definition

```
watermark_ns = max_event_time_seen_ns - allowed_lateness_ns
```

An event with `trigger_ts_ns < watermark_ns` is "late".  `allowed_lateness_ns`
is declared per feature in `TriggerPolicy`, not per engine.

### Late event policies

| Policy | Behaviour | When to use |
|---|---|---|
| `"drop"` (default) | Skip `update()`; return cached value. Safe, hot-path friendly. | Production live trading |
| `"log_only"` | Log `WARNING`; skip `update()`. Observable but non-blocking. | Debugging feed delays |
| `"update_if_not_finalized"` | Call `update()` anyway. For rolling windows the state self-corrects; old entries evict normally on the next in-order push. | Near-real-time windows where slight reordering is acceptable |
| `"recompute_for_backtest_only"` | Calls `update()` during `warmup()` (backtest replay); acts as `"drop"` in live mode. | Backtest accuracy without live risk |
| `"raise"` | Raises `LateEventError` immediately with feature name, timestamps, and allowed lateness. | Strict pipelines that must fail fast on feed anomalies |

```python
from nautilus_ext.features.compute import LateEventError

try:
    snap = engine.on_event(event)
except LateEventError as e:
    log.error("Late event: %s trigger=%d wm=%d", e.feature_name, e.trigger_ts_ns, e.watermark_ns)
```

### update_if_not_finalized — rolling window semantics

For count-based rolling windows (`RollingWindowState`), there is no fixed
window boundary.  The engine always calls `update()`.

For time-based rolling windows (`TimeWindowState`, `VWAPState` with `window_ns`),
the state container evicts entries at the front using `ts_ns - window_ns` as
the cutoff.  A late entry (small `ts_ns`) sets a small cutoff, so no previous
entries are incorrectly evicted.  The next in-order push will perform the correct
eviction.  This is "self-correcting" and requires no special engine logic.

**Why not recompute finalized windows on the live path?**
Fixed-boundary finalized windows (e.g. hourly OHLC) would require a full
re-scan of historical data.  This is O(window) per late event, incompatible
with the O(1) hot-path requirement.  Backtest replay should be used for exact
historical accuracy; live trading accepts eventual consistency.

---

## 4. Clock Abstraction — deterministic process_time_ns

`SpecFeatureEngine` injects a `Clock` object rather than calling `time.time_ns()`
directly.  This makes `process_time_ns` deterministic in tests.

```python
class Clock(Protocol):
    def now_ns(self) -> int: ...

class SystemClock:           # production
    def now_ns(self): return time.time_ns()

class ManualClock:           # tests and replay
    def now_ns(self): return self._ns
    def set(self, ns): ...
    def advance(self, delta_ns): ...
```

Usage:

```python
from nautilus_ext.features.compute.clock import ManualClock

clock = ManualClock(initial_ns=1_000_000_000)
engine = SpecFeatureEngine(specs=specs, clock=clock, stamp_process_time=True)
snap = engine.on_event(bar)
assert snap.process_time_ns == 1_000_000_000           # reproducible
assert snap.processing_latency_ns() == 1_000_000_000 - snap.receive_time_ns
```

The default `stamp_process_time=True` in `SpecFeatureEngine` (live) and
`stamp_process_time=False` in `SpecDrivenFeatureEngine` (backtest adapter)
preserve backward compatibility.

---

## 5. Window Metadata on FeatureValue

`FeatureValue` carries optional window-boundary fields to allow downstream code
to identify the exact time interval a value represents:

```python
@dataclass(frozen=True)
class FeatureValue:
    name: str
    value: float | int | bool | None
    is_ready: bool
    window_start_ns: int | None = None    # time-window features only
    window_end_ns: int | None = None      # time-window features only
    source_event_time_ns: int | None = None  # event that triggered this update
```

| Feature type | `window_start_ns` | `window_end_ns` | `source_event_time_ns` |
|---|---|---|---|
| Count-based rolling (mean, std, min, max) | None | None | ✓ always set |
| Time-based VWAP (`window_unit="seconds"`) | `ts_ns - window_ns` | `ts_ns` | ✓ equals `window_end_ns` |
| Session VWAP (unbounded) | None | None | ✓ always set |
| Spread, MidPrice, BookImbalance | None | None | ✓ always set |

`source_event_time_ns` is the trigger timestamp (honouring `time_semantics`) of
the event that caused the value to be emitted.

---

## 6. How Historical-Data-Dependent Features Are Initialized

Features requiring a lookback window replay historical data through the same
incremental update path that live data uses:

```python
engine = SpecFeatureEngine(specs=[...])
engine.warmup(historical_bars)    # calls feature.update() for each event
snapshot = engine.on_event(live_bar)
```

No separate cold-start code path exists.  `warmup()` is identical to calling
`on_event()` repeatedly, except:
- `process_time_ns` is not stamped.
- Late event policies are bypassed (`_route_warmup()` calls `feature.update()`
  directly, skipping `_handle_late()`).

### WarmupRequirement

Each feature declares its warmup depth:

```python
def warmup_required(self) -> WarmupRequirement:
    return WarmupRequirement(n_events=self._spec.window or 1, unit="bars", mandatory=True)
```

The engine can query this for buffer sizing:

```python
max_warmup = max(f.warmup_required().n_events for f in engine._features.values())
```

---

## 7. How the Stable Interface Allows Replacing the Compute Backend

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
from my_rust_ext import RustFeatureBackend
registry.register("rust", RustFeatureBackend())
spec = FeatureSpec(name="rolling_mean_close_20", ..., backend="rust")
engine = SpecFeatureEngine(specs=[spec], backend_registry=registry)
```

---

## 8. How the Design Reduces Latency

### No full-history recomputation

| Feature | Update cost |
|---|---|
| Rolling mean | O(1): subtract evicted, add new, divide |
| Rolling std | O(1): running sum-of-squares, E[x²]−E[x]² formula |
| EWMA | O(1): one multiply + one add |
| Time-window eviction | O(amortized 1): deque front-pop |

### No DataFrame on the hot path

`SpecFeatureEngine.on_event()` constructs only a `FeatureSnapshot` dataclass,
which contains a plain `dict[str, FeatureValue]`.  No pandas, no numpy, no I/O.

### Watermark checks are O(1)

Each `on_event()` call performs:
1. One dict lookup to find the stream's `WatermarkTracker` (O(1)).
2. One comparison `trigger_ts_ns < max_event_time_ns - allowed_lateness_ns` (O(1)).

Neither operation scales with history length or window size.

### Hot vs cold path

| Path | What happens | Cost |
|---|---|---|
| `on_event(bar)` | Extract timestamps, advance watermark, route to features, return FeatureSnapshot | O(n_features) |
| `feature.update()` | Push to state container, check trigger, return FeatureValue | O(1) per feature |
| `watermark.is_late_for()` | One integer subtraction and comparison | O(1) |

---

## 9. Module Structure

```
nautilus_ext/features/compute/
    __init__.py          — public exports
    spec.py              — FeatureSpec, TriggerPolicy, WarmupRequirement,
                           FeatureValue, FeatureUpdate, FeatureSnapshot
    timestamps.py        — EventTimestamps, TimestampConfig, extract_timestamps,
                           select_timestamp, convert_legacy_ts_event_to_ns
    watermark.py         — StreamKey, WatermarkTracker
    clock.py             — Clock protocol, SystemClock, ManualClock
    state.py             — RollingWindowState, TimeWindowState, EWMAState, VWAPState
    feature_base.py      — FeatureBase protocol (structural)
    features.py          — concrete feature classes (pure Python backend)
    backend.py           — FeatureBackend, BackendRegistry, PythonBackend
    engine.py            — SpecFeatureEngine, SpecDrivenFeatureEngine, LateEventError
```

---

## 10. Feature Computation Lifecycle

Every feature follows the same lifecycle, regardless of whether it is a count-based
rolling window or a time-based window.

```
1. Spec registration
   FeatureSpec(name="rolling_mean_close_20", input_type="bar", window=20, ...)
       ↓
   BackendRegistry.create_feature(spec)
       ↓
   FeatureBase (e.g. RollingMeanFeature)  — concrete state allocated, empty

2. Warmup / backfill
   engine.warmup(historical_bars)
       ↓  for each event:
   _route_warmup(event) → feature.update(event)    ← same update() as live
       ↓
   WatermarkTracker.update(event_time_ns)           ← watermarks advance during warmup
       ↓
   feature.is_ready becomes True once WarmupRequirement.n_events processed

3. Live / backtest event replay
   engine.on_event(event)
       ↓
   extract_timestamps(event)          → EventTimestamps
   clock.now_ns()                     → process_time_ns  (stamped if enabled)
       ↓
   StreamKey = (instrument_id, input_type, source)
   WatermarkTracker.update(event_time_ns)   ← per-stream advance
       ↓  for each subscribed feature:
   select_timestamp() → trigger_ts_ns
   watermark.is_late_for(trigger_ts_ns, allowed_lateness_ns) → bool
       ↓
   if late: _handle_late() → cached FeatureValue  (drop / log / update / raise)
   else:    feature.update(event)    → FeatureUpdate  ← same update() as warmup

4. Incremental state update (inside feature.update())
   push value into state container (O(1) or amortized O(1))
   _should_trigger() → bool
   if triggered: compute output from running stats and emit new FeatureValue
   else:         return cached FeatureValue (no recomputation)

5. Snapshot publication
   FeatureSnapshot(
       ts_event=event_time_ns,
       instrument_id=...,
       values={name: FeatureValue, ...},
       receive_time_ns=...,
       process_time_ns=...,
   )
```

**Key invariant**: `warmup()` and `on_event()` both call the same `feature.update()`.
There is no separate cold-start code path. The only difference is that warmup bypasses
the late-event check (all history is assumed on-time) and does not stamp `process_time_ns`.

---

## 11. Count-Window vs Time-Window Features

Features differ in *how they define the lookback window*.

### Count-based rolling window

Backed by `RollingWindowState` — a fixed-size ring buffer.

- Window is fixed at construction: `RollingWindowState(maxlen=N)`.
- On each push: oldest element evicted (if full), running sum updated — **O(1)**.
- `is_ready` when `count == N` (mandatory warmup) or after first event (non-mandatory).
- **No notion of wall-clock time**: if bars arrive once per day, the window still spans N bars.
- Features: `rolling_mean`, `rolling_std`, `rolling_min`, `rolling_max`, `rolling_sum`,
  `rolling_volume_sum`, `simple_return`, `log_return`.

```python
spec = FeatureSpec(
    name="mean_20",
    input_type="bar",
    input_field="close",
    window=20,
    window_unit="bars",    # count-based
)
```

### Time-based rolling window

Backed by `TimeWindowState` or `VWAPState` with `window_ns` set.

- Window is defined by elapsed time: `window_ns = window * unit_in_ns`.
- On each push: evict all entries with `ts_ns <= current_ts - window_ns` — **O(amortized 1)**.
- `window_start_ns` and `window_end_ns` are populated in `FeatureValue` so downstream
  code can identify the exact interval the value represents.
- Late entries do **not** cause incorrect eviction: a late entry's small `ts_ns` sets a small
  cutoff, so no on-time entries are removed. The next in-order push corrects the window.
- Features: `vwap` with `window_unit` in `"seconds"`, `"minutes"`, `"milliseconds"`, `"nanoseconds"`.

```python
spec = FeatureSpec(
    name="vwap_5min",
    input_type="bar",
    window=5,
    window_unit="minutes",   # time-based
)
```

### Why historical features use warmup + incremental update

The engine does not run a separate batch computation for warmup data. All history
passes through the same `feature.update()` incremental path. This guarantees:

1. Identical numerical results between backtest and live.
2. `state_dict()` checkpoint/restore works the same way in both modes.
3. No code duplication — the state container is defined once.

The trade-off: a cold start replays the full history window (not a summary), so warmup
scales linearly with `WarmupRequirement.n_events`. This is acceptable because warmup runs
once offline; the live hot path is O(1) per event.

---

## 12. Feature Catalog (PythonBackend)

| `params["type"]` / name prefix | Input type | Fields used | Warmup required | Window metadata |
|---|---|---|---|---|
| `rolling_mean` | bar | `input_field` | window bars (mandatory) | `source_event_time_ns` |
| `rolling_std` | bar | `input_field` | window bars (mandatory) | `source_event_time_ns` |
| `rolling_min` | bar | `input_field` | window bars (mandatory) | `source_event_time_ns` |
| `rolling_max` | bar | `input_field` | window bars (mandatory) | `source_event_time_ns` |
| `rolling_sum` | bar / trade / quote | `input_field` (required) | window bars (mandatory) | `source_event_time_ns`; `update_status` |
| `rolling_volume_sum` | bar / trade | `input_field` (default: `volume`) | window bars (mandatory) | `source_event_time_ns`; `update_status` |
| `vwap` | bar | close/volume (configurable) | 1 bar (non-mandatory) | `source_event_time_ns`; `window_start/end_ns` when time-based |
| `simple_return` | bar | `input_field` (default: close) | 2 bars | `source_event_time_ns` |
| `log_return` | bar | `input_field` (default: close) | 2 bars | `source_event_time_ns` |
| `ewma` | bar | `input_field` | span bars (non-mandatory) | `source_event_time_ns` |
| `spread` | quote | bid_price, ask_price | 1 event | `source_event_time_ns` |
| `mid_price` | quote | bid_price, ask_price | 1 event | `source_event_time_ns` |
| `book_imbalance` | book_delta | bids/asks lists or bid_volume/ask_volume | 1 event | `source_event_time_ns` |

**`rolling_sum` vs `rolling_volume_sum`**:
`rolling_sum` is the generic form — it sums any field named in `spec.input_field`.
`rolling_volume_sum` is a semantic alias that defaults `input_field` to `"volume"` when
the spec does not specify one. They are numerically identical when `input_field="volume"`.
Use `rolling_sum` for new features; `rolling_volume_sum` remains for backward compatibility.

**Alias routing**: vendor event types (`"quote_tick"`, `"orderbook"`, `"trade_tick"`, etc.)
are normalised to canonical names (`"quote"`, `"book_delta"`, `"trade"`) by
`input_type_for_event()` before routing. `FeatureSpec.input_type` must always use the
canonical name.

**Backend dispatch priority** (PythonBackend):

1. `params["type"]` — explicit key; always wins over name inference.
2. Exact name match — `"rolling_sum"` resolves directly to `RollingSumFeature`.
3. Longest-prefix match — `"rolling_sum_5bar"` matches `"rolling_sum"` (11 chars) rather than
   `"rolling_volume_sum"` (18 chars) because `"rolling_sum_5bar"` does not start with
   `"rolling_volume_sum"`.  Longer keys are tested first, so ambiguity is impossible by
   construction.

Silent wrong dispatch cannot occur: if neither exact nor prefix match succeeds, `PythonBackend`
raises `ValueError` with the full list of known types.

---

## 13. Why Strategy Code Must Depend Only on FeatureSpec / FeatureSnapshot

The strategy API is deliberately narrow:

```
FeatureSpec  →  FeatureValue  →  FeatureSnapshot
```

Strategy code calls:
```python
snapshot = engine.on_event(event)
value = snapshot.scalar("rolling_mean_close_20")   # float | None
```

This means:

- **No import of feature classes** (`RollingMeanFeature`, etc.) — those are backend internals.
- **No access to state containers** (`RollingWindowState`) — the running sum is an implementation
  detail that can be replaced with a Rust ring buffer without any strategy change.
- **Backend swap is one registration call**: add `registry.register("rust", RustBackend())`,
  set `FeatureSpec(..., backend="rust")` — zero strategy code changes.
- **FeatureSnapshot timestamps are stable** (`ts_event`, `receive_time_ns`, `process_time_ns`)
  regardless of which backend produced the values.

---

## 14. Test Coverage

~290 tests in `nautilus_ext/tests/test_compute_features.py`:

- **State containers**: push, eviction, running sum, variance/std, reset, state_dict round-trip
- **TimestampConfig**: ms/us/ns legacy conversion, `require_event_time_ns_for_live` raise/skip
- **EventTimestamps**: latency_ns, processing_latency_ns, select_timestamp dispatch
- **WatermarkTracker**: monotonic advance, allowed_lateness, is_late_for, finalize, state_dict
- **StreamKey / Partitioned watermarks**: multi-instrument, multi-type independence, all_watermarks(), state_dict round-trip
- **ManualClock**: deterministic process_time_ns, processing_latency_ns, Clock protocol
- **Feature classes**: all 13 feature classes against reference implementations (1e-12 relative tolerance)
- **RollingVolumeSumFeature**: incremental sum vs reference, eviction, custom field, state_dict, engine routing, backend dispatch
- **RollingSumFeature**: incremental sum over close/volume, equivalence with RollingVolumeSumFeature, eviction, update_status, state_dict, backend dispatch, prefix routing
- **TriggerPolicy**: on_event, on_n_bars, on_timer (interval_ns), time_semantics
- **Late event policies**: drop / log_only / update_if_not_finalized / raise / recompute_for_backtest_only
- **Late event boundary**: exact ns thresholds, AAPL vs MSFT independence, bar vs quote isolation, source isolation
- **Window metadata**: VWAP time-window `window_start_ns`/`window_end_ns`, `source_event_time_ns` on all features
- **input_type_for_event**: canonical values, vendor aliases, None cases, routing correctness
- **Watermark source-aware query**: exact source, aggregate max, Binance vs OKX independence
- **Engine mode (is_live)**: live raises on missing event_time_ns, backtest allows fallback
- **LateEventError diagnostics**: stream_key fields, late_by_ns formula, receive/process timestamps
- **Warmup and live same path**: state after warmup equals all-on_event; alias routing verified
- **SpecFeatureEngine**: routing, warmup, snapshot ts_event, state_dict, reset
- **SpecDrivenFeatureEngine**: schema, FeatureEvent (ms ts_event), FeaturePipeline integration, warmup tagging
- **Backend dispatch hardening**: params["type"] priority, exact name priority, longest-prefix determinism, unknown names raise ValueError, all registered types dispatch to correct class
- **Backend replacement equivalence**: DebugPythonBackend produces same names/values/readiness as PythonBackend; strategy code uses only FeatureSpec/FeatureSnapshot
- **Update status / observability**: update_status field on FeatureValue, "updated"/"not_ready"/"skipped_missing_field", reason/source_field on skip, cached value unchanged after skip, backward compat (legacy features return None)
- **Performance guard**: RollingWindowState buffer bounded at maxlen, deque.maxlen proves O(1) construction, no pandas import in hot-path modules, engine per-event routing is linear in subscribed features only
- **FeatureSnapshot consumption API**: get/value/is_ready/updated_names/as_dict/statuses; backward-compatible; strategy-style helper uses only FeatureSnapshot
- **Engine latest-value API**: get/value/latest/latest_values/ready/statuses/feature_specs; API-independence across backends
- **FeatureSpec validation**: empty name, duplicate name, invalid input_type, window ≤ 0, missing input_field for rolling_sum, unknown backend, unknown feature type
- **Feature catalog introspection**: available_feature_types(), feature_names() determinism, feature_specs() returns frozen originals
- **Profiling hook**: update_count/skip_count/late_drop_count per feature; zero overhead when disabled; profile=False leaves dicts empty

---

## 15. Update Status Observability

`FeatureValue` carries lightweight observability fields that help diagnose
field-name mistakes and missing-data conditions without crashing the pipeline.

```python
@dataclass(frozen=True)
class FeatureValue:
    name: str
    value: float | int | bool | None
    is_ready: bool
    # ... existing fields ...
    update_status: str | None   # "updated" | "not_ready" | "skipped_missing_field"
    reason: str | None          # human-readable explanation on skip/error
    source_field: str | None    # input field name involved in a skip
```

### Status values

| `update_status` | Meaning |
|---|---|
| `"updated"` | Feature computed a new value; `value` and `is_ready` reflect the update. |
| `"not_ready"` | Not enough history yet; `is_ready=False`, `value=None`. |
| `"skipped_missing_field"` | The input field named in `FeatureSpec.input_field` was absent on the event. Cached value returned unchanged. |
| `None` | Feature does not populate this field (backward-compatible legacy features). |

### Behavior contract

- **Do not crash**: a missing field returns the cached value, never raises.
- **Cache is unchanged**: `feature.value` (the cached property) is NOT updated on a skip. Only `feature.update(event).value.update_status` shows the skip.
- **`source_field` enables field-name diagnosis**: if `update_status == "skipped_missing_field"`, `source_field` names the missing field for logging/alerting.

### Which features populate update_status

Currently `RollingSumFeature` and `RollingVolumeSumFeature` (subclass). Legacy features
(`RollingMeanFeature`, etc.) return `update_status=None`. New features should populate
these fields using the `_emit()` `update_status` parameter and the `_missing_field()` helper.

```python
# Inside a feature's update():
v = _field(event, self._field_name)
if v is None:
    return self._missing_field(self._field_name)   # skipped_missing_field
self._state.push(v)
return self._emit(value, ready, triggered,
                  update_status="updated" if ready else "not_ready")
```

---

## 16. Strategy-Facing FeatureSnapshot API

Strategy code should consume feature outputs exclusively through `FeatureSnapshot`.
This decouples strategies from backend implementation details and allows the backend
to be swapped without any strategy change.

### Full method reference

```python
snap = engine.on_event(event)

# Retrieve a FeatureValue (includes is_ready, update_status, timestamps)
fv: FeatureValue | None = snap.get("feat_name")           # None if absent
fv = snap.get("feat_name", default_fv)                    # custom default

# Get the raw scalar — None or custom default if absent / not ready
v: float | None = snap.value("feat_name")
v = snap.value("feat_name", float("nan"))                 # custom default

# Readiness check for a single feature
snap.is_ready("feat_name")   # → bool

# Bulk access
snap.ready_values()                         # dict[str, Any] — ready only
snap.as_dict()                              # dict[str, Any] — ready only (default)
snap.as_dict(include_not_ready=True)        # dict[str, Any] — all features, None for unready
snap.to_dict()                              # legacy: all features including None for unready

# Observability
snap.updated_names()   # list[str] — features with update_status == "updated"
snap.statuses()        # dict[str, str|None] — update_status for every feature

# Cross-feature readiness
snap.all_ready()       # True when every feature is ready
len(snap)              # number of features in the snapshot
```

### Strategy pattern

```python
def on_bar(snap: FeatureSnapshot) -> float | None:
    # Guard on readiness — no backend imports needed
    if not snap.is_ready("mean_20") or not snap.is_ready("vol_20"):
        return None

    mean = snap.value("mean_20", 0.0)
    vol  = snap.value("vol_20",  1.0)
    return (snap.value("close_last", mean) - mean) / vol

# Diagnostics: log features that are not computing
not_updated = [
    name for name, status in snap.statuses().items()
    if status != "updated"
]
```

### Important: skip status lives in the snapshot, not the engine cache

`FeatureSnapshot.statuses()` reflects the per-event update returned by
`feature.update()`.  For skipped events (`"skipped_missing_field"`), this shows
the skip status.

`engine.statuses()` reads from each feature's internal `_cached` value, which is
**not updated on a skip** (by design — the cached state reflects the last real emit).
Use `snap.statuses()` for per-event observability; use `engine.statuses()` to check
the state of the engine between events.

---

## 17. Engine Latest-Value API

`SpecFeatureEngine` exposes accessors that mirror `FeatureSnapshot` for use outside
of an `on_event()` call — useful for initialisation checks, health monitors, and REPL
inspection.

```python
# FeatureValue accessors
engine.get("feat_name")               # FeatureValue | None (default None)
engine.get("feat_name", my_default)   # custom default when feature absent

# Raw scalar
engine.value("feat_name")             # Any | None — None if absent or not ready
engine.value("feat_name", 0.0)        # custom default

# Readiness
engine.ready("feat_name")             # bool — True if present and is_ready
engine.is_ready("feat_name")          # bool — same as ready()
engine.is_ready()                     # bool — True if ALL features are ready

# Bulk access (all features)
engine.latest()                       # dict[str, FeatureValue]
engine.latest_values()                # dict[str, Any] — ready only (default)
engine.latest_values(include_not_ready=True)  # all features, None for unready

# Observability
engine.statuses()                     # dict[str, str|None] — update_status per feature

# Spec / catalog inspection
engine.feature_names()                # list[str] — insertion order
engine.feature_specs()                # dict[str, FeatureSpec] — frozen originals
engine.specs()                        # list[FeatureSpec] — original list
```

---

## 18. FeatureSpec Validation

`SpecFeatureEngine` validates all specs at construction time, before any feature
instance is created.  Obvious configuration mistakes are caught with a descriptive
`ValueError` rather than failing silently at runtime.

### Validation rules

| Rule | Error trigger | Example |
|---|---|---|
| Non-empty name | `name == ""` | `FeatureSpec(name="")` |
| Unique names | Duplicate name in list | Two specs both named `"vol"` |
| Known input_type | Not in canonical + alias set | `input_type="candle"` |
| Positive window | `window <= 0` | `window=0` or `window=-1` |
| input_field for rolling_sum | `input_field=None` and `_DEFAULT_FIELD=None` | `FeatureSpec(..., params={"type": "rolling_sum"})` without `input_field` |
| Registered backend | `spec.backend` not in registry | `backend="rust"` when no Rust backend registered |
| Known feature type | Name matches no prefix and `params["type"]` is unrecognised | `params={"type": "magic_alpha"}` |

`rolling_volume_sum` does **not** require `input_field` — its `_DEFAULT_FIELD = "volume"`
acts as the fallback.

```python
# These all raise ValueError at construction time:
SpecFeatureEngine(specs=[FeatureSpec(name="")], ...)              # empty name
SpecFeatureEngine(specs=[spec, spec], ...)                        # duplicate
SpecFeatureEngine(specs=[FeatureSpec(input_type="candle")], ...)  # bad input_type
SpecFeatureEngine(specs=[FeatureSpec(window=-1)], ...)            # bad window
SpecFeatureEngine(
    specs=[FeatureSpec(name="s", params={"type": "rolling_sum"})],
    ...
)                                                                  # missing input_field
```

---

## 19. Feature Catalog and Registry Introspection

### PythonBackend

```python
from nautilus_ext.features.compute.backend import PythonBackend

backend = PythonBackend()
types = backend.available_feature_types()   # sorted list of all registered type keys
# → ['book_imbalance', 'ewma', 'log_return', 'mid_price',
#    'rolling_max', 'rolling_mean', 'rolling_min', 'rolling_std',
#    'rolling_sum', 'rolling_volume_sum', 'simple_return', 'spread', 'vwap']
```

### SpecFeatureEngine

```python
engine.feature_names()    # list[str] — insertion order, deterministic
engine.feature_specs()    # dict[str, FeatureSpec] — frozen originals, safe to read
```

`feature_specs()` returns references to the original `FeatureSpec` objects (which are
frozen dataclasses), so no copying is needed.  The dict preserves insertion order.

---

## 20. Benchmark Script

`scripts/benchmark_feature_engine.py` measures the incremental hot-path latency
for `SpecFeatureEngine.on_event()` without pandas or DataFrame recomputation.

### Usage

```bash
# default: 100 000 events, 20 features, window 100
python -m scripts.benchmark_feature_engine

# stress test: 100 features, large window
python -m scripts.benchmark_feature_engine --events 100000 --features 100 --window 1000

# with engine profiling summary
python -m scripts.benchmark_feature_engine --events 50000 --features 20 --profile
```

### Reported metrics

| Metric | Meaning |
|---|---|
| `total elapsed` | Wall-clock time for all `on_event()` calls |
| `avg on_event` | Mean latency per call (µs) |
| `p50 / p95 / p99` | Percentile latencies (µs) |
| `events/sec` | Throughput |
| `feature·ev/sec` | Throughput × n_features (total incremental updates/sec) |

### How to interpret results

- **avg on_event should scale linearly with `--features`**.  If it does not, something
  on the hot path is no longer O(n_features).
- **`--window` must not affect steady-state latency**.  The ring buffer is bounded at
  construction; a window-1000 feature costs the same as a window-5 feature once warm.
  Any regression here indicates an O(window) scan crept in.
- **p99 << 1 ms** for 20 features on a quiet machine is a reasonable baseline.  Actual
  numbers depend on CPU, Python version, OS scheduler, and machine load.

### Profiling mode

With `--profile`, the engine collects per-feature event counters and prints a summary:

```
  Profile summary (top 10 by update_count)
  --------------------------------------------------------
  feature                        updated  skipped     late
  f0_rolling_mean                  99980    20           0
  f1_rolling_std                   99980    20           0
  ...
```

### IMPORTANT: no CI timing gate

**Do not use these numbers as hard pass/fail thresholds in CI.**
They are machine- and load-dependent.  Use the benchmark for relative profiling:
compare runs before and after a hot-path change, or at different `--features` counts
to verify linearity.  A true performance regression should appear as a clear
O-complexity change, not a small absolute difference in µs.

---

## 21. Optional Engine Profiling Hook

`SpecFeatureEngine` accepts an optional `profile=True` flag that collects per-feature
event counters with minimal overhead.

```python
engine = SpecFeatureEngine(specs, profile=True)

for event in live_feed:
    engine.on_event(event)

summary = engine.profile_summary()
# {
#     "profile": True,
#     "features": {
#         "feat_name": {
#             "update_count":    int,   # events that produced update_status="updated"
#             "skip_count":      int,   # update_status in ("not_ready", "skipped_missing_field")
#             "late_drop_count": int,   # late events dropped (policy=drop/log_only)
#         },
#         ...
#     }
# }
```

When `profile=False` (default), `profile_summary()` returns `{"profile": False}` and
the three counter dicts remain empty — zero memory overhead and zero hot-path cost.

### Counter semantics

| Counter | Increments when |
|---|---|
| `update_count` | `feature.update()` returned `update_status="updated"` |
| `skip_count` | `update_status` was `"not_ready"` or `"skipped_missing_field"` |
| `late_drop_count` | Event was late and policy was `"drop"`, `"log_only"`, or `"recompute_for_backtest_only"` |

**Note**: legacy features (`RollingMeanFeature`, etc.) return `update_status=None`; their
events are not counted in `update_count` or `skip_count`.  Only features that explicitly
set `update_status` (currently `RollingSumFeature` and subclasses) contribute to these
counters.  Warmup events are excluded — counters only reflect `on_event()` calls.

As of Session 5, `profile_summary()` also includes `last_status` per feature — a string
reflecting the `update_status` of the most recent `on_event()` call (or `"late_dropped"`
when the event was dropped by lateness policy).  `last_status` is `None` before the first
`on_event()`.

---

## 22. Event Adapter Layer

Strategy code works with `FeatureSnapshot` and `SpecFeatureEngine`.  The engine accepts
any duck-typed event object that carries the right attributes.  The adapter layer converts
existing event classes (which may use `datetime` timestamps and lack `event_type`) into
adapter objects the engine can consume directly, without modifying the source classes.

### Module: `nautilus_ext/features/compute/adapters.py`

```
adapt_bar_event(bar)        → BarMarketEvent      (frozen dataclass, event_type="bar")
adapt_quote_tick_event(q)   → QuoteMarketEvent    (frozen dataclass, event_type="quote")
```

`BarMarketEvent` and `QuoteMarketEvent` are frozen dataclasses with:

| Field | Type | Notes |
|---|---|---|
| `instrument_id` | `str` | Preserved from source |
| `event_type` | `str` | Always canonical ("bar" / "quote") |
| `event_time_ns` | `int` | Nanoseconds POSIX |
| `receive_time_ns` | `int \| None` | From `ts_init` if available |
| `source` | `str \| None` | Preserved |
| OHLCV / bid/ask | `float` | Type-specific fields |

### Timestamp resolution

```
event_time_ns  = bar.event_time_ns  or  _datetime_to_ns(bar.ts_event)
receive_time_ns = bar.receive_time_ns or _datetime_to_ns(bar.ts_init) or event_time_ns
```

`_datetime_to_ns(dt)` handles `datetime` objects (`.timestamp() × 1e9`), integers, and
floats.  Returns 0 for `None`.

### When adapters are not needed

If your event class already has `event_type`, `event_time_ns`, and `receive_time_ns`
with correct nanosecond semantics, pass it directly — no adapter required.

---

## 23. Warmup Provider Interface

The warmup path accepts any iterable.  For larger-scale warmup from a catalog or
database, implement the `HistoricalEventProvider` protocol:

```python
@runtime_checkable
class HistoricalEventProvider(Protocol):
    def iter_events(
        self,
        instrument_id: str | None = None,
        input_type: str | None = None,
        start_ns: int = 0,
        end_ns: int | None = None,
    ) -> Iterable: ...
```

**Ordering contract**: events MUST be yielded in ascending `event_time_ns` order.
`engine.warmup()` does not sort — it feeds events to features in iteration order and
advances watermarks accordingly.

### InMemoryEventProvider

Bundled list-backed implementation for tests and prototyping:

```python
from nautilus_ext.features.compute.adapters import InMemoryEventProvider, adapt_bar_event

adapted = [adapt_bar_event(b) for b in raw_bars]
provider = InMemoryEventProvider(adapted)

engine.warmup(provider.iter_events(instrument_id="BTC/USDT", input_type="bar"))
engine.warmup(provider.iter_events(start_ns=t0_ns, end_ns=t1_ns))
```

Filtering is O(n) over the list.  Production providers should use indexed stores.

### Warmup equivalence

`engine.warmup(events)` is identical to calling `feature.update(event)` for each event
in order with no late-event check.  State produced by warmup is indistinguishable from
state produced by the same events fed via `on_event()`.

---

## 24. Realized Volatility Feature

`RealizedVolatilityFeature` computes the rolling sample standard deviation of
log close-to-close returns.

**Formula**: `std(log(close_t / close_{t-1}))` over a count-based window of N returns.

| Property | Value |
|---|---|
| Type key | `"realized_volatility"` |
| Input type | `"bar"` |
| Default field | `"close"` (override via `input_field`) |
| Window unit | bars |
| Warmup required | `window + 1` bars (to produce `window` returns) |
| Update status | `"updated"` / `"not_ready"` / `"skipped_missing_field"` |
| State | O(1) via `RollingWindowState(track_squares=True)` |

```python
spec = FeatureSpec(
    name="rvol20",
    input_type="bar",
    input_field="close",  # optional; defaults to "close"
    window=20,
    params={"type": "realized_volatility"},
)
```

`warmup_required().n_events == window + 1` — feeding `window` bars produces `window - 1`
returns, which is one short of a full window.

---

## 25. Strategy Integration Example

Strategy code depends only on `FeatureSpec`, `SpecFeatureEngine`, and `FeatureSnapshot`.
No backend, feature-class, or state imports are needed.

### Recommended pattern

```python
from nautilus_ext.features.compute import FeatureSpec, TriggerPolicy, SpecFeatureEngine
from nautilus_ext.features.compute.adapters import adapt_bar_event, InMemoryEventProvider

# 1. Define specs (stable strategy-facing configuration)
specs = [
    FeatureSpec(name="mean20",  input_type="bar", input_field="close",
                window=20, params={"type": "rolling_mean"}),
    FeatureSpec(name="rvol20",  input_type="bar", input_field="close",
                window=20, params={"type": "realized_volatility"}),
    FeatureSpec(name="spread",  input_type="quote", params={"type": "spread"}),
]

# 2. Build engine
engine = SpecFeatureEngine(specs, stamp_process_time=False)

# 3. Warm up from historical data
provider = InMemoryEventProvider([adapt_bar_event(b) for b in historical_bars])
engine.warmup(provider.iter_events(instrument_id="BTC/USDT"))

# 4. Live loop
def on_bar(bar):
    snap = engine.on_event(adapt_bar_event(bar))
    mean = snap.value("mean20")        # float or None
    vol  = snap.value("rvol20")
    if mean is not None and vol is not None:
        _generate_signal(mean, vol)

def on_quote(quote):
    snap = engine.on_event(adapt_quote_tick_event(quote))
    spread = snap.value("spread")
    ...
```

### Abstraction guarantee

If `FeatureSpec` and `FeatureSnapshot` are unchanged, swapping the backend
(e.g. Python → Rust) requires only one line: `FeatureSpec(..., backend="rust")`.
Strategy code is unaffected.

---

## 26. Realistic Benchmark Modes

`scripts/benchmark_feature_engine.py` supports three event-kind modes:

```bash
# Bar-only (default)
python -m scripts.benchmark_feature_engine --events 100000 --features 100 --window 1000

# Quote-only  (SpreadFeature + MidPriceFeature)
python -m scripts.benchmark_feature_engine --event-kind quote --events 100000 --features 20

# Mixed (bar + quote, features split by type, events interleaved by timestamp)
python -m scripts.benchmark_feature_engine --event-kind mixed --events 100000 --features 20 --window 1000
```

### Report fields

| Field | Description |
|---|---|
| `total elapsed` | Wall-clock time for all `on_event()` calls |
| `actual events` | Actual number of events processed (may differ in mixed mode) |
| `avg on_event` | Mean latency per call (µs) |
| `p50/p95/p99` | Percentile latencies (µs) |
| `events/sec` | Throughput |
| `feature·ev/sec` | Throughput × n_features |

With `--profile`, the report also shows a per-feature table including `last_status`.

### Mixed-mode behaviour

In mixed mode, each event advances only the features subscribed to that event type.
Bar events update bar features; quote events update quote features.  Per-event
complexity is O(n_subscribed_features_for_this_type), not O(n_total_features).

---

## 27. Profiling Hook — Extended (`last_status`)

As of Session 5, `profile_summary()` includes `last_status` per feature:

```python
engine = SpecFeatureEngine(specs, profile=True)
# ... feed events ...
summary = engine.profile_summary()
# {
#     "profile": True,
#     "features": {
#         "feat_name": {
#             "update_count":    int,
#             "skip_count":      int,
#             "late_drop_count": int,
#             "last_status":     str | None,  # NEW in Session 5
#         },
#         ...
#     }
# }
```

`last_status` reflects the `update_status` of the most recent `on_event()` call:

| Value | Meaning |
|---|---|
| `"updated"` | Feature computed a new value on the most recent event |
| `"not_ready"` | Feature has not processed enough history |
| `"skipped_missing_field"` | Required input field absent on the most recent event |
| `"late_dropped"` | Most recent event was late and dropped by lateness policy |
| `None` | No `on_event()` has been called yet (initial state) |

---

## 28. Recommended Production Data Flow

```
                  DataFeed / MarketData
                         │
                         ▼
               adapt_bar_event()          adapt_quote_tick_event()
               adapt_quote_tick_event()   (or custom adapter for trade/book events)
                         │
                         ▼
              ┌──────────────────────┐
              │   SpecFeatureEngine  │   ← specs define all features declaratively
              │   engine.warmup()    │   ← historical provider fills state
              │   engine.on_event()  │   ← O(n_features) per event, no pandas
              └──────────────────────┘
                         │
                         ▼
                  FeatureSnapshot
                  (ts_event, instrument_id, values{})
                         │
                         ▼
               Strategy / Signal Layer
               snap.value("mean20")
               snap.is_ready("rvol20")
               snap.all_ready()
```

The engine, adapters, and snapshot form a stable abstraction boundary.  All three
layers can evolve independently: the data feed can change connectors, the engine can
swap backends, and the strategy can be rewritten — as long as `FeatureSpec` and
`FeatureSnapshot` remain stable, nothing else in the chain needs to change.

---

## §29 Feature-to-Feature Dependencies — Design Rationale

Some features are not computed directly from raw market event fields. They depend
on previously-computed feature values:

- `realized_volatility_60` depends on `log_return_close`
- `spread_ratio` depends on `spread` and `mid_price`
- `zscore` depends on `rolling_mean` and `rolling_std`

### Why not let features call engine.get()

Feature implementations must **not** call `engine.get()` or access other feature
instances directly. Doing so would:

1. Make update order implicit (the called feature may not yet be updated).
2. Create hidden coupling (graph is invisible to the engine; impossible to reorder).
3. Make cycle detection impossible (A calls B which calls A → infinite recursion).

The explicit `depends_on` field in `FeatureSpec` solves all three problems:
the engine owns the dependency graph, can validate it, detect cycles, and enforce
topological update order.

---

## §30 FeatureSpec.depends_on Field

```python
@dataclass(frozen=True)
class FeatureSpec:
    name: str
    input_type: str = "bar"
    # ... existing fields ...
    depends_on: tuple[str, ...] = ()
```

Rules:
- If `depends_on` is empty: the spec describes a **raw** feature that subscribes
  to market events by `input_type`.
- If `depends_on` is non-empty: the spec describes a **derived** feature that is
  computed from other features, not directly from raw events.
- Every name in `depends_on` must refer to another feature registered in the same
  engine instance. Unknown names raise `ValueError` at engine init.
- Self-reference (`spec.name in spec.depends_on`) raises `ValueError`.
- Circular dependencies (A → B → A) raise `ValueError` with the cycle printed.
- Use `input_type = "derived"` to make derived specs explicit; any `input_type`
  is accepted for derived features (the `depends_on` field is the canonical indicator).

---

## §31 Dependency Graph Construction

`SpecFeatureEngine._build()` calls `_build_dependency_graph()` after creating
all feature instances:

```
_validate_spec_list()       # checks depends_on names exist, no self-dep
_build_dependency_graph()   # separates raw from derived, topo sorts
  → _raw_features            # dict[str, FeatureBase] — no depends_on
  → _dep_graph               # dict[str, list[str]]   — direct deps per derived feature
  → _derived_names           # list[str]              — topo-sorted derived names
```

`_topo_sort()` uses iterative DFS with three-colour marking (white/gray/black).
The cycle error includes the full cycle path:

```
ValueError: Circular dependency detected in feature graph: A -> B -> C -> A
```

Topological order guarantees: if feature B depends on feature A, A appears before
B in `_derived_names`. This holds for chains of arbitrary depth.

---

## §32 Topological Update Order and Two-Phase on_event()

Each call to `on_event(event)` has two phases:

### Phase 1 — Raw features
```python
for name, feature in self._raw_features.items():
    if feature.spec.input_type != event_input_type:
        values[name] = feature.value   # cached — event not for this type
        continue
    update = feature.update(event)
    values[name] = update.value
    dirty.add(name)
```

### Phase 2 — Derived features (topological order)
```python
ctx = DependencyContext(values)        # live reference; in-place updates visible
for name in self._derived_names:       # deps before dependents
    if not any(d in dirty for d in deps[name]):
        values[name] = feature.value   # no dep dirty, return cached
        continue
    update = feature.update_from_dependencies(ctx, event)
    values[name] = update.value
    dirty.add(name)                    # propagate dirty upward
```

`DependencyContext` holds a **live reference** to `values`. Each derived feature
that writes `values[name]` immediately makes its value visible to downstream
derived features — this is what enables multi-level chains to work with a single
context object.

---

## §33 Current-Value Semantics

For the current implementation, derived features use **current-value semantics**:

> If features A and B are both affected by event_t, and B depends on A, then
> B sees A's value **after** A was updated on event_t.

This is a consequence of processing in topological order. There is no previous-
value dependency mode in v1.

---

## §34 Latest-Ready Dependency Policy

If a dependency feature did **not** update on the current event (e.g., a bar
feature when the event is a quote), the derived feature may still use its
**latest ready cached value**:

```
scenario:
  mean_bar  — updated on bar events only
  spread    — updated on quote events only
  ratio     — depends_on=("mean_bar", "spread"), input_type="derived"

event: quote arrives at t=5
  Phase 1: spread updated → spread dirty
  Phase 2: ratio deps check → spread ∈ dirty → trigger ratio
           ctx.value("mean_bar") → latest cached value from last bar event
           ratio computes using current spread + cached mean_bar ✓
```

This is the default and only supported dependency policy in v1. Complex same-event
cross-frequency synchronization is out of scope.

---

## §35 DependencyContext

```python
class DependencyContext:
    """Read-only view passed to derived features instead of raw market events."""

    def value(self, name: str) -> float | int | bool | None: ...
    def get(self, name: str) -> FeatureValue | None: ...
    def is_ready(self, name: str) -> bool: ...
    def all_ready(self, names: list[str]) -> bool: ...
```

Derived feature implementations call `ctx.value("dep_name")`, `ctx.is_ready("dep_name")`,
etc. They must not reach into the engine via any other path.

---

## §36 Derived Feature Classes (v1)

Four built-in derived feature types (all in `nautilus_ext.features.compute.features`):

| `params["type"]` | `depends_on` arity | Formula |
|------------------|--------------------|---------|
| `"ratio"`        | exactly 2          | dep[0] / dep[1] |
| `"difference"`   | exactly 2          | dep[0] − dep[1] |
| `"sum"`          | ≥ 1                | Σ dep[i] |
| `"product"`      | ≥ 1                | Π dep[i] |

All four:
- Inherit from `_AbstractDerivedFeature` which inherits from `_AbstractFeature`.
- Implement `update_from_dependencies(ctx, source_event)` (called by engine).
- Return `FeatureValue(update_status="dependency_not_ready")` when any dep is not
  ready (no crash; `value=None, is_ready=False`).
- Are registered in `_FEATURE_CLASSES` in `backend.py` and created via the normal
  `PythonBackend.create_feature(spec)` dispatch.

### Example

```python
from nautilus_ext.features.compute.spec import FeatureSpec
from nautilus_ext.features.compute.engine import SpecFeatureEngine

specs = [
    FeatureSpec("spread",       input_type="quote",   params={"type": "spread"}),
    FeatureSpec("mid",          input_type="quote",   params={"type": "mid_price"}),
    FeatureSpec("spread_ratio", input_type="derived",
                depends_on=("spread", "mid"), params={"type": "ratio"}),
]
engine = SpecFeatureEngine(specs, stamp_process_time=False)
snap = engine.on_event(quote_event)
print(snap.value("spread_ratio"))   # spread / mid_price
```

---

## §37 update_status Values for Derived Features

| `update_status` | Meaning |
|-----------------|---------|
| `"updated"` | All deps ready; new value computed. |
| `"dependency_not_ready"` | At least one dep is not ready; `value=None, is_ready=False`. |
| `"not_ready"` | Internal rolling state not filled (if any). |

`FeatureSnapshot.statuses()` returns the `update_status` for every feature
including derived ones. `profile_summary()` includes `update_count`, `skip_count`,
`late_drop_count`, and `last_status` for derived features.

---

## §38 v1 Limitations

The following are **intentionally out of scope** for this first implementation:

1. **No expression parser.** Arbitrary formula strings like `"(A + B) / C"` are not
   supported. Compose primitive derived features instead.
2. **No previous-value dependency mode.** B always sees A's value from the same event,
   not from the previous event. This may change in a future version.
3. **No complex cross-frequency synchronization.** Mixed-frequency chains use the
   latest-ready policy; there is no per-event alignment or interpolation.
4. **No rolling-window-over-dependency feature.** A dedicated `rolling_std_of_dep`
   derived type (accumulating a dependency's history) is not yet implemented.
5. **No partial-update semantics.** If any dep of a derived feature returns
   `"dependency_not_ready"`, the entire derived feature returns `None`. There is no
   "use last known value" fallback per dependency.

Hot-path constraints are fully preserved:
- No pandas.
- No DataFrame recomputation.
- No full-history scans in `on_event()`.
- No sorting inside `on_event()`.
- Per-event complexity: O(n\_raw\_features\_matching\_type + n\_dirty\_derived\_features).
