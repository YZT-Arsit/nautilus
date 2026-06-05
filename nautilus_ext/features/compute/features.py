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
    RollingMeanFeature    — rolling mean of one bar field
    RollingStdFeature     — rolling sample std of one bar field
    RollingMinFeature     — rolling minimum of one bar field
    RollingMaxFeature     — rolling maximum of one bar field
    VWAPFeature           — VWAP; rolling count, rolling time, or session
    SimpleReturnFeature   — (close_t - close_{t-1}) / close_{t-1}
    LogReturnFeature      — log(close_t / close_{t-1})
    EWMAFeature           — exponentially weighted moving average

Quote-input (input_type="quote"):
    SpreadFeature         — ask_price - bid_price
    MidPriceFeature       — (ask_price + bid_price) / 2

Orderbook-input (input_type="book_delta"):
    BookImbalanceFeature  — (bid_vol - ask_vol) / (bid_vol + ask_vol)
"""
from __future__ import annotations

import math
from typing import Any

from nautilus_ext.features.compute.spec import (
    FeatureSpec,
    FeatureUpdate,
    FeatureValue,
    WarmupRequirement,
)
from nautilus_ext.features.compute.state import (
    EWMAState,
    RollingWindowState,
    TimeWindowState,
    VWAPState,
)
from nautilus_ext.features.compute.timestamps import extract_timestamps, select_timestamp


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
    ) -> FeatureUpdate:
        fv = FeatureValue(
            name=self._spec.name,
            value=raw if ready else None,
            is_ready=ready,
            window_start_ns=window_start_ns,
            window_end_ns=window_end_ns,
            source_event_time_ns=source_event_time_ns,
        )
        self._cached = fv
        return FeatureUpdate(value=fv, triggered=triggered)

    def _no_change(self) -> FeatureUpdate:
        return FeatureUpdate(value=self._cached, triggered=False)

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
