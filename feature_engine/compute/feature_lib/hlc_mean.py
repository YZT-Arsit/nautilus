"""Moving average of HLC3 typical price."""

from __future__ import annotations

from typing import Any

from feature_engine.compute.feature_lib.base import (
    _AbstractFeature, _bar_field, _ts_ns, FeatureUpdate,
    RollingWindowState, WarmupRequirement,
)
from feature_engine.compute.spec import FeatureSpec


class HlcMeanFeature(_AbstractFeature):
    """SMA of ``(high + low + close) / 3``."""

    def __init__(self, spec: FeatureSpec) -> None:
        super().__init__(spec)
        self._state = RollingWindowState(maxlen=spec.window or 20)

    def warmup_required(self) -> WarmupRequirement:
        return WarmupRequirement(n_events=self._spec.window or 20, unit="bars")

    @property
    def is_ready(self) -> bool:
        return self._state.is_full

    def reset(self) -> None:
        self._state.reset(); self._reset_base()

    def update(self, event: Any) -> FeatureUpdate:
        self._event_count += 1
        ts_ns = _ts_ns(event, self._spec.trigger.time_semantics)
        high, low, close = (_bar_field(event, name) for name in ("high", "low", "close"))
        if None in (high, low, close):
            return self._no_change()
        self._state.push((high + low + close) / 3.0)
        triggered = self._should_trigger(ts_ns)
        if triggered:
            self._last_trigger_ts = ts_ns
        ready = self.is_ready
        return self._emit(self._state.mean if ready else None, ready, triggered,
                          source_event_time_ns=ts_ns,
                          update_status="updated" if ready else "not_ready")

    def state_dict(self) -> dict:
        return {**self._base_state(), "rolling": self._state.state_dict()}

    def load_state_dict(self, state: dict) -> None:
        self._load_base(state); self._state.load_state_dict(state["rolling"])
