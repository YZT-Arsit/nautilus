"""Volume features (pure Python).

    VolumeZScoreFeature   — z-score of volume
    VolumeRatioFeature    — volume / max(rolling_mean(volume, n), eps)
    QuoteVolumeFeature    — event quote_volume, else close * volume
    VWAPDistanceFeature   — close / max(vwap, eps) - 1
"""
from __future__ import annotations

from typing import Any

from feature_engine.compute.feature_lib.base import (
    _EPS,
    _NS_PER_UNIT,
    _AbstractFeature,
    _bar_field,
    _ts_ns,
    FeatureUpdate,
    RollingWindowState,
    VWAPState,
    WarmupRequirement,
)
from feature_engine.compute.feature_lib.normalization import ZScoreFeature
from feature_engine.compute.spec import FeatureSpec


class VolumeZScoreFeature(ZScoreFeature):
    """Z-score of volume: ``ZScoreFeature`` with default ``input_field="volume"``."""

    _DEFAULT_FIELD = "volume"


class VolumeRatioFeature(_AbstractFeature):
    """Volume ratio: ``volume / max(rolling_mean(volume, n), eps)``."""

    def __init__(self, spec: FeatureSpec) -> None:
        super().__init__(spec)
        self._state = RollingWindowState(maxlen=spec.window or 1)
        self._field_name = spec.input_field or "volume"

    def warmup_required(self) -> WarmupRequirement:
        return WarmupRequirement(n_events=self._spec.window or 1, unit="bars")

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
        ratio = v / max(mean, _EPS)
        return self._emit(ratio, True, triggered,
                          source_event_time_ns=ts_ns, update_status="updated")

    def state_dict(self) -> dict:
        return {**self._base_state(), "rolling": self._state.state_dict()}

    def load_state_dict(self, state: dict) -> None:
        self._load_base(state)
        self._state.load_state_dict(state["rolling"])


class QuoteVolumeFeature(_AbstractFeature):
    """Quote (notional) volume.

    Reads ``quote_volume`` from the event when present; otherwise falls back to
    ``close * volume``.
    """

    def warmup_required(self) -> WarmupRequirement:
        return WarmupRequirement(n_events=1, unit="bars")

    @property
    def is_ready(self) -> bool:
        return self._cached.is_ready

    def reset(self) -> None:
        self._reset_base()

    def update(self, event: Any) -> FeatureUpdate:
        self._event_count += 1
        ts_ns = _ts_ns(event, self._spec.trigger.time_semantics)
        qv = _bar_field(event, "quote_volume")
        if qv is None:
            close = _bar_field(event, "close")
            volume = _bar_field(event, "volume")
            if close is None or volume is None:
                return self._no_change()
            qv = close * volume
        triggered = self._should_trigger(ts_ns)
        if triggered:
            self._last_trigger_ts = ts_ns
        return self._emit(qv, True, triggered,
                          source_event_time_ns=ts_ns, update_status="updated")

    def state_dict(self) -> dict:
        return self._base_state()

    def load_state_dict(self, state: dict) -> None:
        self._load_base(state)


class VWAPDistanceFeature(_AbstractFeature):
    """Distance of close from VWAP: ``close / max(vwap, eps) - 1``.

    VWAP is computed internally (session by default, or a rolling count/time
    window via ``window`` / ``window_unit``).  Reuses ``VWAPState``.

    Parameters (from ``params``)
    -----------------------------
    price_field  : str   — VWAP price field (default "close").
    volume_field : str   — VWAP volume field (default "volume").
    """

    def __init__(self, spec: FeatureSpec) -> None:
        super().__init__(spec)
        window = spec.window
        unit = spec.window_unit or "bars"
        window_ns: int | None = None
        count_window: int | None = None
        if window is not None:
            if unit in _NS_PER_UNIT:
                window_ns = window * _NS_PER_UNIT[unit]
            else:
                count_window = window
        self._state = VWAPState(window=count_window, window_ns=window_ns)
        self._price_field = spec.params.get("price_field", "close")
        self._volume_field = spec.params.get("volume_field", "volume")

    def warmup_required(self) -> WarmupRequirement:
        return WarmupRequirement(n_events=self._spec.window or 1,
                                 unit=self._spec.window_unit or "bars", mandatory=False)

    @property
    def is_ready(self) -> bool:
        return self._cached.is_ready

    def reset(self) -> None:
        self._state.reset()
        self._reset_base()

    def update(self, event: Any) -> FeatureUpdate:
        self._event_count += 1
        ts_ns = _ts_ns(event, self._spec.trigger.time_semantics)
        price = _bar_field(event, self._price_field)
        close = _bar_field(event, "close")
        volume = _bar_field(event, self._volume_field)
        if price is None or close is None or volume is None:
            return self._no_change()
        self._state.push(price, volume, ts_ns=ts_ns)
        triggered = self._should_trigger(ts_ns)
        if triggered:
            self._last_trigger_ts = ts_ns
        vwap = self._state.vwap
        if vwap is None:
            return self._emit(None, False, triggered,
                              source_event_time_ns=ts_ns, update_status="not_ready")
        value = close / max(vwap, _EPS) - 1.0
        return self._emit(value, True, triggered,
                          source_event_time_ns=ts_ns, update_status="updated")

    def state_dict(self) -> dict:
        return {**self._base_state(), "vwap": self._state.state_dict()}

    def load_state_dict(self, state: dict) -> None:
        self._load_base(state)
        self._state.load_state_dict(state["vwap"])
