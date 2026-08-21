"""Welles Wilder Parabolic SAR feature."""
from __future__ import annotations

from typing import Any

from feature_engine.compute.feature_lib.base import (
    _AbstractFeature, _bar_field, _ts_ns, FeatureUpdate, WarmupRequirement,
)
from feature_engine.compute.spec import FeatureSpec


class ParabolicSarFeature(_AbstractFeature):
    """Parabolic SAR value or trend direction with standard clamp/reversal rules."""

    def __init__(self, spec: FeatureSpec) -> None:
        super().__init__(spec)
        self._step = float(spec.params.get("step", 0.02))
        self._maximum = float(spec.params.get("maximum", 0.2))
        self._output = str(spec.params.get("output", "sar"))
        if not 0 < self._step <= self._maximum or self._output not in {"sar", "direction"}:
            raise ValueError("PSAR requires 0 < step <= maximum and output sar/direction")
        self._highs: list[float] = []
        self._lows: list[float] = []
        self._closes: list[float] = []
        self._direction = 0
        self._sar: float | None = None
        self._extreme: float | None = None
        self._acceleration = self._step

    def warmup_required(self) -> WarmupRequirement:
        return WarmupRequirement(n_events=2, unit="bars")

    @property
    def is_ready(self) -> bool:
        return self._sar is not None

    def reset(self) -> None:
        self._highs.clear(); self._lows.clear(); self._closes.clear()
        self._direction = 0; self._sar = self._extreme = None; self._acceleration = self._step
        self._reset_base()

    def update(self, event: Any) -> FeatureUpdate:
        self._event_count += 1
        ts_ns = _ts_ns(event, self._spec.trigger.time_semantics)
        high, low, close = (_bar_field(event, field) for field in ("high", "low", "close"))
        if None in (high, low, close):
            return self._no_change()
        self._highs.append(high); self._lows.append(low); self._closes.append(close)
        if len(self._highs) > 3:
            del self._highs[0]; del self._lows[0]; del self._closes[0]
        if len(self._closes) == 2 and self._sar is None:
            self._direction = 1 if self._closes[-1] >= self._closes[-2] else -1
            self._sar = min(self._lows) if self._direction > 0 else max(self._highs)
            self._extreme = max(self._highs) if self._direction > 0 else min(self._lows)
        elif self._sar is not None and self._extreme is not None:
            projected = self._sar + self._acceleration * (self._extreme - self._sar)
            if self._direction > 0:
                projected = min(projected, *self._lows[:-1][-2:])
                if low < projected:
                    self._direction = -1; self._sar = self._extreme; self._extreme = low
                    self._acceleration = self._step
                else:
                    self._sar = projected
                    if high > self._extreme:
                        self._extreme = high; self._acceleration = min(self._maximum, self._acceleration + self._step)
            else:
                projected = max(projected, *self._highs[:-1][-2:])
                if high > projected:
                    self._direction = 1; self._sar = self._extreme; self._extreme = high
                    self._acceleration = self._step
                else:
                    self._sar = projected
                    if low < self._extreme:
                        self._extreme = low; self._acceleration = min(self._maximum, self._acceleration + self._step)
        triggered = self._should_trigger(ts_ns)
        if triggered:
            self._last_trigger_ts = ts_ns
        value = self._sar if self._output == "sar" else float(self._direction)
        return self._emit(value, self.is_ready, triggered, source_event_time_ns=ts_ns,
                          update_status="updated" if self.is_ready else "not_ready")

    def state_dict(self) -> dict:
        return {**self._base_state(), "highs": self._highs, "lows": self._lows,
                "closes": self._closes, "direction": self._direction, "sar": self._sar,
                "extreme": self._extreme, "acceleration": self._acceleration}

    def load_state_dict(self, state: dict) -> None:
        self._load_base(state); self._highs = list(state.get("highs", [])); self._lows = list(state.get("lows", []))
        self._closes = list(state.get("closes", [])); self._direction = int(state.get("direction", 0))
        self._sar = state.get("sar"); self._extreme = state.get("extreme")
        self._acceleration = float(state.get("acceleration", self._step))

