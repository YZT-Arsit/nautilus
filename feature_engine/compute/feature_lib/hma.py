"""Hull moving average feature."""

from __future__ import annotations

from collections import deque
from math import isqrt
from typing import Any

from feature_engine.compute.feature_lib.base import (
    _AbstractFeature, _bar_field, _ts_ns, FeatureUpdate, WarmupRequirement,
)
from feature_engine.compute.spec import FeatureSpec


def _wma(values: list[float]) -> float:
    weights = range(1, len(values) + 1)
    return sum(value * weight for value, weight in zip(values, weights)) / sum(weights)


class HullMovingAverageFeature(_AbstractFeature):
    """``WMA(2*WMA(x,n/2)-WMA(x,n), floor(sqrt(n)))``."""

    def __init__(self, spec: FeatureSpec) -> None:
        super().__init__(spec)
        self._window = spec.window or 1
        self._sqrt_window = max(1, isqrt(self._window))
        self._values: deque[float] = deque(maxlen=self._window)
        self._raw_hull: deque[float] = deque(maxlen=self._sqrt_window)

    def warmup_required(self) -> WarmupRequirement:
        return WarmupRequirement(n_events=self._window + self._sqrt_window - 1, unit="bars")

    @property
    def is_ready(self) -> bool:
        return len(self._raw_hull) == self._sqrt_window

    def reset(self) -> None:
        self._values.clear(); self._raw_hull.clear(); self._reset_base()

    def update(self, event: Any) -> FeatureUpdate:
        self._event_count += 1
        ts_ns = _ts_ns(event, self._spec.trigger.time_semantics)
        value = _bar_field(event, self._spec.input_field or "close")
        if value is None:
            return self._missing_field(self._spec.input_field or "close")
        self._values.append(value)
        if len(self._values) == self._window:
            values = list(self._values)
            half = max(1, self._window // 2)
            self._raw_hull.append(2.0 * _wma(values[-half:]) - _wma(values))
        triggered = self._should_trigger(ts_ns)
        if triggered:
            self._last_trigger_ts = ts_ns
        ready = self.is_ready
        return self._emit(_wma(list(self._raw_hull)) if ready else None, ready, triggered,
                          source_event_time_ns=ts_ns,
                          update_status="updated" if ready else "not_ready")

    def state_dict(self) -> dict:
        return {**self._base_state(), "values": list(self._values), "raw_hull": list(self._raw_hull)}

    def load_state_dict(self, state: dict) -> None:
        self._load_base(state)
        self._values = deque(state["values"], maxlen=self._window)
        self._raw_hull = deque(state["raw_hull"], maxlen=self._sqrt_window)
