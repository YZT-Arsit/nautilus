"""Awesome Oscillator feature."""
from __future__ import annotations

from typing import Any

from feature_engine.compute.feature_lib.base import (
    _AbstractFeature, _bar_field, _ts_ns, FeatureUpdate, RollingWindowState, WarmupRequirement,
)
from feature_engine.compute.spec import FeatureSpec


class AwesomeOscillatorFeature(_AbstractFeature):
    """SMA(fast, HL2) minus SMA(slow, HL2)."""

    def __init__(self, spec: FeatureSpec) -> None:
        super().__init__(spec)
        self._fast_window = int(spec.params.get("fast_window", 5))
        self._slow_window = int(spec.params.get("slow_window", spec.window or 34))
        if not 0 < self._fast_window < self._slow_window:
            raise ValueError("AO requires 0 < fast_window < slow_window")
        self._fast = RollingWindowState(maxlen=self._fast_window)
        self._slow = RollingWindowState(maxlen=self._slow_window)

    def warmup_required(self) -> WarmupRequirement:
        return WarmupRequirement(n_events=self._slow_window, unit="bars")

    @property
    def is_ready(self) -> bool:
        return self._slow.is_full

    def reset(self) -> None:
        self._fast.reset(); self._slow.reset(); self._reset_base()

    def update(self, event: Any) -> FeatureUpdate:
        self._event_count += 1
        ts_ns = _ts_ns(event, self._spec.trigger.time_semantics)
        high, low = _bar_field(event, "high"), _bar_field(event, "low")
        if high is None or low is None:
            return self._no_change()
        midpoint = (high + low) / 2.0
        self._fast.push(midpoint); self._slow.push(midpoint)
        triggered = self._should_trigger(ts_ns)
        if triggered:
            self._last_trigger_ts = ts_ns
        value = (self._fast.mean or 0.0) - (self._slow.mean or 0.0) if self.is_ready else None
        return self._emit(value, self.is_ready, triggered, source_event_time_ns=ts_ns,
                          update_status="updated" if self.is_ready else "not_ready")

    def state_dict(self) -> dict:
        return {**self._base_state(), "fast": self._fast.state_dict(), "slow": self._slow.state_dict()}

    def load_state_dict(self, state: dict) -> None:
        self._load_base(state); self._fast.load_state_dict(state["fast"]); self._slow.load_state_dict(state["slow"])

