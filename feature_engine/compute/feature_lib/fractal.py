"""Lookahead-safe confirmed five-bar fractal levels."""
from __future__ import annotations

from typing import Any

from feature_engine.compute.feature_lib.base import (
    _AbstractFeature, _bar_field, _ts_ns, FeatureUpdate, WarmupRequirement,
)
from feature_engine.compute.spec import FeatureSpec


class ConfirmedFractalFeature(_AbstractFeature):
    """Latest upper/lower five-bar fractal, revealed two bars after its pivot."""

    def __init__(self, spec: FeatureSpec) -> None:
        super().__init__(spec)
        self._output = str(spec.params.get("output", "upper"))
        if self._output not in {"upper", "lower", "upper_pulse", "lower_pulse"}:
            raise ValueError(f"unsupported fractal output: {self._output}")
        self._highs: list[float] = []
        self._lows: list[float] = []
        self._last_upper: float | None = None
        self._last_lower: float | None = None

    def warmup_required(self) -> WarmupRequirement:
        return WarmupRequirement(n_events=5, unit="bars")

    @property
    def is_ready(self) -> bool:
        return len(self._highs) == 5

    def reset(self) -> None:
        self._highs.clear(); self._lows.clear(); self._last_upper = self._last_lower = None
        self._reset_base()

    def update(self, event: Any) -> FeatureUpdate:
        self._event_count += 1
        ts_ns = _ts_ns(event, self._spec.trigger.time_semantics)
        high, low = _bar_field(event, "high"), _bar_field(event, "low")
        if high is None or low is None:
            return self._no_change()
        self._highs.append(high); self._lows.append(low)
        if len(self._highs) > 5:
            del self._highs[0]; del self._lows[0]
        upper_pulse = lower_pulse = False
        if len(self._highs) == 5:
            upper_pulse = self._highs[2] > max(self._highs[0], self._highs[1], self._highs[3], self._highs[4])
            lower_pulse = self._lows[2] < min(self._lows[0], self._lows[1], self._lows[3], self._lows[4])
            if upper_pulse:
                self._last_upper = self._highs[2]
            if lower_pulse:
                self._last_lower = self._lows[2]
        triggered = self._should_trigger(ts_ns)
        if triggered:
            self._last_trigger_ts = ts_ns
        values = {"upper": self._last_upper, "lower": self._last_lower,
                  "upper_pulse": float(upper_pulse), "lower_pulse": float(lower_pulse)}
        value = values[self._output]
        ready = self.is_ready and (self._output.endswith("pulse") or value is not None)
        return self._emit(value, ready, triggered, source_event_time_ns=ts_ns,
                          update_status="updated" if ready else "not_ready")

    def state_dict(self) -> dict:
        return {**self._base_state(), "highs": self._highs, "lows": self._lows,
                "last_upper": self._last_upper, "last_lower": self._last_lower}

    def load_state_dict(self, state: dict) -> None:
        self._load_base(state); self._highs = list(state.get("highs", [])); self._lows = list(state.get("lows", []))
        self._last_upper = state.get("last_upper"); self._last_lower = state.get("last_lower")

