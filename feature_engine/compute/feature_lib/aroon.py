"""Aroon Up, Down and Oscillator feature."""
from __future__ import annotations

from typing import Any

from feature_engine.compute.feature_lib.base import (
    _AbstractFeature, _bar_field, _ts_ns, FeatureUpdate, WarmupRequirement,
)
from feature_engine.compute.spec import FeatureSpec


class AroonFeature(_AbstractFeature):
    """Standard Aroon value over completed bars, selected by ``params['output']``."""

    def __init__(self, spec: FeatureSpec) -> None:
        super().__init__(spec)
        self._window = int(spec.window or 25)
        self._output = str(spec.params.get("output", "up"))
        if self._output not in {"up", "down", "oscillator"}:
            raise ValueError(f"unsupported Aroon output: {self._output}")
        self._highs: list[float] = []
        self._lows: list[float] = []

    def warmup_required(self) -> WarmupRequirement:
        return WarmupRequirement(n_events=self._window, unit="bars")

    @property
    def is_ready(self) -> bool:
        return len(self._highs) == self._window

    def reset(self) -> None:
        self._highs.clear(); self._lows.clear(); self._reset_base()

    @staticmethod
    def _last_index(values: list[float], target: float) -> int:
        return len(values) - 1 - values[::-1].index(target)

    def update(self, event: Any) -> FeatureUpdate:
        self._event_count += 1
        ts_ns = _ts_ns(event, self._spec.trigger.time_semantics)
        high, low = _bar_field(event, "high"), _bar_field(event, "low")
        if high is None or low is None:
            return self._no_change()
        self._highs.append(high); self._lows.append(low)
        if len(self._highs) > self._window:
            del self._highs[0]; del self._lows[0]
        triggered = self._should_trigger(ts_ns)
        if triggered:
            self._last_trigger_ts = ts_ns
        if not self.is_ready:
            return self._emit(None, False, triggered, source_event_time_ns=ts_ns, update_status="not_ready")
        denominator = max(self._window - 1, 1)
        up = 100.0 * self._last_index(self._highs, max(self._highs)) / denominator
        down = 100.0 * self._last_index(self._lows, min(self._lows)) / denominator
        value = up if self._output == "up" else down if self._output == "down" else up - down
        return self._emit(value, True, triggered, source_event_time_ns=ts_ns, update_status="updated")

    def state_dict(self) -> dict:
        return {**self._base_state(), "highs": self._highs, "lows": self._lows}

    def load_state_dict(self, state: dict) -> None:
        self._load_base(state); self._highs = list(state.get("highs", [])); self._lows = list(state.get("lows", []))

