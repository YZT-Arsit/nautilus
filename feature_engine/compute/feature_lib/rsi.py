"""Wilder Relative Strength Index feature."""
from __future__ import annotations

from typing import Any

from feature_engine.compute.feature_lib.base import (
    _EPS, _AbstractFeature, _bar_field, _ts_ns, FeatureUpdate, WarmupRequirement,
)
from feature_engine.compute.spec import FeatureSpec


class RelativeStrengthIndexFeature(_AbstractFeature):
    """RSI with Wilder smoothing and an explicit ``window+1`` warmup."""

    def __init__(self, spec: FeatureSpec) -> None:
        super().__init__(spec)
        self._window = int(spec.window or 14)
        self._previous: float | None = None
        self._gains: list[float] = []
        self._losses: list[float] = []
        self._avg_gain: float | None = None
        self._avg_loss: float | None = None

    def warmup_required(self) -> WarmupRequirement:
        return WarmupRequirement(n_events=self._window + 1, unit="bars")

    @property
    def is_ready(self) -> bool:
        return self._avg_gain is not None

    def reset(self) -> None:
        self._previous = None; self._gains.clear(); self._losses.clear()
        self._avg_gain = self._avg_loss = None; self._reset_base()

    def update(self, event: Any) -> FeatureUpdate:
        self._event_count += 1
        ts_ns = _ts_ns(event, self._spec.trigger.time_semantics)
        value = _bar_field(event, self._spec.input_field or "close")
        if value is None:
            return self._no_change()
        if self._previous is not None:
            change = value - self._previous
            gain, loss = max(change, 0.0), max(-change, 0.0)
            if self._avg_gain is None:
                self._gains.append(gain); self._losses.append(loss)
                if len(self._gains) == self._window:
                    self._avg_gain = sum(self._gains) / self._window
                    self._avg_loss = sum(self._losses) / self._window
            else:
                self._avg_gain = ((self._window - 1) * self._avg_gain + gain) / self._window
                self._avg_loss = ((self._window - 1) * (self._avg_loss or 0.0) + loss) / self._window
        self._previous = value
        triggered = self._should_trigger(ts_ns)
        if triggered:
            self._last_trigger_ts = ts_ns
        if not self.is_ready:
            return self._emit(None, False, triggered, source_event_time_ns=ts_ns, update_status="not_ready")
        if (self._avg_loss or 0.0) <= _EPS:
            result = 100.0 if (self._avg_gain or 0.0) > _EPS else 50.0
        else:
            result = 100.0 - 100.0 / (1.0 + (self._avg_gain or 0.0) / self._avg_loss)
        return self._emit(result, True, triggered, source_event_time_ns=ts_ns, update_status="updated")

    def state_dict(self) -> dict:
        return {**self._base_state(), "previous": self._previous, "gains": self._gains,
                "losses": self._losses, "avg_gain": self._avg_gain, "avg_loss": self._avg_loss}

    def load_state_dict(self, state: dict) -> None:
        self._load_base(state); self._previous = state.get("previous")
        self._gains = list(state.get("gains", [])); self._losses = list(state.get("losses", []))
        self._avg_gain = state.get("avg_gain"); self._avg_loss = state.get("avg_loss")

