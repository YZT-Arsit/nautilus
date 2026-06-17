"""Trend / momentum features (pure Python).

    ReturnNFeature   — close / close[-n] - 1
    MomentumNFeature — close - close[-n]
"""
from __future__ import annotations

from typing import Any

from feature_engine.compute.feature_lib.base import (
    _EPS,
    _AbstractFeature,
    _bar_field,
    _ts_ns,
    FeatureUpdate,
    RollingWindowState,
    WarmupRequirement,
)
from feature_engine.compute.spec import FeatureSpec


class ReturnNFeature(_AbstractFeature):
    """N-bar simple return: ``close / close[-n] - 1`` (``window`` == n)."""

    def __init__(self, spec: FeatureSpec) -> None:
        super().__init__(spec)
        self._n = spec.window or 1
        self._state = RollingWindowState(maxlen=self._n + 1)
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
        self._n = spec.window or 1
        self._state = RollingWindowState(maxlen=self._n + 1)
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
