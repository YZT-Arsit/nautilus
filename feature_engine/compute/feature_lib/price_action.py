"""Price / bar-structure features (pure Python).

    RollingRangeFeature           — high - low
    PricePositionFeature          — stochastic-style position in the n-bar range
    DrawdownFromRollingHighFeature— close / rolling_max(close, n) - 1
    BreakoutUpFeature             — close > previous rolling_max(high, n)
    BreakoutDownFeature           — close < previous rolling_min(low, n)
    CandleBodyRatioFeature        — |close-open| / max(high-low, eps)
    UpperShadowRatioFeature       — (high-max(open,close)) / max(high-low, eps)
    LowerShadowRatioFeature       — (min(open,close)-low) / max(high-low, eps)
"""
from __future__ import annotations

from typing import Any

from feature_engine.compute.feature_lib.base import (
    _EPS,
    _AbstractFeature,
    _bar_field,
    _ts_ns,
    FeatureUpdate,
    FeatureValue,
    RollingWindowState,
    WarmupRequirement,
)
from feature_engine.compute.spec import FeatureSpec


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
        return self._emit(high - low, True, triggered,
                          source_event_time_ns=ts_ns, update_status="updated")

    def state_dict(self) -> dict:
        return self._base_state()

    def load_state_dict(self, state: dict) -> None:
        self._load_base(state)


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
        return self._emit(ratio, True, triggered,
                          source_event_time_ns=ts_ns, update_status="updated")

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
        return self._emit(ratio, True, triggered,
                          source_event_time_ns=ts_ns, update_status="updated")

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
        return self._emit(ratio, True, triggered,
                          source_event_time_ns=ts_ns, update_status="updated")

    def state_dict(self) -> dict:
        return self._base_state()

    def load_state_dict(self, state: dict) -> None:
        self._load_base(state)


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
        if not self.is_ready:
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
        if not self._state.is_full:
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
    """Breakout up: ``close > previous rolling_max(high, n)`` (bool).

    "Previous" means the rolling max of the prior n highs, evaluated **before**
    the current bar's high is added, so a bar cannot break out against itself.
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
        ready = self._state.is_full
        result: bool | None = (c > self._state.max) if ready else None
        self._state.push(h)
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
        result: bool | None = (c < self._state.min) if ready else None
        self._state.push(low)
        return self._emit(result, ready, triggered,
                          source_event_time_ns=ts_ns,
                          update_status="updated" if ready else "not_ready")

    def state_dict(self) -> dict:
        return {**self._base_state(), "rolling": self._state.state_dict()}

    def load_state_dict(self, state: dict) -> None:
        self._load_base(state)
        self._state.load_state_dict(state["rolling"])
