"""Stateful SuperTrend line and direction for completed OHLC bars."""
from __future__ import annotations

from collections import deque
from typing import Any

from feature_engine.compute.feature_lib.base import (
    _AbstractFeature, _bar_field, _ts_ns, FeatureUpdate, WarmupRequirement,
)
from feature_engine.compute.spec import FeatureSpec


class SuperTrendFeature(_AbstractFeature):
    """SuperTrend using the source-specified SMA(close) centre and mean TR."""

    def __init__(self, spec: FeatureSpec) -> None:
        super().__init__(spec)
        self._window = int(spec.window or 10)
        self._multiplier = float(spec.params.get("multiplier", 3.0))
        self._output = str(spec.params.get("output", "line"))
        if self._window <= 0 or self._multiplier <= 0:
            raise ValueError("SuperTrend window and multiplier must be positive")
        if self._output not in {"line", "direction"}:
            raise ValueError(f"unsupported SuperTrend output: {self._output}")
        self._closes: deque[float] = deque(maxlen=self._window)
        self._trs: deque[float] = deque(maxlen=self._window)
        self._previous_close: float | None = None
        self._upper: float | None = None
        self._lower: float | None = None
        self._direction = 0

    def warmup_required(self) -> WarmupRequirement:
        return WarmupRequirement(n_events=self._window, unit="bars")

    @property
    def is_ready(self) -> bool:
        return len(self._closes) == self._window

    def reset(self) -> None:
        self._closes.clear(); self._trs.clear()
        self._previous_close = self._upper = self._lower = None
        self._direction = 0
        self._reset_base()

    def update(self, event: Any) -> FeatureUpdate:
        self._event_count += 1
        ts_ns = _ts_ns(event, self._spec.trigger.time_semantics)
        high, low, close = (_bar_field(event, field) for field in ("high", "low", "close"))
        if None in (high, low, close):
            return self._no_change()
        tr = high - low if self._previous_close is None else max(
            high - low, abs(high - self._previous_close), abs(low - self._previous_close)
        )
        previous_close = self._previous_close
        self._closes.append(close); self._trs.append(tr)
        self._previous_close = close
        triggered = self._should_trigger(ts_ns)
        if triggered:
            self._last_trigger_ts = ts_ns
        if not self.is_ready:
            return self._emit(None, False, triggered, source_event_time_ns=ts_ns, update_status="not_ready")

        middle = sum(self._closes) / self._window
        atr = sum(self._trs) / self._window
        basic_upper = middle + self._multiplier * atr
        basic_lower = middle - self._multiplier * atr
        if self._upper is None or self._lower is None:
            self._upper, self._lower = basic_upper, basic_lower
            self._direction = 1 if close >= middle else -1
        else:
            old_upper, old_lower = self._upper, self._lower
            self._upper = basic_upper if basic_upper < old_upper or (previous_close or close) > old_upper else old_upper
            self._lower = basic_lower if basic_lower > old_lower or (previous_close or close) < old_lower else old_lower
            if self._direction <= 0 and close > old_upper:
                self._direction = 1
            elif self._direction >= 0 and close < old_lower:
                self._direction = -1
        value = self._lower if self._direction > 0 else self._upper
        if self._output == "direction":
            value = float(self._direction)
        return self._emit(value, True, triggered, source_event_time_ns=ts_ns, update_status="updated")

    def state_dict(self) -> dict:
        return {**self._base_state(), "closes": list(self._closes), "trs": list(self._trs),
                "previous_close": self._previous_close, "upper": self._upper,
                "lower": self._lower, "direction": self._direction}

    def load_state_dict(self, state: dict) -> None:
        self._load_base(state)
        self._closes = deque(state.get("closes", []), maxlen=self._window)
        self._trs = deque(state.get("trs", []), maxlen=self._window)
        self._previous_close = state.get("previous_close")
        self._upper = state.get("upper"); self._lower = state.get("lower")
        self._direction = int(state.get("direction", 0))
