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

## 10. Feature Catalog (PythonBackend)

| `params["type"]` / name prefix | Input type | Fields used | Warmup required | Window metadata |
|---|---|---|---|---|
| `rolling_mean` | bar | `input_field` | window bars (mandatory) | `source_event_time_ns` |
| `rolling_std` | bar | `input_field` | window bars (mandatory) | `source_event_time_ns` |
| `rolling_min` | bar | `input_field` | window bars (mandatory) | `source_event_time_ns` |
| `rolling_max` | bar | `input_field` | window bars (mandatory) | `source_event_time_ns` |
| `vwap` | bar | close/volume (configurable) | 1 bar (non-mandatory) | `source_event_time_ns`; `window_start/end_ns` when time-based |
| `simple_return` | bar | `input_field` (default: close) | 2 bars | `source_event_time_ns` |
| `log_return` | bar | `input_field` (default: close) | 2 bars | `source_event_time_ns` |
| `ewma` | bar | `input_field` | span bars (non-mandatory) | `source_event_time_ns` |
| `spread` | quote | bid_price, ask_price | 1 event | `source_event_time_ns` |
| `mid_price` | quote | bid_price, ask_price | 1 event | `source_event_time_ns` |
| `book_imbalance` | book_delta | bids/asks lists or bid_volume/ask_volume | 1 event | `source_event_time_ns` |

---

## 11. Test Coverage

148 tests in `nautilus_ext/tests/test_compute_features.py`:

- **State containers**: push, eviction, running sum, variance/std, reset, state_dict round-trip
- **TimestampConfig**: ms/us/ns legacy conversion, `require_event_time_ns_for_live` raise/skip
- **EventTimestamps**: latency_ns, processing_latency_ns, select_timestamp dispatch
- **WatermarkTracker**: monotonic advance, allowed_lateness, is_late_for, finalize, state_dict
- **StreamKey / Partitioned watermarks**: multi-instrument, multi-type independence, all_watermarks(), state_dict round-trip
- **ManualClock**: deterministic process_time_ns, processing_latency_ns, Clock protocol
- **Feature classes**: all 11 feature classes against reference implementations (1e-12 relative tolerance for mean)
- **TriggerPolicy**: on_event, on_n_bars, on_timer (interval_ns), time_semantics
- **Late event policies**: drop / log_only / update_if_not_finalized / raise / recompute_for_backtest_only
- **Window metadata**: VWAP time-window `window_start_ns`/`window_end_ns`, `source_event_time_ns` on all features
- **SpecFeatureEngine**: routing, warmup, snapshot ts_event, state_dict, reset
- **SpecDrivenFeatureEngine**: schema, FeatureEvent (ms ts_event), FeaturePipeline integration, warmup tagging
- **Backend**: dispatch by params["type"] and name prefix, backend-swappable API
