"""Standard exponentially weighted moving average feature."""
from __future__ import annotations

from typing import Any

from feature_engine.compute.feature_lib.base import (
    _AbstractFeature, _bar_field, _ts_ns, FeatureUpdate, WarmupRequirement,
)
from feature_engine.compute.spec import FeatureSpec


class ExponentialMovingAverageFeature(_AbstractFeature):
    """EMA seeded by the first ``window``-bar simple mean."""

    def __init__(self, spec: FeatureSpec) -> None:
        super().__init__(spec)
        self._window = int(spec.window or 1)
        self._alpha = float(spec.params.get("alpha", 2.0 / (self._window + 1.0)))
        if not 0.0 < self._alpha <= 1.0:
            raise ValueError("EMA alpha must be in (0, 1]")
        self._seed: list[float] = []
        self._ema: float | None = None

    def warmup_required(self) -> WarmupRequirement:
        return WarmupRequirement(n_events=self._window, unit="bars")

    @property
    def is_ready(self) -> bool:
        return self._ema is not None

    def reset(self) -> None:
        self._seed.clear(); self._ema = None; self._reset_base()

    def update(self, event: Any) -> FeatureUpdate:
        self._event_count += 1
        ts_ns = _ts_ns(event, self._spec.trigger.time_semantics)
        value = _bar_field(event, self._spec.input_field or "close")
        if value is None:
            return self._no_change()
        if self._ema is None:
            self._seed.append(value)
            if len(self._seed) == self._window:
                self._ema = sum(self._seed) / self._window
        else:
            self._ema += self._alpha * (value - self._ema)
        triggered = self._should_trigger(ts_ns)
        if triggered:
            self._last_trigger_ts = ts_ns
        return self._emit(self._ema, self.is_ready, triggered, source_event_time_ns=ts_ns,
                          update_status="updated" if self.is_ready else "not_ready")

    def state_dict(self) -> dict:
        return {**self._base_state(), "seed": self._seed, "ema": self._ema}

    def load_state_dict(self, state: dict) -> None:
        self._load_base(state); self._seed = list(state.get("seed", [])); self._ema = state.get("ema")

