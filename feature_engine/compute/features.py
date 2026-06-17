"""
Concrete incremental feature implementations (pure Python backend).

All timestamps in nanoseconds. Each feature selects which timestamp to use
for time-based trigger checks and window eviction via
spec.trigger.time_semantics:
    "event_time"   → EventTimestamps.event_time_ns  (default, use for windows)
    "receive_time" → EventTimestamps.receive_time_ns (latency measurement)
    "process_time" → EventTimestamps.process_time_ns (system monitoring only)

Time-window state containers (TimeWindowState, VWAPState time mode) expect
timestamps in nanoseconds. The window_unit→nanosecond conversion is handled
in VWAPFeature and any future time-window feature constructors.

Features implemented
--------------------
Bar-input (input_type="bar"):
    RollingMeanFeature        — rolling mean of one bar field
    RollingStdFeature         — rolling sample std of one bar field
    RollingMinFeature         — rolling minimum of one bar field
    RollingMaxFeature         — rolling maximum of one bar field
    RollingSumFeature         — generic rolling sum over any input field
    RollingVolumeSumFeature   — rolling sum alias with default input_field='volume'
    VWAPFeature               — VWAP; rolling count, rolling time, or session
    SimpleReturnFeature       — (close_t - close_{t-1}) / close_{t-1}
    LogReturnFeature          — log(close_t / close_{t-1})
    EWMAFeature               — exponentially weighted moving average

Quote-input (input_type="quote"):
    SpreadFeature         — ask_price - bid_price
    MidPriceFeature       — (ask_price + bid_price) / 2

Orderbook-input (input_type="book_delta"):
    BookImbalanceFeature  — (bid_vol - ask_vol) / (bid_vol + ask_vol)
"""
from __future__ import annotations

import math
from typing import Any

from feature_engine.compute.spec import (
    FeatureSpec,
    FeatureUpdate,
    FeatureValue,
    WarmupRequirement,
)
from feature_engine.compute.state import (
    EWMAState,
    RollingWindowState,
    TimeWindowState,
    VWAPState,
)
from feature_engine.compute.timestamps import extract_timestamps, select_timestamp


# ---------------------------------------------------------------------------
# Timestamp extraction helpers
# ---------------------------------------------------------------------------

def _ts_ns(event: Any, time_semantics: str = "event_time") -> int:
    """Extract the appropriate nanosecond timestamp from any event.

    Uses extract_timestamps() for field resolution and falls back from
    ns-precision fields to ts_event (ms) × 1_000_000.
    """
    ts = extract_timestamps(event)
    return select_timestamp(ts, time_semantics)


def _field(event: Any, name: str | None) -> float | None:
    """Extract a named float field from an event (duck-typed)."""
    if name is None:
        return None
    v = getattr(event, name, None)
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Abstract base — shared trigger/caching scaffolding
# ---------------------------------------------------------------------------

class _AbstractFeature:
    """Internal mixin: spec storage, event counting, trigger checking, cache.

    All concrete feature classes inherit from this. Timestamp handling is
    delegated to _ts_ns() which selects the right field based on
    spec.trigger.time_semantics.
    """

    def __init__(self, spec: FeatureSpec) -> None:
        self._spec = spec
        self._event_count: int = 0
        self._last_trigger_ts: int = 0  # nanoseconds
        self._cached = FeatureValue(name=spec.name, value=None, is_ready=False)

    @property
    def spec(self) -> FeatureSpec:
        return self._spec

    @property
    def value(self) -> FeatureValue:
        return self._cached

    # ------------------------------------------------------------------
    # Trigger policy (nanosecond timestamps)
    # ------------------------------------------------------------------

    def _should_trigger(self, ts_ns: int) -> bool:
        """Check whether the trigger condition fires for this event."""
        kind = self._spec.trigger.kind
        if kind in ("on_event", "on_bar_close"):
            return True
        n = self._spec.trigger.n
        if kind in ("on_n_events", "on_n_bars"):
            return n is not None and (self._event_count % n == 0)
        interval_ns = self._spec.trigger.interval_ns or 0
        if kind in ("on_timer", "on_window_close"):
            return (ts_ns - self._last_trigger_ts) >= interval_ns
        return True

    # ------------------------------------------------------------------
    # Cache helpers
    # ------------------------------------------------------------------

    def _emit(
        self,
        raw: float | None,
        ready: bool,
        triggered: bool,
        *,
        window_start_ns: int | None = None,
        window_end_ns: int | None = None,
        source_event_time_ns: int | None = None,
        update_status: str | None = None,
    ) -> FeatureUpdate:
        fv = FeatureValue(
            name=self._spec.name,
            value=raw if ready else None,
            is_ready=ready,
            window_start_ns=window_start_ns,
            window_end_ns=window_end_ns,
            source_event_time_ns=source_event_time_ns,
            update_status=update_status,
        )
        self._cached = fv
        return FeatureUpdate(value=fv, triggered=triggered)

    def _no_change(self) -> FeatureUpdate:
        return FeatureUpdate(value=self._cached, triggered=False)

    def _missing_field(self, field_name: str | None) -> FeatureUpdate:
        """Return cached value with update_status='skipped_missing_field'.

        Does NOT update self._cached — the skip is visible in the FeatureUpdate
        return value but the cached state is left unchanged.
        """
        fv = FeatureValue(
            name=self._cached.name,
            value=self._cached.value,
            is_ready=self._cached.is_ready,
            window_start_ns=self._cached.window_start_ns,
            window_end_ns=self._cached.window_end_ns,
            source_event_time_ns=self._cached.source_event_time_ns,
            update_status="skipped_missing_field",
            reason=f"Field '{field_name}' not found on event",
            source_field=field_name,
        )
        return FeatureUpdate(value=fv, triggered=False)

    # ------------------------------------------------------------------
    # State helpers
    # ------------------------------------------------------------------

    def _base_state(self) -> dict:
        return {
            "event_count": self._event_count,
            "last_trigger_ts": self._last_trigger_ts,
        }

    def _load_base(self, state: dict) -> None:
        self._event_count = state.get("event_count", 0)
        self._last_trigger_ts = state.get("last_trigger_ts", 0)
        self._cached = FeatureValue(name=self._spec.name, value=None, is_ready=False)

    def _reset_base(self) -> None:
        self._event_count = 0
        self._last_trigger_ts = 0
        self._cached = FeatureValue(name=self._spec.name, value=None, is_ready=False)


# ---------------------------------------------------------------------------
# Bar-input features
# ---------------------------------------------------------------------------

class RollingMeanFeature(_AbstractFeature):
    """Rolling mean of one bar field. O(1) update via running sum.

    Time semantics: count-based (bars). Trigger time uses time_semantics
    for on_timer / on_window_close variants.
    """

    def __init__(self, spec: FeatureSpec) -> None:
        super().__init__(spec)
        self._state = RollingWindowState(maxlen=spec.window or 1)

    def warmup_required(self) -> WarmupRequirement:
        return WarmupRequirement(n_events=self._spec.window or 1, unit=self._spec.window_unit or "bars")

    @property
    def is_ready(self) -> bool:
        return self._state.is_full

    def reset(self) -> None:
        self._state.reset()
        self._reset_base()

    def update(self, event: Any) -> FeatureUpdate:
        self._event_count += 1
        ts_ns = _ts_ns(event, self._spec.trigger.time_semantics)
        v = _field(event, self._spec.input_field)
        if v is None:
            return self._no_change()
        self._state.push(v)
        triggered = self._should_trigger(ts_ns)
        if triggered:
            self._last_trigger_ts = ts_ns
        return self._emit(
            self._state.mean, self._state.is_full, triggered,
            source_event_time_ns=ts_ns,
        )

    def state_dict(self) -> dict:
        return {**self._base_state(), "rolling": self._state.state_dict()}

    def load_state_dict(self, state: dict) -> None:
        self._load_base(state)
        self._state.load_state_dict(state["rolling"])
        if self._state.is_full:
            self._cached = FeatureValue(name=self._spec.name, value=self._state.mean, is_ready=True)


class RollingStdFeature(_AbstractFeature):
    """Rolling sample std. O(1) update via running sum-of-squares."""

    def __init__(self, spec: FeatureSpec) -> None:
        super().__init__(spec)
        self._state = RollingWindowState(maxlen=spec.window or 2, track_squares=True)

    def warmup_required(self) -> WarmupRequirement:
        return WarmupRequirement(n_events=self._spec.window or 2, unit=self._spec.window_unit or "bars")

    @property
    def is_ready(self) -> bool:
        return self._state.is_full

    def reset(self) -> None:
        self._state.reset()
        self._reset_base()

    def update(self, event: Any) -> FeatureUpdate:
        self._event_count += 1
        ts_ns = _ts_ns(event, self._spec.trigger.time_semantics)
        v = _field(event, self._spec.input_field)
        if v is None:
            return self._no_change()
        self._state.push(v)
        triggered = self._should_trigger(ts_ns)
        if triggered:
            self._last_trigger_ts = ts_ns
        return self._emit(
            self._state.std, self._state.is_full, triggered,
            source_event_time_ns=ts_ns,
        )

    def state_dict(self) -> dict:
        return {**self._base_state(), "rolling": self._state.state_dict()}

    def load_state_dict(self, state: dict) -> None:
        self._load_base(state)
        self._state.load_state_dict(state["rolling"])
        if self._state.is_full:
            self._cached = FeatureValue(name=self._spec.name, value=self._state.std, is_ready=True)


class RollingMinFeature(_AbstractFeature):
    """Rolling minimum. O(window) scan for min."""

    def __init__(self, spec: FeatureSpec) -> None:
        super().__init__(spec)
        self._state = RollingWindowState(maxlen=spec.window or 1)

    def warmup_required(self) -> WarmupRequirement:
        return WarmupRequirement(n_events=self._spec.window or 1, unit=self._spec.window_unit or "bars")

    @property
    def is_ready(self) -> bool:
        return self._state.is_full

    def reset(self) -> None:
        self._state.reset()
        self._reset_base()

    def update(self, event: Any) -> FeatureUpdate:
        self._event_count += 1
        ts_ns = _ts_ns(event, self._spec.trigger.time_semantics)
        v = _field(event, self._spec.input_field)
        if v is None:
            return self._no_change()
        self._state.push(v)
        triggered = self._should_trigger(ts_ns)
        if triggered:
            self._last_trigger_ts = ts_ns
        return self._emit(
            self._state.min, self._state.is_full, triggered,
            source_event_time_ns=ts_ns,
        )

    def state_dict(self) -> dict:
        return {**self._base_state(), "rolling": self._state.state_dict()}

    def load_state_dict(self, state: dict) -> None:
        self._load_base(state)
        self._state.load_state_dict(state["rolling"])
        if self._state.is_full:
            self._cached = FeatureValue(name=self._spec.name, value=self._state.min, is_ready=True)


class RollingMaxFeature(_AbstractFeature):
    """Rolling maximum. O(window) scan for max."""

    def __init__(self, spec: FeatureSpec) -> None:
        super().__init__(spec)
        self._state = RollingWindowState(maxlen=spec.window or 1)

    def warmup_required(self) -> WarmupRequirement:
        return WarmupRequirement(n_events=self._spec.window or 1, unit=self._spec.window_unit or "bars")

    @property
    def is_ready(self) -> bool:
        return self._state.is_full

    def reset(self) -> None:
        self._state.reset()
        self._reset_base()

    def update(self, event: Any) -> FeatureUpdate:
        self._event_count += 1
        ts_ns = _ts_ns(event, self._spec.trigger.time_semantics)
        v = _field(event, self._spec.input_field)
        if v is None:
            return self._no_change()
        self._state.push(v)
        triggered = self._should_trigger(ts_ns)
        if triggered:
            self._last_trigger_ts = ts_ns
        return self._emit(
            self._state.max, self._state.is_full, triggered,
            source_event_time_ns=ts_ns,
        )

    def state_dict(self) -> dict:
        return {**self._base_state(), "rolling": self._state.state_dict()}

    def load_state_dict(self, state: dict) -> None:
        self._load_base(state)
        self._state.load_state_dict(state["rolling"])
        if self._state.is_full:
            self._cached = FeatureValue(name=self._spec.name, value=self._state.max, is_ready=True)


class VWAPFeature(_AbstractFeature):
    """Volume-weighted average price.

    Supports three modes via window / window_unit:
    - Session (unbounded): window=None, window_unit=None.
    - Count-based rolling: window=N, window_unit="bars"/"events".
    - Time-based rolling: window=N, window_unit in seconds/milliseconds/minutes/nanoseconds.
      Window eviction uses time_semantics timestamp.

    Time-based windows use nanoseconds internally.

    Parameters (from FeatureSpec)
    -----------------------------
    window : int | None     — window size
    window_unit : str       — "bars", "events", "nanoseconds", "milliseconds", "seconds", "minutes"
    params["price_field"]   — bar field for price (default "close")
    params["volume_field"]  — bar field for volume (default "volume")
    """

    _NS_PER_UNIT: dict[str, int] = {
        "nanoseconds": 1,
        "milliseconds": 1_000_000,
        "seconds": 1_000_000_000,
        "minutes": 60_000_000_000,
    }

    def __init__(self, spec: FeatureSpec) -> None:
        super().__init__(spec)
        window = spec.window
        unit = spec.window_unit or "bars"
        window_ns: int | None = None
        count_window: int | None = None
        if window is not None:
            if unit in self._NS_PER_UNIT:
                window_ns = window * self._NS_PER_UNIT[unit]
            else:
                count_window = window
        self._state = VWAPState(window=count_window, window_ns=window_ns)
        self._price_field: str = spec.params.get("price_field", "close")
        self._volume_field: str = spec.params.get("volume_field", "volume")

    def warmup_required(self) -> WarmupRequirement:
        n = self._spec.window or 1
        return WarmupRequirement(n_events=n, unit=self._spec.window_unit or "bars", mandatory=False)

    @property
    def is_ready(self) -> bool:
        return self._state.count > 0

    def reset(self) -> None:
        self._state.reset()
        self._reset_base()

    def update(self, event: Any) -> FeatureUpdate:
        self._event_count += 1
        ts_ns = _ts_ns(event, self._spec.trigger.time_semantics)
        price = _field(event, self._price_field)
        volume = _field(event, self._volume_field)
        if price is None or volume is None:
            return self._no_change()
        self._state.push(price, volume, ts_ns=ts_ns)
        triggered = self._should_trigger(ts_ns)
        if triggered:
            self._last_trigger_ts = ts_ns
        # Populate window bounds for time-based VWAP windows
        window_ns = self._state._window_ns
        window_start_ns = (ts_ns - window_ns) if window_ns is not None else None
        window_end_ns = ts_ns if window_ns is not None else None
        return self._emit(
            self._state.vwap, True, triggered,
            window_start_ns=window_start_ns,
            window_end_ns=window_end_ns,
            source_event_time_ns=ts_ns,
        )

    def state_dict(self) -> dict:
        return {**self._base_state(), "vwap": self._state.state_dict()}

    def load_state_dict(self, state: dict) -> None:
        self._load_base(state)
        self._state.load_state_dict(state["vwap"])
        if self._state.count > 0:
            self._cached = FeatureValue(name=self._spec.name, value=self._state.vwap, is_ready=True)


class RollingSumFeature(_AbstractFeature):
    """Generic rolling sum of any named input field over a fixed count window.

    Uses RollingWindowState for O(1) updates — no full-window scan on push.
    Sets update_status="updated"/"not_ready" on each emit and
    update_status="skipped_missing_field" when the field is absent on the event.

    Parameters
    ----------
    input_field : str | None
        Field to sum (from spec.input_field).  When None, every event returns
        a skipped_missing_field update — use a concrete field name in the spec.
    window : int
        Number of events in the lookback window.

    Subclasses
    ----------
    RollingVolumeSumFeature — same feature with _DEFAULT_FIELD = "volume".
    """

    _DEFAULT_FIELD: str | None = None  # subclasses override to supply a fallback field

    def __init__(self, spec: FeatureSpec) -> None:
        super().__init__(spec)
        self._state = RollingWindowState(maxlen=spec.window or 1)
        self._field_name: str | None = spec.input_field or self._DEFAULT_FIELD
        if self._field_name is None:
            raise ValueError(
                f"RollingSumFeature requires spec.input_field to be set (or use a "
                f"subclass that defines _DEFAULT_FIELD, e.g. RollingVolumeSumFeature). "
                f"Spec {spec.name!r} has neither. "
                f"Example fix: FeatureSpec(..., input_field='volume')."
            )

    def warmup_required(self) -> WarmupRequirement:
        return WarmupRequirement(n_events=self._spec.window or 1, unit=self._spec.window_unit or "bars")

    @property
    def is_ready(self) -> bool:
        return self._state.is_full

    def reset(self) -> None:
        self._state.reset()
        self._reset_base()

    def update(self, event: Any) -> FeatureUpdate:
        self._event_count += 1
        ts_ns = _ts_ns(event, self._spec.trigger.time_semantics)
        v = _field(event, self._field_name)
        if v is None:
            return self._missing_field(self._field_name)
        self._state.push(v)
        triggered = self._should_trigger(ts_ns)
        if triggered:
            self._last_trigger_ts = ts_ns
        ready = self._state.is_full
        return self._emit(
            self._state.sum, ready, triggered,
            source_event_time_ns=ts_ns,
            update_status="updated" if ready else "not_ready",
        )

    def state_dict(self) -> dict:
        return {**self._base_state(), "rolling": self._state.state_dict()}

    def load_state_dict(self, state: dict) -> None:
        self._load_base(state)
        self._state.load_state_dict(state["rolling"])
        if self._state.is_full:
            self._cached = FeatureValue(name=self._spec.name, value=self._state.sum, is_ready=True)


class RollingVolumeSumFeature(RollingSumFeature):
    """Compatibility alias for RollingSumFeature with default input_field='volume'.

    Identical to RollingSumFeature when spec.input_field is None — the field
    defaults to "volume" instead of being absent.  When spec.input_field is
    set explicitly it is used as-is (e.g. "ask_size" for quote events).

    Keep using this class when the semantic intent is "sum of volume".
    Use RollingSumFeature directly for generic field aggregation.
    """

    _DEFAULT_FIELD = "volume"


class SimpleReturnFeature(_AbstractFeature):
    """Simple close-to-close return: (close_t - close_{t-1}) / close_{t-1}."""

    def __init__(self, spec: FeatureSpec) -> None:
        super().__init__(spec)
        self._prev: float | None = None

    def warmup_required(self) -> WarmupRequirement:
        return WarmupRequirement(n_events=2, unit="bars")

    @property
    def is_ready(self) -> bool:
        return self._prev is not None and self._cached.is_ready

    def reset(self) -> None:
        self._prev = None
        self._reset_base()

    def update(self, event: Any) -> FeatureUpdate:
        self._event_count += 1
        ts_ns = _ts_ns(event, self._spec.trigger.time_semantics)
        cur = _field(event, self._spec.input_field or "close")
        if cur is None:
            return self._no_change()
        triggered = self._should_trigger(ts_ns)
        if triggered:
            self._last_trigger_ts = ts_ns
        if self._prev is None or self._prev == 0.0:
            self._prev = cur
            return self._emit(None, False, False, source_event_time_ns=ts_ns)
        ret = (cur - self._prev) / self._prev
        self._prev = cur
        return self._emit(ret, True, triggered, source_event_time_ns=ts_ns)

    def state_dict(self) -> dict:
        return {**self._base_state(), "prev": self._prev}

    def load_state_dict(self, state: dict) -> None:
        self._load_base(state)
        self._prev = state.get("prev")


class LogReturnFeature(_AbstractFeature):
    """Log return: log(close_t / close_{t-1})."""

    def __init__(self, spec: FeatureSpec) -> None:
        super().__init__(spec)
        self._prev: float | None = None

    def warmup_required(self) -> WarmupRequirement:
        return WarmupRequirement(n_events=2, unit="bars")

    @property
    def is_ready(self) -> bool:
        return self._prev is not None and self._cached.is_ready

    def reset(self) -> None:
        self._prev = None
        self._reset_base()

    def update(self, event: Any) -> FeatureUpdate:
        self._event_count += 1
        ts_ns = _ts_ns(event, self._spec.trigger.time_semantics)
        cur = _field(event, self._spec.input_field or "close")
        if cur is None:
            return self._no_change()
        triggered = self._should_trigger(ts_ns)
        if triggered:
            self._last_trigger_ts = ts_ns
        if self._prev is None or self._prev <= 0.0 or cur <= 0.0:
            self._prev = cur
            return self._emit(None, False, False, source_event_time_ns=ts_ns)
        ret = math.log(cur / self._prev)
        self._prev = cur
        return self._emit(ret, True, triggered, source_event_time_ns=ts_ns)

    def state_dict(self) -> dict:
        return {**self._base_state(), "prev": self._prev}

    def load_state_dict(self, state: dict) -> None:
        self._load_base(state)
        self._prev = state.get("prev")


class EWMAFeature(_AbstractFeature):
    """Exponentially weighted moving average of one bar field."""

    def __init__(self, spec: FeatureSpec) -> None:
        super().__init__(spec)
        alpha = spec.params.get("alpha")
        span = spec.params.get("span") or spec.window or 10
        self._state = EWMAState(
            span=None if alpha else int(span),
            alpha=float(alpha) if alpha else None,
        )

    def warmup_required(self) -> WarmupRequirement:
        span = self._spec.params.get("span") or self._spec.window or 10
        return WarmupRequirement(n_events=int(span), unit="bars", mandatory=False)

    @property
    def is_ready(self) -> bool:
        return self._state.value is not None

    def reset(self) -> None:
        self._state.reset()
        self._reset_base()

    def update(self, event: Any) -> FeatureUpdate:
        self._event_count += 1
        ts_ns = _ts_ns(event, self._spec.trigger.time_semantics)
        v = _field(event, self._spec.input_field)
        if v is None:
            return self._no_change()
        self._state.push(v)
        triggered = self._should_trigger(ts_ns)
        if triggered:
            self._last_trigger_ts = ts_ns
        return self._emit(
            self._state.value, True, triggered,
            source_event_time_ns=ts_ns,
        )

    def state_dict(self) -> dict:
        return {**self._base_state(), "ewma": self._state.state_dict()}

    def load_state_dict(self, state: dict) -> None:
        self._load_base(state)
        self._state.load_state_dict(state["ewma"])
        if self._state.value is not None:
            self._cached = FeatureValue(name=self._spec.name, value=self._state.value, is_ready=True)


# ---------------------------------------------------------------------------
# Quote-input features
# ---------------------------------------------------------------------------

class SpreadFeature(_AbstractFeature):
    """Bid-ask spread: ask_price - bid_price."""

    def __init__(self, spec: FeatureSpec) -> None:
        super().__init__(spec)

    def warmup_required(self) -> WarmupRequirement:
        return WarmupRequirement(n_events=1, unit="events")

    @property
    def is_ready(self) -> bool:
        return self._cached.is_ready

    def reset(self) -> None:
        self._reset_base()

    def update(self, event: Any) -> FeatureUpdate:
        self._event_count += 1
        ts_ns = _ts_ns(event, self._spec.trigger.time_semantics)
        bid = _field(event, "bid_price")
        ask = _field(event, "ask_price")
        if bid is None or ask is None:
            return self._no_change()
        triggered = self._should_trigger(ts_ns)
        if triggered:
            self._last_trigger_ts = ts_ns
        return self._emit(ask - bid, True, triggered, source_event_time_ns=ts_ns)

    def state_dict(self) -> dict:
        return self._base_state()

    def load_state_dict(self, state: dict) -> None:
        self._load_base(state)


class MidPriceFeature(_AbstractFeature):
    """Mid price: (ask_price + bid_price) / 2."""

    def __init__(self, spec: FeatureSpec) -> None:
        super().__init__(spec)

    def warmup_required(self) -> WarmupRequirement:
        return WarmupRequirement(n_events=1, unit="events")

    @property
    def is_ready(self) -> bool:
        return self._cached.is_ready

    def reset(self) -> None:
        self._reset_base()

    def update(self, event: Any) -> FeatureUpdate:
        self._event_count += 1
        ts_ns = _ts_ns(event, self._spec.trigger.time_semantics)
        bid = _field(event, "bid_price")
        ask = _field(event, "ask_price")
        if bid is None or ask is None:
            return self._no_change()
        triggered = self._should_trigger(ts_ns)
        if triggered:
            self._last_trigger_ts = ts_ns
        return self._emit((ask + bid) / 2.0, True, triggered, source_event_time_ns=ts_ns)

    def state_dict(self) -> dict:
        return self._base_state()

    def load_state_dict(self, state: dict) -> None:
        self._load_base(state)


# ---------------------------------------------------------------------------
# Order-book features
# ---------------------------------------------------------------------------

class BookImbalanceFeature(_AbstractFeature):
    """Order-book volume imbalance: (bid_vol - ask_vol) / (bid_vol + ask_vol)."""

    def __init__(self, spec: FeatureSpec) -> None:
        super().__init__(spec)

    def warmup_required(self) -> WarmupRequirement:
        return WarmupRequirement(n_events=1, unit="events")

    @property
    def is_ready(self) -> bool:
        return self._cached.is_ready

    def reset(self) -> None:
        self._reset_base()

    def update(self, event: Any) -> FeatureUpdate:
        self._event_count += 1
        ts_ns = _ts_ns(event, self._spec.trigger.time_semantics)
        bid_vol: float | None = None
        ask_vol: float | None = None
        bids = getattr(event, "bids", None)
        asks = getattr(event, "asks", None)
        if bids is not None and asks is not None:
            bid_vol = sum(size for _, size in bids) if bids else 0.0
            ask_vol = sum(size for _, size in asks) if asks else 0.0
        else:
            bid_vol = _field(event, "bid_volume")
            ask_vol = _field(event, "ask_volume")
        if bid_vol is None or ask_vol is None:
            return self._no_change()
        total = bid_vol + ask_vol
        imbalance = (bid_vol - ask_vol) / total if total > 0.0 else None
        triggered = self._should_trigger(ts_ns)
        if triggered:
            self._last_trigger_ts = ts_ns
        return self._emit(
            imbalance, imbalance is not None, triggered,
            source_event_time_ns=ts_ns,
        )

    def state_dict(self) -> dict:
        return self._base_state()

    def load_state_dict(self, state: dict) -> None:
        self._load_base(state)


# ---------------------------------------------------------------------------
# Realized volatility
# ---------------------------------------------------------------------------

class RealizedVolatilityFeature(_AbstractFeature):
    """Realized volatility: rolling sample std of log close-to-close returns.

    Computes std(log(close_t / close_{t-1})) over a count-based rolling window
    of N returns, which requires N+1 consecutive bar events.

    State: O(1) update via RollingWindowState (running sum + sum-of-squares).
    Sets update_status="updated"/"not_ready"/"skipped_missing_field" on every call.

    Parameters (from FeatureSpec)
    -----------------------------
    input_field : str   — bar field to use as price series (default "close").
    window : int        — number of log returns in the rolling window.
                          warmup_required().n_events == window + 1.
    """

    def __init__(self, spec: FeatureSpec) -> None:
        super().__init__(spec)
        self._state = RollingWindowState(maxlen=spec.window or 20, track_squares=True)
        self._prev: float | None = None
        self._field_name: str = spec.input_field or "close"

    def warmup_required(self) -> WarmupRequirement:
        n = (self._spec.window or 20) + 1  # window returns need window+1 bars
        return WarmupRequirement(n_events=n, unit=self._spec.window_unit or "bars")

    @property
    def is_ready(self) -> bool:
        return self._state.is_full

    def reset(self) -> None:
        self._state.reset()
        self._prev = None
        self._reset_base()

    def update(self, event: Any) -> FeatureUpdate:
        self._event_count += 1
        ts_ns = _ts_ns(event, self._spec.trigger.time_semantics)
        cur = _field(event, self._field_name)
        if cur is None:
            return self._missing_field(self._field_name)

        triggered = self._should_trigger(ts_ns)
        if triggered:
            self._last_trigger_ts = ts_ns

        if self._prev is None or self._prev <= 0.0 or cur <= 0.0:
            self._prev = cur
            return self._emit(
                None, False, triggered,
                source_event_time_ns=ts_ns,
                update_status="not_ready",
            )

        log_ret = math.log(cur / self._prev)
        self._prev = cur
        self._state.push(log_ret)

        ready = self._state.is_full
        return self._emit(
            self._state.std if ready else None,
            ready,
            triggered,
            source_event_time_ns=ts_ns,
            update_status="updated" if ready else "not_ready",
        )

    def state_dict(self) -> dict:
        return {
            **self._base_state(),
            "rolling": self._state.state_dict(),
            "prev": self._prev,
        }

    def load_state_dict(self, state: dict) -> None:
        self._load_base(state)
        self._state.load_state_dict(state["rolling"])
        self._prev = state.get("prev")
        if self._state.is_full:
            self._cached = FeatureValue(
                name=self._spec.name, value=self._state.std, is_ready=True,
            )


# ---------------------------------------------------------------------------
# Dependency context — passed to derived features instead of raw events
# ---------------------------------------------------------------------------

class DependencyContext:
    """Read-only view of the current feature values, passed to derived features.

    Derived features receive this object instead of the raw market event.
    They must not call engine internals directly — doing so would create hidden
    coupling, make update order implicit, and make cycle detection impossible.

    The dict is held by reference and updated in-place as the engine processes
    each derived feature in topological order.  Reads always reflect the latest
    values for all features that have already been updated in this event turn.

    Current-value semantics
    -----------------------
    If feature A and feature B are both processed for event_t, and B depends on A,
    B is guaranteed to see A's value from event_t (not event_{t-1}) because the
    engine processes A before B (topological order).

    Latest-ready semantics
    ----------------------
    If A did not update on event_t (e.g., A is a bar feature and event_t is a
    quote event), B sees A's most-recently-computed ready value.  This supports
    cross-frequency derived features.
    """

    __slots__ = ("_values",)

    def __init__(self, values: dict) -> None:
        self._values = values

    def value(self, name: str) -> float | int | bool | None:
        """Return the scalar for a named dependency, or None if absent or not ready."""
        fv = self._values.get(name)
        return fv.value if fv is not None else None

    def get(self, name: str):
        """Return the full FeatureValue for a named dependency, or None if absent."""
        return self._values.get(name)

    def is_ready(self, name: str) -> bool:
        """True if the named dependency exists and has is_ready=True."""
        fv = self._values.get(name)
        return fv is not None and fv.is_ready

    def all_ready(self, names) -> bool:
        """True if every name in *names* is ready."""
        return all(self.is_ready(n) for n in names)


# ---------------------------------------------------------------------------
# Abstract base for derived features
# ---------------------------------------------------------------------------

class _AbstractDerivedFeature(_AbstractFeature):
    """Base class for features that compute from other feature values.

    Derived features:
    - Do NOT subscribe to raw market events (engine skips them in routing).
    - Implement ``update_from_dependencies(ctx, source_event)`` instead.
    - Have ``spec.depends_on`` non-empty.

    The ``update()`` stub is present for protocol compatibility; the engine
    never calls it on derived features.
    """

    def warmup_required(self) -> WarmupRequirement:
        return WarmupRequirement(n_events=0, unit="events", mandatory=False)

    def update(self, event: Any) -> FeatureUpdate:
        # Engine routes to update_from_dependencies(); this stub satisfies FeatureBase.
        return self._no_change()

    def update_from_dependencies(
        self, ctx: DependencyContext, source_event: Any
    ) -> FeatureUpdate:
        """Compute a new value from dependency context.

        Must be O(1).  Called by the engine in topological order after all
        raw-event features have been updated for this event turn.
        """
        raise NotImplementedError  # pragma: no cover

    def _dep_not_ready(self, dep_name: str, ts_ns: int) -> FeatureUpdate:
        """Emit a dependency_not_ready value without touching internal state."""
        fv = FeatureValue(
            name=self._spec.name,
            value=None,
            is_ready=False,
            source_event_time_ns=ts_ns,
            update_status="dependency_not_ready",
            reason=f"Dependency {dep_name!r} is not ready",
        )
        return FeatureUpdate(value=fv, triggered=False)


# ---------------------------------------------------------------------------
# Concrete derived feature classes
# ---------------------------------------------------------------------------

class RatioDerivedFeature(_AbstractDerivedFeature):
    """ratio: value(depends_on[0]) / value(depends_on[1]).

    ``depends_on`` must list exactly two names: [numerator, denominator].
    Emits ``dependency_not_ready`` when either dep is not ready.
    Emits ``dependency_not_ready`` (reason: denominator zero) when denom == 0.
    """

    def __init__(self, spec: FeatureSpec) -> None:
        super().__init__(spec)
        deps = list(spec.depends_on)
        if len(deps) != 2:
            raise ValueError(
                f"RatioDerivedFeature {spec.name!r}: depends_on must have exactly 2 entries "
                f"[numerator, denominator], got {deps!r}"
            )
        self._dep0 = deps[0]
        self._dep1 = deps[1]

    @property
    def is_ready(self) -> bool:
        return self._cached.is_ready

    def reset(self) -> None:
        self._reset_base()

    def update_from_dependencies(
        self, ctx: DependencyContext, source_event: Any
    ) -> FeatureUpdate:
        ts_ns = _ts_ns(source_event, self._spec.trigger.time_semantics)
        if not ctx.is_ready(self._dep0):
            return self._dep_not_ready(self._dep0, ts_ns)
        if not ctx.is_ready(self._dep1):
            return self._dep_not_ready(self._dep1, ts_ns)
        numer = ctx.value(self._dep0)
        denom = ctx.value(self._dep1)
        if denom == 0.0:
            fv = FeatureValue(
                name=self._spec.name,
                value=None,
                is_ready=False,
                source_event_time_ns=ts_ns,
                update_status="dependency_not_ready",
                reason="Denominator is zero",
            )
            return FeatureUpdate(value=fv, triggered=False)
        result = numer / denom
        return self._emit(result, True, True, source_event_time_ns=ts_ns, update_status="updated")

    def state_dict(self) -> dict:
        return self._base_state()

    def load_state_dict(self, state: dict) -> None:
        self._load_base(state)


class DifferenceDerivedFeature(_AbstractDerivedFeature):
    """difference: value(depends_on[0]) - value(depends_on[1]).

    ``depends_on`` must list exactly two names: [minuend, subtrahend].
    """

    def __init__(self, spec: FeatureSpec) -> None:
        super().__init__(spec)
        deps = list(spec.depends_on)
        if len(deps) != 2:
            raise ValueError(
                f"DifferenceDerivedFeature {spec.name!r}: depends_on must have exactly 2 "
                f"entries [minuend, subtrahend], got {deps!r}"
            )
        self._dep0 = deps[0]
        self._dep1 = deps[1]

    @property
    def is_ready(self) -> bool:
        return self._cached.is_ready

    def reset(self) -> None:
        self._reset_base()

    def update_from_dependencies(
        self, ctx: DependencyContext, source_event: Any
    ) -> FeatureUpdate:
        ts_ns = _ts_ns(source_event, self._spec.trigger.time_semantics)
        for dep in (self._dep0, self._dep1):
            if not ctx.is_ready(dep):
                return self._dep_not_ready(dep, ts_ns)
        result = ctx.value(self._dep0) - ctx.value(self._dep1)
        return self._emit(result, True, True, source_event_time_ns=ts_ns, update_status="updated")

    def state_dict(self) -> dict:
        return self._base_state()

    def load_state_dict(self, state: dict) -> None:
        self._load_base(state)


class SumDerivedFeature(_AbstractDerivedFeature):
    """sum: sum of all dependency values. All deps must be ready.

    ``depends_on`` must list at least one name.
    """

    def __init__(self, spec: FeatureSpec) -> None:
        super().__init__(spec)
        self._deps = list(spec.depends_on)
        if not self._deps:
            raise ValueError(
                f"SumDerivedFeature {spec.name!r}: depends_on must be non-empty"
            )

    @property
    def is_ready(self) -> bool:
        return self._cached.is_ready

    def reset(self) -> None:
        self._reset_base()

    def update_from_dependencies(
        self, ctx: DependencyContext, source_event: Any
    ) -> FeatureUpdate:
        ts_ns = _ts_ns(source_event, self._spec.trigger.time_semantics)
        for dep in self._deps:
            if not ctx.is_ready(dep):
                return self._dep_not_ready(dep, ts_ns)
        result = sum(ctx.value(d) for d in self._deps)
        return self._emit(result, True, True, source_event_time_ns=ts_ns, update_status="updated")

    def state_dict(self) -> dict:
        return self._base_state()

    def load_state_dict(self, state: dict) -> None:
        self._load_base(state)


class ProductDerivedFeature(_AbstractDerivedFeature):
    """product: product of all dependency values. All deps must be ready.

    ``depends_on`` must list at least one name.
    """

    def __init__(self, spec: FeatureSpec) -> None:
        super().__init__(spec)
        self._deps = list(spec.depends_on)
        if not self._deps:
            raise ValueError(
                f"ProductDerivedFeature {spec.name!r}: depends_on must be non-empty"
            )

    @property
    def is_ready(self) -> bool:
        return self._cached.is_ready

    def reset(self) -> None:
        self._reset_base()

    def update_from_dependencies(
        self, ctx: DependencyContext, source_event: Any
    ) -> FeatureUpdate:
        ts_ns = _ts_ns(source_event, self._spec.trigger.time_semantics)
        for dep in self._deps:
            if not ctx.is_ready(dep):
                return self._dep_not_ready(dep, ts_ns)
        result = 1.0
        for dep in self._deps:
            result *= ctx.value(dep)
        return self._emit(result, True, True, source_event_time_ns=ts_ns, update_status="updated")

    def state_dict(self) -> dict:
        return self._base_state()

    def load_state_dict(self, state: dict) -> None:
        self._load_base(state)


class RollingStdDerivedFeature(_AbstractDerivedFeature):
    """Rolling sample std of a single dependency's stream of values.

    Accumulates values from a raw feature (e.g. ``log_return``) into a
    fixed-size ring buffer and emits the rolling sample standard deviation
    once the window is full.  All arithmetic is O(1) per update via the
    running sum and sum-of-squares in ``RollingWindowState``.

    Typical use case — realized volatility from log-returns::

        log_ret_spec = FeatureSpec(
            "log_return_close", input_type="bar",
            input_field="close", params={"type": "log_return"},
        )
        rvol_spec = FeatureSpec(
            "realized_vol_60", input_type="derived",
            depends_on=("log_return_close",),
            window=60,
            params={"type": "rolling_std_derived"},
        )

    Parameters (from ``FeatureSpec``)
    -----------------------------------
    depends_on : tuple[str]   — exactly one name; the raw return / signal feature.
    window     : int          — number of dep values to accumulate (default 20).
    """

    def __init__(self, spec: FeatureSpec) -> None:
        super().__init__(spec)
        deps = list(spec.depends_on)
        if len(deps) != 1:
            raise ValueError(
                f"RollingStdDerivedFeature {spec.name!r}: depends_on must have "
                f"exactly 1 entry (the source feature), got {deps!r}"
            )
        self._dep = deps[0]
        self._state = RollingWindowState(maxlen=spec.window or 20, track_squares=True)

    def warmup_required(self) -> WarmupRequirement:
        n = self._spec.window or 20
        return WarmupRequirement(n_events=n, unit="events", mandatory=True)

    @property
    def is_ready(self) -> bool:
        return self._state.is_full

    def reset(self) -> None:
        self._state.reset()
        self._reset_base()

    def update_from_dependencies(
        self, ctx: DependencyContext, source_event: Any
    ) -> FeatureUpdate:
        ts_ns = _ts_ns(source_event, self._spec.trigger.time_semantics)
        if not ctx.is_ready(self._dep):
            return self._dep_not_ready(self._dep, ts_ns)
        v = ctx.value(self._dep)
        if v is None:
            return self._dep_not_ready(self._dep, ts_ns)
        self._state.push(v)
        ready = self._state.is_full
        return self._emit(
            self._state.std if ready else None,
            ready,
            ready,
            source_event_time_ns=ts_ns,
            update_status="updated" if ready else "not_ready",
        )

    def state_dict(self) -> dict:
        return {**self._base_state(), "rolling": self._state.state_dict()}

    def load_state_dict(self, state: dict) -> None:
        self._load_base(state)
        self._state.load_state_dict(state["rolling"])
        if self._state.is_full:
            self._cached = FeatureValue(
                name=self._spec.name, value=self._state.std, is_ready=True,
            )


# ===========================================================================
# OHLCV feature library (pure Python; no nautilus_trader)
# ===========================================================================
#
# A set of common technical features computed incrementally from bar events.
# Each is a self-contained _AbstractFeature subclass: it reads bar fields
# directly (open/high/low/close/volume), keeps its own RollingWindowState /
# VWAPState, guards division by zero with _EPS, and reports not_ready until it
# has enough history.  Formulas reference standard indicator definitions
# (TA-Lib / common technical analysis) but the maths is implemented here in
# plain Python — no Nautilus indicators are used.
#
# Categories:
#   A. price / bar structure   — rolling_range, true_range, candle_body_ratio,
#                                 upper_shadow_ratio, lower_shadow_ratio
#   B. trend / momentum        — return_n, momentum_n, price_position,
#                                 drawdown_from_rolling_high, breakout_up,
#                                 breakout_down
#   C. volatility              — atr, volatility_ratio, bollinger_width,
#                                 bollinger_percent_b
#   D. normalization / volume  — zscore, volume_zscore, volume_ratio,
#                                 quote_volume, vwap_distance
# ---------------------------------------------------------------------------

# Small constant used to guard divisions (matches the spec's ``max(denom, eps)``).
_EPS = 1e-12


def _bar_field(event: Any, name: str) -> float | None:
    """Alias of _field for readability in OHLCV features."""
    return _field(event, name)


# ---------------------------------------------------------------------------
# A. Price / bar-structure features (single bar, ready after 1 event)
# ---------------------------------------------------------------------------

class RollingRangeFeature(_AbstractFeature):
    """Intrabar range: ``high - low`` for the current bar."""

    def warmup_required(self) -> WarmupRequirement:
        return WarmupRequirement(n_events=1, unit="bars")

    @property
    def is_ready(self) -> bool:
        return self._cached.is_ready

    def reset(self) -> None:
        self._reset_base()

    def update(self, event: Any) -> FeatureUpdate:
        self._event_count += 1
        ts_ns = _ts_ns(event, self._spec.trigger.time_semantics)
        high = _bar_field(event, "high")
        low = _bar_field(event, "low")
        if high is None or low is None:
            return self._no_change()
        triggered = self._should_trigger(ts_ns)
        if triggered:
            self._last_trigger_ts = ts_ns
        return self._emit(
            high - low, True, triggered,
            source_event_time_ns=ts_ns, update_status="updated",
        )

    def state_dict(self) -> dict:
        return self._base_state()

    def load_state_dict(self, state: dict) -> None:
        self._load_base(state)


class TrueRangeFeature(_AbstractFeature):
    """True range: ``max(high-low, |high-prev_close|, |low-prev_close|)``.

    On the first bar (no previous close) the true range is ``high - low``.
    """

    def __init__(self, spec: FeatureSpec) -> None:
        super().__init__(spec)
        self._prev_close: float | None = None

    def warmup_required(self) -> WarmupRequirement:
        return WarmupRequirement(n_events=1, unit="bars")

    @property
    def is_ready(self) -> bool:
        return self._cached.is_ready

    def reset(self) -> None:
        self._prev_close = None
        self._reset_base()

    def _true_range(self, high: float, low: float) -> float:
        if self._prev_close is None:
            return high - low
        return max(
            high - low,
            abs(high - self._prev_close),
            abs(low - self._prev_close),
        )

    def update(self, event: Any) -> FeatureUpdate:
        self._event_count += 1
        ts_ns = _ts_ns(event, self._spec.trigger.time_semantics)
        high = _bar_field(event, "high")
        low = _bar_field(event, "low")
        close = _bar_field(event, "close")
        if high is None or low is None or close is None:
            return self._no_change()
        tr = self._true_range(high, low)
        self._prev_close = close
        triggered = self._should_trigger(ts_ns)
        if triggered:
            self._last_trigger_ts = ts_ns
        return self._emit(
            tr, True, triggered,
            source_event_time_ns=ts_ns, update_status="updated",
        )

    def state_dict(self) -> dict:
        return {**self._base_state(), "prev_close": self._prev_close}

    def load_state_dict(self, state: dict) -> None:
        self._load_base(state)
        self._prev_close = state.get("prev_close")


class CandleBodyRatioFeature(_AbstractFeature):
    """Body / range: ``|close - open| / max(high - low, eps)``."""

    def warmup_required(self) -> WarmupRequirement:
        return WarmupRequirement(n_events=1, unit="bars")

    @property
    def is_ready(self) -> bool:
        return self._cached.is_ready

    def reset(self) -> None:
        self._reset_base()

    def update(self, event: Any) -> FeatureUpdate:
        self._event_count += 1
        ts_ns = _ts_ns(event, self._spec.trigger.time_semantics)
        o = _bar_field(event, "open")
        h = _bar_field(event, "high")
        low = _bar_field(event, "low")
        c = _bar_field(event, "close")
        if None in (o, h, low, c):
            return self._no_change()
        ratio = abs(c - o) / max(h - low, _EPS)
        triggered = self._should_trigger(ts_ns)
        if triggered:
            self._last_trigger_ts = ts_ns
        return self._emit(
            ratio, True, triggered,
            source_event_time_ns=ts_ns, update_status="updated",
        )

    def state_dict(self) -> dict:
        return self._base_state()

    def load_state_dict(self, state: dict) -> None:
        self._load_base(state)


class UpperShadowRatioFeature(_AbstractFeature):
    """Upper shadow / range: ``(high - max(open, close)) / max(high - low, eps)``."""

    def warmup_required(self) -> WarmupRequirement:
        return WarmupRequirement(n_events=1, unit="bars")

    @property
    def is_ready(self) -> bool:
        return self._cached.is_ready

    def reset(self) -> None:
        self._reset_base()

    def update(self, event: Any) -> FeatureUpdate:
        self._event_count += 1
        ts_ns = _ts_ns(event, self._spec.trigger.time_semantics)
        o = _bar_field(event, "open")
        h = _bar_field(event, "high")
        low = _bar_field(event, "low")
        c = _bar_field(event, "close")
        if None in (o, h, low, c):
            return self._no_change()
        ratio = (h - max(o, c)) / max(h - low, _EPS)
        triggered = self._should_trigger(ts_ns)
        if triggered:
            self._last_trigger_ts = ts_ns
        return self._emit(
            ratio, True, triggered,
            source_event_time_ns=ts_ns, update_status="updated",
        )

    def state_dict(self) -> dict:
        return self._base_state()

    def load_state_dict(self, state: dict) -> None:
        self._load_base(state)


class LowerShadowRatioFeature(_AbstractFeature):
    """Lower shadow / range: ``(min(open, close) - low) / max(high - low, eps)``."""

    def warmup_required(self) -> WarmupRequirement:
        return WarmupRequirement(n_events=1, unit="bars")

    @property
    def is_ready(self) -> bool:
        return self._cached.is_ready

    def reset(self) -> None:
        self._reset_base()

    def update(self, event: Any) -> FeatureUpdate:
        self._event_count += 1
        ts_ns = _ts_ns(event, self._spec.trigger.time_semantics)
        o = _bar_field(event, "open")
        h = _bar_field(event, "high")
        low = _bar_field(event, "low")
        c = _bar_field(event, "close")
        if None in (o, h, low, c):
            return self._no_change()
        ratio = (min(o, c) - low) / max(h - low, _EPS)
        triggered = self._should_trigger(ts_ns)
        if triggered:
            self._last_trigger_ts = ts_ns
        return self._emit(
            ratio, True, triggered,
            source_event_time_ns=ts_ns, update_status="updated",
        )

    def state_dict(self) -> dict:
        return self._base_state()

    def load_state_dict(self, state: dict) -> None:
        self._load_base(state)


# ---------------------------------------------------------------------------
# B. Trend / momentum features
# ---------------------------------------------------------------------------

class ReturnNFeature(_AbstractFeature):
    """N-bar simple return: ``close / close[-n] - 1`` (``window`` == n)."""

    def __init__(self, spec: FeatureSpec) -> None:
        super().__init__(spec)
        n = spec.window or 1
        self._n = n
        self._state = RollingWindowState(maxlen=n + 1)
        self._field_name = spec.input_field or "close"

    def warmup_required(self) -> WarmupRequirement:
        return WarmupRequirement(n_events=self._n + 1, unit="bars")

    @property
    def is_ready(self) -> bool:
        return self._state.is_full

    def reset(self) -> None:
        self._state.reset()
        self._reset_base()

    def update(self, event: Any) -> FeatureUpdate:
        self._event_count += 1
        ts_ns = _ts_ns(event, self._spec.trigger.time_semantics)
        cur = _bar_field(event, self._field_name)
        if cur is None:
            return self._missing_field(self._field_name)
        self._state.push(cur)
        triggered = self._should_trigger(ts_ns)
        if triggered:
            self._last_trigger_ts = ts_ns
        if not self._state.is_full:
            return self._emit(None, False, triggered,
                              source_event_time_ns=ts_ns, update_status="not_ready")
        past = self._state.values[0]
        if abs(past) < _EPS:
            return self._emit(None, False, triggered,
                              source_event_time_ns=ts_ns, update_status="not_ready")
        return self._emit(cur / past - 1.0, True, triggered,
                          source_event_time_ns=ts_ns, update_status="updated")

    def state_dict(self) -> dict:
        return {**self._base_state(), "rolling": self._state.state_dict()}

    def load_state_dict(self, state: dict) -> None:
        self._load_base(state)
        self._state.load_state_dict(state["rolling"])


class MomentumNFeature(_AbstractFeature):
    """N-bar momentum: ``close - close[-n]`` (``window`` == n)."""

    def __init__(self, spec: FeatureSpec) -> None:
        super().__init__(spec)
        n = spec.window or 1
        self._n = n
        self._state = RollingWindowState(maxlen=n + 1)
        self._field_name = spec.input_field or "close"

    def warmup_required(self) -> WarmupRequirement:
        return WarmupRequirement(n_events=self._n + 1, unit="bars")

    @property
    def is_ready(self) -> bool:
        return self._state.is_full

    def reset(self) -> None:
        self._state.reset()
        self._reset_base()

    def update(self, event: Any) -> FeatureUpdate:
        self._event_count += 1
        ts_ns = _ts_ns(event, self._spec.trigger.time_semantics)
        cur = _bar_field(event, self._field_name)
        if cur is None:
            return self._missing_field(self._field_name)
        self._state.push(cur)
        triggered = self._should_trigger(ts_ns)
        if triggered:
            self._last_trigger_ts = ts_ns
        ready = self._state.is_full
        value = (cur - self._state.values[0]) if ready else None
        return self._emit(value, ready, triggered,
                          source_event_time_ns=ts_ns,
                          update_status="updated" if ready else "not_ready")

    def state_dict(self) -> dict:
        return {**self._base_state(), "rolling": self._state.state_dict()}

    def load_state_dict(self, state: dict) -> None:
        self._load_base(state)
        self._state.load_state_dict(state["rolling"])


class PricePositionFeature(_AbstractFeature):
    """Stochastic-style position of close within the n-bar high/low range::

        (close - rolling_min(low, n)) / max(rolling_max(high, n) - rolling_min(low, n), eps)
    """

    def __init__(self, spec: FeatureSpec) -> None:
        super().__init__(spec)
        n = spec.window or 1
        self._low_state = RollingWindowState(maxlen=n)
        self._high_state = RollingWindowState(maxlen=n)

    def warmup_required(self) -> WarmupRequirement:
        return WarmupRequirement(n_events=self._spec.window or 1, unit="bars")

    @property
    def is_ready(self) -> bool:
        return self._low_state.is_full and self._high_state.is_full

    def reset(self) -> None:
        self._low_state.reset()
        self._high_state.reset()
        self._reset_base()

    def update(self, event: Any) -> FeatureUpdate:
        self._event_count += 1
        ts_ns = _ts_ns(event, self._spec.trigger.time_semantics)
        h = _bar_field(event, "high")
        low = _bar_field(event, "low")
        c = _bar_field(event, "close")
        if None in (h, low, c):
            return self._no_change()
        self._low_state.push(low)
        self._high_state.push(h)
        triggered = self._should_trigger(ts_ns)
        if triggered:
            self._last_trigger_ts = ts_ns
        ready = self.is_ready
        if not ready:
            return self._emit(None, False, triggered,
                              source_event_time_ns=ts_ns, update_status="not_ready")
        lo = self._low_state.min
        hi = self._high_state.max
        pos = (c - lo) / max(hi - lo, _EPS)
        return self._emit(pos, True, triggered,
                          source_event_time_ns=ts_ns, update_status="updated")

    def state_dict(self) -> dict:
        return {
            **self._base_state(),
            "low": self._low_state.state_dict(),
            "high": self._high_state.state_dict(),
        }

    def load_state_dict(self, state: dict) -> None:
        self._load_base(state)
        self._low_state.load_state_dict(state["low"])
        self._high_state.load_state_dict(state["high"])


class DrawdownFromRollingHighFeature(_AbstractFeature):
    """Drawdown from the rolling high: ``close / rolling_max(close, n) - 1``."""

    def __init__(self, spec: FeatureSpec) -> None:
        super().__init__(spec)
        self._state = RollingWindowState(maxlen=spec.window or 1)
        self._field_name = spec.input_field or "close"

    def warmup_required(self) -> WarmupRequirement:
        return WarmupRequirement(n_events=self._spec.window or 1, unit="bars")

    @property
    def is_ready(self) -> bool:
        return self._state.is_full

    def reset(self) -> None:
        self._state.reset()
        self._reset_base()

    def update(self, event: Any) -> FeatureUpdate:
        self._event_count += 1
        ts_ns = _ts_ns(event, self._spec.trigger.time_semantics)
        c = _bar_field(event, self._field_name)
        if c is None:
            return self._missing_field(self._field_name)
        self._state.push(c)
        triggered = self._should_trigger(ts_ns)
        if triggered:
            self._last_trigger_ts = ts_ns
        ready = self._state.is_full
        if not ready:
            return self._emit(None, False, triggered,
                              source_event_time_ns=ts_ns, update_status="not_ready")
        roll_max = self._state.max
        value = c / max(roll_max, _EPS) - 1.0
        return self._emit(value, True, triggered,
                          source_event_time_ns=ts_ns, update_status="updated")

    def state_dict(self) -> dict:
        return {**self._base_state(), "rolling": self._state.state_dict()}

    def load_state_dict(self, state: dict) -> None:
        self._load_base(state)
        self._state.load_state_dict(state["rolling"])


class BreakoutUpFeature(_AbstractFeature):
    """Breakout up: ``close > previous rolling_max(high, n)`` (bool, 1.0/0.0-style).

    "Previous" means the rolling max of the prior n highs, evaluated **before**
    the current bar's high is included, so the current bar cannot break out
    against itself.
    """

    def __init__(self, spec: FeatureSpec) -> None:
        super().__init__(spec)
        self._state = RollingWindowState(maxlen=spec.window or 1)

    def warmup_required(self) -> WarmupRequirement:
        return WarmupRequirement(n_events=(self._spec.window or 1) + 1, unit="bars")

    @property
    def is_ready(self) -> bool:
        return self._cached.is_ready

    def reset(self) -> None:
        self._state.reset()
        self._reset_base()

    def update(self, event: Any) -> FeatureUpdate:
        self._event_count += 1
        ts_ns = _ts_ns(event, self._spec.trigger.time_semantics)
        h = _bar_field(event, "high")
        c = _bar_field(event, "close")
        if h is None or c is None:
            return self._no_change()
        triggered = self._should_trigger(ts_ns)
        if triggered:
            self._last_trigger_ts = ts_ns
        ready = self._state.is_full  # n prior highs available
        if ready:
            prev_max = self._state.max
            result: bool | None = c > prev_max
        else:
            result = None
        self._state.push(h)  # current high becomes "previous" for the next bar
        return self._emit(result, ready, triggered,
                          source_event_time_ns=ts_ns,
                          update_status="updated" if ready else "not_ready")

    def state_dict(self) -> dict:
        return {**self._base_state(), "rolling": self._state.state_dict()}

    def load_state_dict(self, state: dict) -> None:
        self._load_base(state)
        self._state.load_state_dict(state["rolling"])


class BreakoutDownFeature(_AbstractFeature):
    """Breakout down: ``close < previous rolling_min(low, n)`` (bool)."""

    def __init__(self, spec: FeatureSpec) -> None:
        super().__init__(spec)
        self._state = RollingWindowState(maxlen=spec.window or 1)

    def warmup_required(self) -> WarmupRequirement:
        return WarmupRequirement(n_events=(self._spec.window or 1) + 1, unit="bars")

    @property
    def is_ready(self) -> bool:
        return self._cached.is_ready

    def reset(self) -> None:
        self._state.reset()
        self._reset_base()

    def update(self, event: Any) -> FeatureUpdate:
        self._event_count += 1
        ts_ns = _ts_ns(event, self._spec.trigger.time_semantics)
        low = _bar_field(event, "low")
        c = _bar_field(event, "close")
        if low is None or c is None:
            return self._no_change()
        triggered = self._should_trigger(ts_ns)
        if triggered:
            self._last_trigger_ts = ts_ns
        ready = self._state.is_full
        if ready:
            prev_min = self._state.min
            result: bool | None = c < prev_min
        else:
            result = None
        self._state.push(low)
        return self._emit(result, ready, triggered,
                          source_event_time_ns=ts_ns,
                          update_status="updated" if ready else "not_ready")

    def state_dict(self) -> dict:
        return {**self._base_state(), "rolling": self._state.state_dict()}

    def load_state_dict(self, state: dict) -> None:
        self._load_base(state)
        self._state.load_state_dict(state["rolling"])


# ---------------------------------------------------------------------------
# C. Volatility features
# ---------------------------------------------------------------------------

class AtrFeature(_AbstractFeature):
    """Average True Range: rolling mean of true range over ``window`` bars.

    Uses a simple moving average of the true range (the spec's definition),
    not Wilder's smoothing.  The first bar's true range is ``high - low``.
    """

    def __init__(self, spec: FeatureSpec) -> None:
        super().__init__(spec)
        self._state = RollingWindowState(maxlen=spec.window or 1)
        self._prev_close: float | None = None

    def warmup_required(self) -> WarmupRequirement:
        return WarmupRequirement(n_events=self._spec.window or 1, unit="bars")

    @property
    def is_ready(self) -> bool:
        return self._state.is_full

    def reset(self) -> None:
        self._state.reset()
        self._prev_close = None
        self._reset_base()

    def _true_range(self, high: float, low: float) -> float:
        if self._prev_close is None:
            return high - low
        return max(
            high - low,
            abs(high - self._prev_close),
            abs(low - self._prev_close),
        )

    def update(self, event: Any) -> FeatureUpdate:
        self._event_count += 1
        ts_ns = _ts_ns(event, self._spec.trigger.time_semantics)
        h = _bar_field(event, "high")
        low = _bar_field(event, "low")
        c = _bar_field(event, "close")
        if None in (h, low, c):
            return self._no_change()
        tr = self._true_range(h, low)
        self._prev_close = c
        self._state.push(tr)
        triggered = self._should_trigger(ts_ns)
        if triggered:
            self._last_trigger_ts = ts_ns
        ready = self._state.is_full
        return self._emit(self._state.mean if ready else None, ready, triggered,
                          source_event_time_ns=ts_ns,
                          update_status="updated" if ready else "not_ready")

    def state_dict(self) -> dict:
        return {
            **self._base_state(),
            "rolling": self._state.state_dict(),
            "prev_close": self._prev_close,
        }

    def load_state_dict(self, state: dict) -> None:
        self._load_base(state)
        self._state.load_state_dict(state["rolling"])
        self._prev_close = state.get("prev_close")
        if self._state.is_full:
            self._cached = FeatureValue(
                name=self._spec.name, value=self._state.mean, is_ready=True,
            )


class VolatilityRatioFeature(_AbstractFeature):
    """Short/long realized-volatility ratio::

        std(logret, short) / max(std(logret, long), eps)

    Realized volatility is the sample std of log close-to-close returns.

    Parameters (from ``params``)
    -----------------------------
    short_window : int   — short volatility window (default 5).
    long_window  : int   — long volatility window (default 20).
    """

    def __init__(self, spec: FeatureSpec) -> None:
        super().__init__(spec)
        self._short = int(spec.params.get("short_window", 5))
        self._long = int(spec.params.get("long_window", 20))
        self._short_state = RollingWindowState(maxlen=self._short, track_squares=True)
        self._long_state = RollingWindowState(maxlen=self._long, track_squares=True)
        self._prev: float | None = None
        self._field_name = spec.input_field or "close"

    def warmup_required(self) -> WarmupRequirement:
        return WarmupRequirement(n_events=self._long + 1, unit="bars")

    @property
    def is_ready(self) -> bool:
        return self._short_state.is_full and self._long_state.is_full

    def reset(self) -> None:
        self._short_state.reset()
        self._long_state.reset()
        self._prev = None
        self._reset_base()

    def update(self, event: Any) -> FeatureUpdate:
        self._event_count += 1
        ts_ns = _ts_ns(event, self._spec.trigger.time_semantics)
        cur = _bar_field(event, self._field_name)
        if cur is None:
            return self._missing_field(self._field_name)
        triggered = self._should_trigger(ts_ns)
        if triggered:
            self._last_trigger_ts = ts_ns
        if self._prev is None or self._prev <= 0.0 or cur <= 0.0:
            self._prev = cur
            return self._emit(None, False, triggered,
                              source_event_time_ns=ts_ns, update_status="not_ready")
        log_ret = math.log(cur / self._prev)
        self._prev = cur
        self._short_state.push(log_ret)
        self._long_state.push(log_ret)
        ready = self.is_ready
        if not ready:
            return self._emit(None, False, triggered,
                              source_event_time_ns=ts_ns, update_status="not_ready")
        short_vol = self._short_state.std or 0.0
        long_vol = self._long_state.std or 0.0
        ratio = short_vol / max(long_vol, _EPS)
        return self._emit(ratio, True, triggered,
                          source_event_time_ns=ts_ns, update_status="updated")

    def state_dict(self) -> dict:
        return {
            **self._base_state(),
            "short": self._short_state.state_dict(),
            "long": self._long_state.state_dict(),
            "prev": self._prev,
        }

    def load_state_dict(self, state: dict) -> None:
        self._load_base(state)
        self._short_state.load_state_dict(state["short"])
        self._long_state.load_state_dict(state["long"])
        self._prev = state.get("prev")


class _BollingerBase(_AbstractFeature):
    """Shared state for Bollinger-band features: rolling mean/std of close.

    Parameters (from ``params``)
    -----------------------------
    k : float   — number of standard deviations for the bands (default 2.0).
    """

    def __init__(self, spec: FeatureSpec) -> None:
        super().__init__(spec)
        self._state = RollingWindowState(maxlen=spec.window or 2, track_squares=True)
        self._k = float(spec.params.get("k", 2.0))
        self._field_name = spec.input_field or "close"

    def warmup_required(self) -> WarmupRequirement:
        return WarmupRequirement(n_events=self._spec.window or 2, unit="bars")

    @property
    def is_ready(self) -> bool:
        return self._state.is_full

    def reset(self) -> None:
        self._state.reset()
        self._reset_base()

    def _compute(self, close: float) -> float:  # pragma: no cover - overridden
        raise NotImplementedError

    def update(self, event: Any) -> FeatureUpdate:
        self._event_count += 1
        ts_ns = _ts_ns(event, self._spec.trigger.time_semantics)
        c = _bar_field(event, self._field_name)
        if c is None:
            return self._missing_field(self._field_name)
        self._state.push(c)
        triggered = self._should_trigger(ts_ns)
        if triggered:
            self._last_trigger_ts = ts_ns
        ready = self._state.is_full
        if not ready:
            return self._emit(None, False, triggered,
                              source_event_time_ns=ts_ns, update_status="not_ready")
        return self._emit(self._compute(c), True, triggered,
                          source_event_time_ns=ts_ns, update_status="updated")

    def state_dict(self) -> dict:
        return {**self._base_state(), "rolling": self._state.state_dict()}

    def load_state_dict(self, state: dict) -> None:
        self._load_base(state)
        self._state.load_state_dict(state["rolling"])


class BollingerWidthFeature(_BollingerBase):
    """Bollinger band width: ``(upper - lower) / max(middle, eps)`` = ``2k*std / max(mean, eps)``."""

    def _compute(self, close: float) -> float:
        middle = self._state.mean or 0.0
        std = self._state.std or 0.0
        return (2.0 * self._k * std) / max(middle, _EPS)


class BollingerPercentBFeature(_BollingerBase):
    """Bollinger %B: ``(close - lower) / max(upper - lower, eps)``."""

    def _compute(self, close: float) -> float:
        middle = self._state.mean or 0.0
        std = self._state.std or 0.0
        lower = middle - self._k * std
        band = 2.0 * self._k * std
        return (close - lower) / max(band, _EPS)


# ---------------------------------------------------------------------------
# D. Normalization / volume features
# ---------------------------------------------------------------------------

class ZScoreFeature(_AbstractFeature):
    """Rolling z-score: ``(x - rolling_mean(x, n)) / max(rolling_std(x, n), eps)``.

    ``x`` is ``spec.input_field`` (default ``"close"``).
    """

    _DEFAULT_FIELD = "close"

    def __init__(self, spec: FeatureSpec) -> None:
        super().__init__(spec)
        self._state = RollingWindowState(maxlen=spec.window or 2, track_squares=True)
        self._field_name = spec.input_field or self._DEFAULT_FIELD

    def warmup_required(self) -> WarmupRequirement:
        return WarmupRequirement(n_events=self._spec.window or 2, unit="bars")

    @property
    def is_ready(self) -> bool:
        return self._state.is_full

    def reset(self) -> None:
        self._state.reset()
        self._reset_base()

    def update(self, event: Any) -> FeatureUpdate:
        self._event_count += 1
        ts_ns = _ts_ns(event, self._spec.trigger.time_semantics)
        v = _bar_field(event, self._field_name)
        if v is None:
            return self._missing_field(self._field_name)
        self._state.push(v)
        triggered = self._should_trigger(ts_ns)
        if triggered:
            self._last_trigger_ts = ts_ns
        ready = self._state.is_full
        if not ready:
            return self._emit(None, False, triggered,
                              source_event_time_ns=ts_ns, update_status="not_ready")
        mean = self._state.mean or 0.0
        std = self._state.std or 0.0
        z = (v - mean) / max(std, _EPS)
        return self._emit(z, True, triggered,
                          source_event_time_ns=ts_ns, update_status="updated")

    def state_dict(self) -> dict:
        return {**self._base_state(), "rolling": self._state.state_dict()}

    def load_state_dict(self, state: dict) -> None:
        self._load_base(state)
        self._state.load_state_dict(state["rolling"])


class VolumeZScoreFeature(ZScoreFeature):
    """Z-score of volume: ``ZScoreFeature`` with default ``input_field="volume"``."""

    _DEFAULT_FIELD = "volume"


class VolumeRatioFeature(_AbstractFeature):
    """Volume ratio: ``volume / max(rolling_mean(volume, n), eps)``."""

    def __init__(self, spec: FeatureSpec) -> None:
        super().__init__(spec)
        self._state = RollingWindowState(maxlen=spec.window or 1)
        self._field_name = spec.input_field or "volume"

    def warmup_required(self) -> WarmupRequirement:
        return WarmupRequirement(n_events=self._spec.window or 1, unit="bars")

    @property
    def is_ready(self) -> bool:
        return self._state.is_full

    def reset(self) -> None:
        self._state.reset()
        self._reset_base()

    def update(self, event: Any) -> FeatureUpdate:
        self._event_count += 1
        ts_ns = _ts_ns(event, self._spec.trigger.time_semantics)
        v = _bar_field(event, self._field_name)
        if v is None:
            return self._missing_field(self._field_name)
        self._state.push(v)
        triggered = self._should_trigger(ts_ns)
        if triggered:
            self._last_trigger_ts = ts_ns
        ready = self._state.is_full
        if not ready:
            return self._emit(None, False, triggered,
                              source_event_time_ns=ts_ns, update_status="not_ready")
        mean = self._state.mean or 0.0
        ratio = v / max(mean, _EPS)
        return self._emit(ratio, True, triggered,
                          source_event_time_ns=ts_ns, update_status="updated")

    def state_dict(self) -> dict:
        return {**self._base_state(), "rolling": self._state.state_dict()}

    def load_state_dict(self, state: dict) -> None:
        self._load_base(state)
        self._state.load_state_dict(state["rolling"])


class QuoteVolumeFeature(_AbstractFeature):
    """Quote (notional) volume.

    Reads ``quote_volume`` from the event when present; otherwise falls back to
    ``close * volume``.
    """

    def warmup_required(self) -> WarmupRequirement:
        return WarmupRequirement(n_events=1, unit="bars")

    @property
    def is_ready(self) -> bool:
        return self._cached.is_ready

    def reset(self) -> None:
        self._reset_base()

    def update(self, event: Any) -> FeatureUpdate:
        self._event_count += 1
        ts_ns = _ts_ns(event, self._spec.trigger.time_semantics)
        qv = _bar_field(event, "quote_volume")
        if qv is None:
            close = _bar_field(event, "close")
            volume = _bar_field(event, "volume")
            if close is None or volume is None:
                return self._no_change()
            qv = close * volume
        triggered = self._should_trigger(ts_ns)
        if triggered:
            self._last_trigger_ts = ts_ns
        return self._emit(qv, True, triggered,
                          source_event_time_ns=ts_ns, update_status="updated")

    def state_dict(self) -> dict:
        return self._base_state()

    def load_state_dict(self, state: dict) -> None:
        self._load_base(state)


class VwapDistanceFeature(_AbstractFeature):
    """Distance of close from VWAP: ``close / max(vwap, eps) - 1``.

    VWAP is computed internally (session by default, or a rolling count/time
    window via ``window`` / ``window_unit``).  Reuses VWAPState.

    Parameters (from ``params``)
    -----------------------------
    price_field  : str   — VWAP price field (default "close").
    volume_field : str   — VWAP volume field (default "volume").
    """

    _NS_PER_UNIT = VWAPFeature._NS_PER_UNIT

    def __init__(self, spec: FeatureSpec) -> None:
        super().__init__(spec)
        window = spec.window
        unit = spec.window_unit or "bars"
        window_ns: int | None = None
        count_window: int | None = None
        if window is not None:
            if unit in self._NS_PER_UNIT:
                window_ns = window * self._NS_PER_UNIT[unit]
            else:
                count_window = window
        self._state = VWAPState(window=count_window, window_ns=window_ns)
        self._price_field = spec.params.get("price_field", "close")
        self._volume_field = spec.params.get("volume_field", "volume")

    def warmup_required(self) -> WarmupRequirement:
        return WarmupRequirement(n_events=self._spec.window or 1,
                                 unit=self._spec.window_unit or "bars", mandatory=False)

    @property
    def is_ready(self) -> bool:
        return self._cached.is_ready

    def reset(self) -> None:
        self._state.reset()
        self._reset_base()

    def update(self, event: Any) -> FeatureUpdate:
        self._event_count += 1
        ts_ns = _ts_ns(event, self._spec.trigger.time_semantics)
        price = _bar_field(event, self._price_field)
        close = _bar_field(event, "close")
        volume = _bar_field(event, self._volume_field)
        if price is None or close is None or volume is None:
            return self._no_change()
        self._state.push(price, volume, ts_ns=ts_ns)
        triggered = self._should_trigger(ts_ns)
        if triggered:
            self._last_trigger_ts = ts_ns
        vwap = self._state.vwap
        if vwap is None:
            return self._emit(None, False, triggered,
                              source_event_time_ns=ts_ns, update_status="not_ready")
        value = close / max(vwap, _EPS) - 1.0
        return self._emit(value, True, triggered,
                          source_event_time_ns=ts_ns, update_status="updated")

    def state_dict(self) -> dict:
        return {**self._base_state(), "vwap": self._state.state_dict()}

    def load_state_dict(self, state: dict) -> None:
        self._load_base(state)
        self._state.load_state_dict(state["vwap"])
