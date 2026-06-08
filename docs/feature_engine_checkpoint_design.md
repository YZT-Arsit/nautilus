# Feature Engine Checkpoint Design

> **Status**: Design document only — not yet implemented.
> The serialization primitives (`state_dict` / `load_state_dict`) are **already implemented**
> on every feature class and on `SpecFeatureEngine` itself. This document describes
> how to build a checkpoint + replay helper on top of those primitives.

---

## 1. Why `FeatureValue` Is Not Enough for Online Recovery

A `FeatureValue` snapshot stores only the **output** of a feature at one point in time:

```python
@dataclass(frozen=True)
class FeatureValue:
    name: str
    value: float | int | bool | None   # the scalar output
    is_ready: bool
    source_event_time_ns: int | None
    update_status: str | None
    # ...
```

This is sufficient for strategy consumption but **not sufficient for recovery** because:

| What is missing | Why it matters |
|-----------------|----------------|
| Ring buffer contents | `RollingMeanFeature` needs the last `window` values to correctly update on the next event. Knowing the current mean is not enough — the oldest value being evicted on the next push is unknown. |
| Running sums / sum-of-squares | `RollingWindowState` maintains `Σx` and `Σx²` for O(1) std. Recomputing these from the mean alone requires full history. |
| EWMA state | `EwmaFeature` stores `α`, `current_ewma`, and whether warmup is complete. The scalar output is the same as the EWMA, but the `alpha` and warmup flag are not in `FeatureValue`. |
| VWAP accumulators | `VwapFeature` stores cumulative `Σ(price×volume)` and `Σvolume`. The scalar VWAP cannot be decomposed back into these. |
| Watermark state | Each `StreamKey → WatermarkTracker` stores `watermark_ns` and `max_event_time_ns`. Without these, the first live event after recovery may be incorrectly classified as late. |
| Derived feature buffer | `RollingStdDerivedFeature` has its own `RollingWindowState` accumulating its dependency's values. The current std is not recoverable without the buffer. |

**Conclusion**: recovery requires `state_dict()`, not `FeatureValue`.

---

## 2. What `state_dict()` Stores

`SpecFeatureEngine.state_dict()` returns:

```python
{
    "features": {
        "rolling_mean_close": {
            "name":     "rolling_mean_close",
            "is_ready": True,
            "cached":   {"name": ..., "value": 101.2, "is_ready": True, ...},
            "rolling":  {
                "buffer":  [100.0, 100.5, 101.0, 101.3, 101.8],  # deque contents
                "sum":     504.6,
                "count":   5,
                "maxlen":  5,
            }
        },
        "realized_vol": {
            "rolling": {
                "buffer":      [0.0049, 0.0051, 0.0050, 0.0052, 0.0048],
                "sum":         0.0250,
                "sum_squares": 0.000001250,
                "count":       5,
                "maxlen":      5,
                "track_squares": True,
            }
        },
        # ... all other features
    },
    "watermarks": [
        {
            "key":   {"instrument_id": "BTC/USDT", "input_type": "bar", "source": None},
            "state": {"watermark_ns": 12_000_000_000, "max_event_time_ns": 12_000_000_000}
        },
        {
            "key":   {"instrument_id": "BTC/USDT", "input_type": "quote", "source": None},
            "state": {"watermark_ns": 12_500_000_000, "max_event_time_ns": 12_500_000_000}
        },
    ]
}
```

This is a pure-Python dict of primitive types (int, float, bool, str, list, None). It is JSON-serializable without custom encoders.

---

## 3. Proposed Checkpoint API

```python
import json
import pathlib

def save_checkpoint(engine: SpecFeatureEngine, path: str | pathlib.Path) -> None:
    """Serialize engine state to a JSON file."""
    state = engine.state_dict()
    with open(path, "w") as fh:
        json.dump(state, fh, indent=2)


def load_checkpoint(
    engine: SpecFeatureEngine, path: str | pathlib.Path
) -> None:
    """Restore engine state from a JSON checkpoint file.

    The engine must have been constructed with the same FeatureSpec list.
    After this call, engine.on_event() resumes as if the saved state
    was the result of re-running all prior warmup + live events.
    """
    with open(path) as fh:
        state = json.load(fh)
    engine.load_state_dict(state)
```

These two functions are the entire API. They delegate entirely to the existing `state_dict()` / `load_state_dict()` primitives.

---

## 4. Checkpoint + Replay Workflow

```
Historical events (days 1–N)
     │
     ▼
engine.warmup(historical_events)       ← standard warmup
     │
     ▼
save_checkpoint(engine, "checkpoint.json")   ← daily snapshot

--- restart / recovery ---

engine = SpecFeatureEngine(same_specs)
load_checkpoint(engine, "checkpoint.json")    ← restore state

# Optional: replay events since checkpoint to catch up
for event in events_since_checkpoint:
    engine.on_event(adapted_event)

# Live trading resumes normally
for event in live_feed:
    snap = engine.on_event(adapted_event)
    signal = generate_signal(snap)
```

This is called **checkpoint + partial replay**: rather than replaying all history from day 1, replay only events from the last checkpoint (e.g., 1 day) to reach the present state.

---

## 5. Checkpoint Frequency and Size

| Parameter | Recommendation |
|-----------|---------------|
| Checkpoint interval | Once per trading day (at session close) |
| Replay window | Last N events since the last checkpoint, where N is the maximum `warmup_required().n_events` across all features |
| File size | Proportional to `n_features × window`. For 100 features with window=200: ~40 KB of JSON. |
| Format | JSON (human-readable, debuggable). Upgrade to MessagePack or protobuf when file size becomes a concern. |

---

## 6. Correctness Requirements

### The specs must match

`load_state_dict()` does not validate that the engine's specs match the checkpoint's feature names. If a feature is added or removed between checkpoint and restore, `load_state_dict()` silently ignores unknown names and leaves new features in their initial state.

**Mitigation**: store the spec list or a hash of it alongside the checkpoint:

```python
def save_checkpoint(engine, path):
    state = engine.state_dict()
    state["_meta"] = {
        "feature_names": engine.feature_names(),
        "n_features": len(engine.feature_names()),
    }
    # ... write to file
```

### Replay events must be in order

The replay feed must yield events in ascending `event_time_ns` order, matching the ordering contract of `engine.warmup()`. Out-of-order replay will advance watermarks incorrectly.

### Checkpoint must be taken at a clean boundary

Avoid checkpointing mid-bar (e.g., during a partial VWAP accumulation) if the event stream can be replayed from a bar close. For tick data this constraint is relaxed — the state_dict captures accumulator state correctly at any point.

---

## 7. Limitations of the Current Design

| Limitation | Detail |
|------------|--------|
| No automatic schema migration | If `window` changes between checkpoint and restore, the ring buffer may have a different `maxlen`. `load_state_dict()` overwrites the buffer without validation. |
| No compressed format | Large windows (e.g., 10,000 bars) produce large JSON blobs. An optional `msgpack` serializer would reduce this by ~3×. |
| No incremental (delta) checkpoint | Each save writes the full state. For very large feature sets, an incremental format (only changed features) would reduce I/O. |
| No atomic write | `json.dump()` to a file is not crash-safe. Use a write-then-rename pattern for production: write to `checkpoint.tmp` then `os.replace()`. |
| Watermarks are restored exactly | If the live feed has a different source (e.g., a new exchange connection), `StreamKey.source` may differ from the checkpoint. The engine will create a new watermark for the new source key. This is correct but means the old watermark (from checkpoint) is orphaned. |

---

## 8. Example: Atomic Write Pattern (Production-Safe)

```python
import json
import os
import pathlib

def save_checkpoint_atomic(engine, path: str) -> None:
    """Write checkpoint atomically — safe against mid-write crashes."""
    state = engine.state_dict()
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(state, fh)
    os.replace(tmp, path)   # atomic on POSIX
```

---

*This document describes planned functionality. Implementation is straightforward given the existing `state_dict` / `load_state_dict` primitives — estimated effort: 0.5 day including tests.*
