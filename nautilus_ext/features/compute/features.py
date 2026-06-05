"""
Concrete incremental feature implementations (pure Python backend).

All classes satisfy FeatureBase via structural protocol. Each update() is
O(1) or amortized O(1) — no full-window iteration beyond min/max which are
O(window) but acceptable for windows < ~10 000 bars.

Event field extraction uses duck typing (getattr) so these classes work with
any object that has the expected attributes, not just the specific MarketEvent
dataclasses.

Features implemented
--------------------
Bar-input (input_type="bar"):
    RollingMeanFeature    — rolling mean of one bar field
    RollingStdFeature     — rolling sample std of one bar field
    RollingMinFeature     — rolling minimum of one bar field
    RollingMaxFeature     — rolling maximum of one bar field
    VWAPFeature           — volume-weighted average price (rolling or session)
    SimpleReturnFeature   — (close_t - close_{t-1}) / close_{t-1}
    LogReturnFeature      — log(close_t / close_{t-1})
    EWMAFeature           — exponentially weighted moving average of one bar field

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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ts(event: Any) -> int:
    """Extract ts_event from an event, defaulting to 0."""
    ts = getattr(event, "ts_event", None)
    return int(ts) if ts is not None else 0


def _field(event: Any, name: str | None) -> float | None:
    """Extract a named float field from an event."""
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
    """Internal mixin: spec storage, event counting, trigger checking, cache."""

    def __init__(self, spec: FeatureSpec) -> None:
        self._spec = spec
        self._event_count: int = 0
        self._last_trigger_ts: int = 0
        self._cached = FeatureValue(name=spec.name, value=None, is_ready=False)

    @property
    def spec(self) -> FeatureSpec:
        return self._spec

    @property
    def value(self) -> FeatureValue:
        return self._cached

    # ------------------------------------------------------------------
    # Trigger policy
    # ------------------------------------------------------------------

    def _should_trigger(self, ts_ms: int) -> bool:
        kind = self._spec.trigger.kind
        if kind in ("on_event", "on_bar_close"):
            return True
        n = self._spec.trigger.n
        if kind in ("on_n_events", "on_n_bars"):
            return n is not None and (self._event_count % n == 0)
        interval = self._spec.trigger.interval_ms or 0
        if kind in ("on_timer", "on_window_close"):
            return (ts_ms - self._last_trigger_ts) >= interval
        return True

    # ------------------------------------------------------------------
    # Cache helpers
    # ------------------------------------------------------------------

    def _emit(self, raw: float | None, ready: bool, triggered: bool) -> FeatureUpdate:
        fv = FeatureValue(
            name=self._spec.name,
            value=raw if ready else None,
            is_ready=ready,
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

    Parameters (from FeatureSpec)
    -----------------------------
    input_field : str  — bar field to average (e.g. ``"close"``)
    window : int       — bar count for the rolling window
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
        ts = _ts(event)
        v = _field(event, self._spec.input_field)
        if v is None:
            return self._no_change()
        self._state.push(v)
        triggered = self._should_trigger(ts)
        if triggered:
            self._last_trigger_ts = ts
        return self._emit(self._state.mean, self._state.is_full, triggered)

    def state_dict(self) -> dict:
        return {**self._base_state(), "rolling": self._state.state_dict()}

    def load_state_dict(self, state: dict) -> None:
        self._load_base(state)
        self._state.load_state_dict(state["rolling"])
        if self._state.is_full:
            self._cached = FeatureValue(
                name=self._spec.name, value=self._state.mean, is_ready=True
            )


class RollingStdFeature(_AbstractFeature):
    """Rolling sample standard deviation. O(1) update via running sum-of-squares.

    Parameters (from FeatureSpec)
    -----------------------------
    input_field : str  — bar field (e.g. ``"close"``)
    window : int       — bar count for the rolling window
    """

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
        ts = _ts(event)
        v = _field(event, self._spec.input_field)
        if v is None:
            return self._no_change()
        self._state.push(v)
        triggered = self._should_trigger(ts)
        if triggered:
            self._last_trigger_ts = ts
        return self._emit(self._state.std, self._state.is_full, triggered)

    def state_dict(self) -> dict:
        return {**self._base_state(), "rolling": self._state.state_dict()}

    def load_state_dict(self, state: dict) -> None:
        self._load_base(state)
        self._state.load_state_dict(state["rolling"])
        if self._state.is_full:
            self._cached = FeatureValue(
                name=self._spec.name, value=self._state.std, is_ready=True
            )


class RollingMinFeature(_AbstractFeature):
    """Rolling minimum of one bar field.

    min/max require O(window) scan; use monotone-deque variant for large windows.
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
        ts = _ts(event)
        v = _field(event, self._spec.input_field)
        if v is None:
            return self._no_change()
        self._state.push(v)
        triggered = self._should_trigger(ts)
        if triggered:
            self._last_trigger_ts = ts
        return self._emit(self._state.min, self._state.is_full, triggered)

    def state_dict(self) -> dict:
        return {**self._base_state(), "rolling": self._state.state_dict()}

    def load_state_dict(self, state: dict) -> None:
        self._load_base(state)
        self._state.load_state_dict(state["rolling"])
        if self._state.is_full:
            self._cached = FeatureValue(
                name=self._spec.name, value=self._state.min, is_ready=True
            )


class RollingMaxFeature(_AbstractFeature):
    """Rolling maximum of one bar field."""

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
        ts = _ts(event)
        v = _field(event, self._spec.input_field)
        if v is None:
            return self._no_change()
        self._state.push(v)
        triggered = self._should_trigger(ts)
        if triggered:
            self._last_trigger_ts = ts
        return self._emit(self._state.max, self._state.is_full, triggered)

    def state_dict(self) -> dict:
        return {**self._base_state(), "rolling": self._state.state_dict()}

    def load_state_dict(self, state: dict) -> None:
        self._load_base(state)
        self._state.load_state_dict(state["rolling"])
        if self._state.is_full:
            self._cached = FeatureValue(
                name=self._spec.name, value=self._state.max, is_ready=True
            )


class VWAPFeature(_AbstractFeature):
    """Volume-weighted average price.

    Supports three modes selected by window / window_unit:
    - Session (unbounded): window=None, window_unit=None.
    - Count-based rolling: window=N, window_unit="bars"/"events".
    - Time-based rolling: window=N, window_unit in seconds/milliseconds/minutes.

    Parameters (from FeatureSpec)
    -----------------------------
    window : int | None     — window size
    window_unit : str | None — "bars", "events", "seconds", "milliseconds", "minutes"
    params["price_field"]   — bar field used as price (default "close")
    params["volume_field"]  — bar field used as volume (default "volume")
    """

    _MS_PER_UNIT = {"seconds": 1_000, "milliseconds": 1, "minutes": 60_000}

    def __init__(self, spec: FeatureSpec) -> None:
        super().__init__(spec)
        window = spec.window
        unit = spec.window_unit or "bars"
        window_ms: int | None = None
        count_window: int | None = None
        if window is not None:
            if unit in self._MS_PER_UNIT:
                window_ms = window * self._MS_PER_UNIT[unit]
            else:
                count_window = window
        self._state = VWAPState(window=count_window, window_ms=window_ms)
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
        ts = _ts(event)
        price = _field(event, self._price_field)
        volume = _field(event, self._volume_field)
        if price is None or volume is None:
            return self._no_change()
        self._state.push(price, volume, ts_ms=ts)
        triggered = self._should_trigger(ts)
        if triggered:
            self._last_trigger_ts = ts
        return self._emit(self._state.vwap, True, triggered)

    def state_dict(self) -> dict:
        return {**self._base_state(), "vwap": self._state.state_dict()}

    def load_state_dict(self, state: dict) -> None:
        self._load_base(state)
        self._state.load_state_dict(state["vwap"])
        if self._state.count > 0:
            self._cached = FeatureValue(name=self._spec.name, value=self._state.vwap, is_ready=True)


class SimpleReturnFeature(_AbstractFeature):
    """Simple close-to-close return: (close_t - close_{t-1}) / close_{t-1}.

    Parameters (from FeatureSpec)
    -----------------------------
    input_field : str  — bar field (default "close")
    """

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
        ts = _ts(event)
        field = self._spec.input_field or "close"
        cur = _field(event, field)
        if cur is None:
            return self._no_change()
        triggered = self._should_trigger(ts)
        if triggered:
            self._last_trigger_ts = ts
        if self._prev is None or self._prev == 0.0:
            self._prev = cur
            return self._emit(None, False, False)
        ret = (cur - self._prev) / self._prev
        self._prev = cur
        return self._emit(ret, True, triggered)

    def state_dict(self) -> dict:
        return {**self._base_state(), "prev": self._prev}

    def load_state_dict(self, state: dict) -> None:
        self._load_base(state)
        self._prev = state.get("prev")


class LogReturnFeature(_AbstractFeature):
    """Log return: log(close_t / close_{t-1}).

    Parameters (from FeatureSpec)
    -----------------------------
    input_field : str  — bar field (default "close")
    """

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
        ts = _ts(event)
        field = self._spec.input_field or "close"
        cur = _field(event, field)
        if cur is None:
            return self._no_change()
        triggered = self._should_trigger(ts)
        if triggered:
            self._last_trigger_ts = ts
        if self._prev is None or self._prev <= 0.0 or cur <= 0.0:
            self._prev = cur
            return self._emit(None, False, False)
        ret = math.log(cur / self._prev)
        self._prev = cur
        return self._emit(ret, True, triggered)

    def state_dict(self) -> dict:
        return {**self._base_state(), "prev": self._prev}

    def load_state_dict(self, state: dict) -> None:
        self._load_base(state)
        self._prev = state.get("prev")


class EWMAFeature(_AbstractFeature):
    """Exponentially weighted moving average of one bar field.

    Parameters (from FeatureSpec)
    -----------------------------
    input_field : str     — bar field (e.g. ``"close"``)
    params["span"] : int  — span n (alpha = 2/(n+1)). Default 10.
    params["alpha"] : float — explicit alpha, overrides span.
    """

    def __init__(self, spec: FeatureSpec) -> None:
        super().__init__(spec)
        alpha = spec.params.get("alpha")
        span = spec.params.get("span") or spec.window or 10
        self._state = EWMAState(span=None if alpha else int(span), alpha=float(alpha) if alpha else None)

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
        ts = _ts(event)
        v = _field(event, self._spec.input_field)
        if v is None:
            return self._no_change()
        self._state.push(v)
        triggered = self._should_trigger(ts)
        if triggered:
            self._last_trigger_ts = ts
        return self._emit(self._state.value, True, triggered)

    def state_dict(self) -> dict:
        return {**self._base_state(), "ewma": self._state.state_dict()}

    def load_state_dict(self, state: dict) -> None:
        self._load_base(state)
        self._state.load_state_dict(state["ewma"])
        if self._state.value is not None:
            self._cached = FeatureValue(
                name=self._spec.name, value=self._state.value, is_ready=True
            )


# ---------------------------------------------------------------------------
# Quote-input features
# ---------------------------------------------------------------------------

class SpreadFeature(_AbstractFeature):
    """Bid-ask spread: ask_price - bid_price.

    Expects events with ask_price and bid_price attributes (QuoteTickInput).
    """

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
        ts = _ts(event)
        bid = _field(event, "bid_price")
        ask = _field(event, "ask_price")
        if bid is None or ask is None:
            return self._no_change()
        triggered = self._should_trigger(ts)
        if triggered:
            self._last_trigger_ts = ts
        return self._emit(ask - bid, True, triggered)

    def state_dict(self) -> dict:
        return self._base_state()

    def load_state_dict(self, state: dict) -> None:
        self._load_base(state)


class MidPriceFeature(_AbstractFeature):
    """Mid price: (ask_price + bid_price) / 2.

    Expects events with ask_price and bid_price attributes (QuoteTickInput).
    """

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
        ts = _ts(event)
        bid = _field(event, "bid_price")
        ask = _field(event, "ask_price")
        if bid is None or ask is None:
            return self._no_change()
        triggered = self._should_trigger(ts)
        if triggered:
            self._last_trigger_ts = ts
        return self._emit((ask + bid) / 2.0, True, triggered)

    def state_dict(self) -> dict:
        return self._base_state()

    def load_state_dict(self, state: dict) -> None:
        self._load_base(state)


# ---------------------------------------------------------------------------
# Order-book features
# ---------------------------------------------------------------------------

class BookImbalanceFeature(_AbstractFeature):
    """Order-book volume imbalance: (bid_vol - ask_vol) / (bid_vol + ask_vol).

    Works with either:
    - OrderBookInput (bids/asks as list[tuple[price, size]]): sums all levels.
    - Any event with bid_volume and ask_volume scalar attributes.

    Result in [-1, +1]: +1 means all volume on bid side, -1 all on ask side.
    """

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
        ts = _ts(event)

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
        triggered = self._should_trigger(ts)
        if triggered:
            self._last_trigger_ts = ts
        return self._emit(imbalance, imbalance is not None, triggered)

    def state_dict(self) -> dict:
        return self._base_state()

    def load_state_dict(self, state: dict) -> None:
        self._load_base(state)
