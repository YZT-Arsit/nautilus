"""Volatility features (pure Python).

    TrueRangeFeature          — max(high-low, |high-prev_close|, |low-prev_close|)
    ATRFeature                — rolling mean of true range (simple MA)
    VolatilityRatioFeature    — rvol(short) / max(rvol(long), eps)
    BollingerWidthFeature     — (upper-lower) / max(middle, eps)
    BollingerPercentBFeature  — (close-lower) / max(upper-lower, eps)
"""
from __future__ import annotations

import math
from typing import Any

from feature_engine.compute.feature_lib.base import (
    _EPS,
    _AbstractFeature,
    _bar_field,
    _ts_ns,
    FeatureUpdate,
    FeatureValue,
    RollingWindowState,
    WarmupRequirement,
)
from feature_engine.compute.spec import FeatureSpec


def _true_range(high: float, low: float, prev_close: float | None) -> float:
    if prev_close is None:
        return high - low
    return max(high - low, abs(high - prev_close), abs(low - prev_close))


class TrueRangeFeature(_AbstractFeature):
    """True range: ``max(high-low, |high-prev_close|, |low-prev_close|)``.

    On the first bar (no previous close) the true range is ``high - low``.
    """

    def __init__(self, spec: FeatureSpec) -> None:
        super().__init__(spec)
        self._prev_close: float | None = None

    def warmup_required(self) -> WarmupRequirement:
        return WarmupRequirement(n_events=1, unit="bars")

    @property
    def is_ready(self) -> bool:
        return self._cached.is_ready

    def reset(self) -> None:
        self._prev_close = None
        self._reset_base()

    def update(self, event: Any) -> FeatureUpdate:
        self._event_count += 1
        ts_ns = _ts_ns(event, self._spec.trigger.time_semantics)
        high = _bar_field(event, "high")
        low = _bar_field(event, "low")
        close = _bar_field(event, "close")
        if high is None or low is None or close is None:
            return self._no_change()
        tr = _true_range(high, low, self._prev_close)
        self._prev_close = close
        triggered = self._should_trigger(ts_ns)
        if triggered:
            self._last_trigger_ts = ts_ns
        return self._emit(tr, True, triggered,
                          source_event_time_ns=ts_ns, update_status="updated")

    def state_dict(self) -> dict:
        return {**self._base_state(), "prev_close": self._prev_close}

    def load_state_dict(self, state: dict) -> None:
        self._load_base(state)
        self._prev_close = state.get("prev_close")


class ATRFeature(_AbstractFeature):
    """Average True Range: rolling mean of true range over ``window`` bars.

    Uses a simple moving average of the true range (the spec's definition),
    not Wilder's smoothing.  The first bar's true range is ``high - low``.
    """

    def __init__(self, spec: FeatureSpec) -> None:
        super().__init__(spec)
        self._state = RollingWindowState(maxlen=spec.window or 1)
        self._prev_close: float | None = None

    def warmup_required(self) -> WarmupRequirement:
        return WarmupRequirement(n_events=self._spec.window or 1, unit="bars")

    @property
    def is_ready(self) -> bool:
        return self._state.is_full

    def reset(self) -> None:
        self._state.reset()
        self._prev_close = None
        self._reset_base()

    def update(self, event: Any) -> FeatureUpdate:
        self._event_count += 1
        ts_ns = _ts_ns(event, self._spec.trigger.time_semantics)
        h = _bar_field(event, "high")
        low = _bar_field(event, "low")
        c = _bar_field(event, "close")
        if None in (h, low, c):
            return self._no_change()
        tr = _true_range(h, low, self._prev_close)
        self._prev_close = c
        self._state.push(tr)
        triggered = self._should_trigger(ts_ns)
        if triggered:
            self._last_trigger_ts = ts_ns
        ready = self._state.is_full
        return self._emit(self._state.mean if ready else None, ready, triggered,
                          source_event_time_ns=ts_ns,
                          update_status="updated" if ready else "not_ready")

    def state_dict(self) -> dict:
        return {
            **self._base_state(),
            "rolling": self._state.state_dict(),
            "prev_close": self._prev_close,
        }

    def load_state_dict(self, state: dict) -> None:
        self._load_base(state)
        self._state.load_state_dict(state["rolling"])
        self._prev_close = state.get("prev_close")
        if self._state.is_full:
            self._cached = FeatureValue(
                name=self._spec.name, value=self._state.mean, is_ready=True,
            )


class VolatilityRatioFeature(_AbstractFeature):
    """Short/long realized-volatility ratio::

        std(logret, short) / max(std(logret, long), eps)

    Realized volatility is the sample std of log close-to-close returns.

    Parameters (from ``params``)
    -----------------------------
    short_window : int   — short volatility window (default 5).
    long_window  : int   — long volatility window (default 20).
    """

    def __init__(self, spec: FeatureSpec) -> None:
        super().__init__(spec)
        self._short = int(spec.params.get("short_window", 5))
        self._long = int(spec.params.get("long_window", 20))
        self._short_state = RollingWindowState(maxlen=self._short, track_squares=True)
        self._long_state = RollingWindowState(maxlen=self._long, track_squares=True)
        self._prev: float | None = None
        self._field_name = spec.input_field or "close"

    def warmup_required(self) -> WarmupRequirement:
        return WarmupRequirement(n_events=self._long + 1, unit="bars")

    @property
    def is_ready(self) -> bool:
        return self._short_state.is_full and self._long_state.is_full

    def reset(self) -> None:
        self._short_state.reset()
        self._long_state.reset()
        self._prev = None
        self._reset_base()

    def update(self, event: Any) -> FeatureUpdate:
        self._event_count += 1
        ts_ns = _ts_ns(event, self._spec.trigger.time_semantics)
        cur = _bar_field(event, self._field_name)
        if cur is None:
            return self._missing_field(self._field_name)
        triggered = self._should_trigger(ts_ns)
        if triggered:
            self._last_trigger_ts = ts_ns
        if self._prev is None or self._prev <= 0.0 or cur <= 0.0:
            self._prev = cur
            return self._emit(None, False, triggered,
                              source_event_time_ns=ts_ns, update_status="not_ready")
        log_ret = math.log(cur / self._prev)
        self._prev = cur
        self._short_state.push(log_ret)
        self._long_state.push(log_ret)
        if not self.is_ready:
            return self._emit(None, False, triggered,
                              source_event_time_ns=ts_ns, update_status="not_ready")
        short_vol = self._short_state.std or 0.0
        long_vol = self._long_state.std or 0.0
        ratio = short_vol / max(long_vol, _EPS)
        return self._emit(ratio, True, triggered,
                          source_event_time_ns=ts_ns, update_status="updated")

    def state_dict(self) -> dict:
        return {
            **self._base_state(),
            "short": self._short_state.state_dict(),
            "long": self._long_state.state_dict(),
            "prev": self._prev,
        }

    def load_state_dict(self, state: dict) -> None:
        self._load_base(state)
        self._short_state.load_state_dict(state["short"])
        self._long_state.load_state_dict(state["long"])
        self._prev = state.get("prev")


class _BollingerBase(_AbstractFeature):
    """Shared rolling mean/std of close for Bollinger-band features.

    Parameters (from ``params``)
    -----------------------------
    k : float   — number of standard deviations for the bands (default 2.0).
    """

    def __init__(self, spec: FeatureSpec) -> None:
        super().__init__(spec)
        self._state = RollingWindowState(maxlen=spec.window or 2, track_squares=True)
        self._k = float(spec.params.get("k", 2.0))
        self._field_name = spec.input_field or "close"

    def warmup_required(self) -> WarmupRequirement:
        return WarmupRequirement(n_events=self._spec.window or 2, unit="bars")

    @property
    def is_ready(self) -> bool:
        return self._state.is_full

    def reset(self) -> None:
        self._state.reset()
        self._reset_base()

    def _compute(self, close: float) -> float:  # pragma: no cover - overridden
        raise NotImplementedError

    def update(self, event: Any) -> FeatureUpdate:
        self._event_count += 1
        ts_ns = _ts_ns(event, self._spec.trigger.time_semantics)
        c = _bar_field(event, self._field_name)
        if c is None:
            return self._missing_field(self._field_name)
        self._state.push(c)
        triggered = self._should_trigger(ts_ns)
        if triggered:
            self._last_trigger_ts = ts_ns
        if not self._state.is_full:
            return self._emit(None, False, triggered,
                              source_event_time_ns=ts_ns, update_status="not_ready")
        return self._emit(self._compute(c), True, triggered,
                          source_event_time_ns=ts_ns, update_status="updated")

    def state_dict(self) -> dict:
        return {**self._base_state(), "rolling": self._state.state_dict()}

    def load_state_dict(self, state: dict) -> None:
        self._load_base(state)
        self._state.load_state_dict(state["rolling"])


class BollingerWidthFeature(_BollingerBase):
    """Bollinger band width: ``2k*std / max(mean, eps)``."""

    def _compute(self, close: float) -> float:
        middle = self._state.mean or 0.0
        std = self._state.std or 0.0
        return (2.0 * self._k * std) / max(middle, _EPS)


class BollingerPercentBFeature(_BollingerBase):
    """Bollinger %B: ``(close - lower) / max(upper - lower, eps)``."""

    def _compute(self, close: float) -> float:
        middle = self._state.mean or 0.0
        std = self._state.std or 0.0
        lower = middle - self._k * std
        band = 2.0 * self._k * std
        return (close - lower) / max(band, _EPS)
