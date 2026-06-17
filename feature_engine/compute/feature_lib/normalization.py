"""Normalization features (pure Python).

    ZScoreFeature — (x - rolling_mean(x, n)) / max(rolling_std(x, n), eps)
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


class ZScoreFeature(_AbstractFeature):
    """Rolling z-score: ``(x - rolling_mean(x, n)) / max(rolling_std(x, n), eps)``.

    ``x`` is ``spec.input_field`` (default ``"close"``).  Subclasses may override
    ``_DEFAULT_FIELD`` (e.g. ``VolumeZScoreFeature`` uses ``"volume"``).
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
        if not self._state.is_full:
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
