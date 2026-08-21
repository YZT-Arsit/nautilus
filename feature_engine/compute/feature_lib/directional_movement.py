"""Wilder directional-movement features (ADX, +DI and -DI)."""

from __future__ import annotations

from typing import Any

from feature_engine.compute.feature_lib.base import (
    _EPS,
    _AbstractFeature,
    _bar_field,
    _ts_ns,
    FeatureUpdate,
    WarmupRequirement,
)
from feature_engine.compute.spec import FeatureSpec


class DirectionalMovementFeature(_AbstractFeature):
    """Wilder-smoothed ADX/+DI/-DI selected by ``params['output']``."""

    def __init__(self, spec: FeatureSpec) -> None:
        super().__init__(spec)
        self._window = int(spec.window or 14)
        self._output = str(spec.params.get("output", "adx"))
        if self._output not in {"adx", "plus_di", "minus_di"}:
            raise ValueError(f"unsupported directional-movement output: {self._output}")
        self._previous_high: float | None = None
        self._previous_low: float | None = None
        self._previous_close: float | None = None
        self._seed_tr: list[float] = []
        self._seed_plus: list[float] = []
        self._seed_minus: list[float] = []
        self._smoothed_tr: float | None = None
        self._smoothed_plus: float | None = None
        self._smoothed_minus: float | None = None
        self._dx_seed: list[float] = []
        self._adx: float | None = None

    def warmup_required(self) -> WarmupRequirement:
        events = self._window * 2 - 1 if self._output == "adx" else self._window
        return WarmupRequirement(n_events=events, unit="bars")

    @property
    def is_ready(self) -> bool:
        return self._adx is not None if self._output == "adx" else self._smoothed_tr is not None

    def reset(self) -> None:
        self._previous_high = self._previous_low = self._previous_close = None
        self._seed_tr.clear(); self._seed_plus.clear(); self._seed_minus.clear()
        self._smoothed_tr = self._smoothed_plus = self._smoothed_minus = None
        self._dx_seed.clear(); self._adx = None
        self._reset_base()

    def _directional_values(self) -> tuple[float, float, float]:
        tr = self._smoothed_tr or 0.0
        plus_di = 100.0 * (self._smoothed_plus or 0.0) / max(tr, _EPS)
        minus_di = 100.0 * (self._smoothed_minus or 0.0) / max(tr, _EPS)
        dx = 100.0 * abs(plus_di - minus_di) / max(plus_di + minus_di, _EPS)
        return plus_di, minus_di, dx

    def update(self, event: Any) -> FeatureUpdate:
        self._event_count += 1
        ts_ns = _ts_ns(event, self._spec.trigger.time_semantics)
        high, low, close = (_bar_field(event, field) for field in ("high", "low", "close"))
        if None in (high, low, close):
            return self._no_change()
        if self._previous_close is None:
            tr, plus_dm, minus_dm = high - low, 0.0, 0.0
        else:
            tr = max(high - low, abs(high - self._previous_close), abs(low - self._previous_close))
            up_move = high - (self._previous_high or high)
            down_move = (self._previous_low or low) - low
            plus_dm = up_move if up_move > down_move and up_move > 0.0 else 0.0
            minus_dm = down_move if down_move > up_move and down_move > 0.0 else 0.0
        self._previous_high, self._previous_low, self._previous_close = high, low, close
        if self._smoothed_tr is None:
            self._seed_tr.append(tr); self._seed_plus.append(plus_dm); self._seed_minus.append(minus_dm)
            if len(self._seed_tr) == self._window:
                self._smoothed_tr = sum(self._seed_tr)
                self._smoothed_plus = sum(self._seed_plus)
                self._smoothed_minus = sum(self._seed_minus)
        else:
            self._smoothed_tr = self._smoothed_tr - self._smoothed_tr / self._window + tr
            self._smoothed_plus = (self._smoothed_plus or 0.0) - (self._smoothed_plus or 0.0) / self._window + plus_dm
            self._smoothed_minus = (self._smoothed_minus or 0.0) - (self._smoothed_minus or 0.0) / self._window + minus_dm
        if self._smoothed_tr is not None:
            plus_di, minus_di, dx = self._directional_values()
            if self._adx is None:
                self._dx_seed.append(dx)
                if len(self._dx_seed) == self._window:
                    self._adx = sum(self._dx_seed) / self._window
            else:
                self._adx = ((self._window - 1) * self._adx + dx) / self._window
        triggered = self._should_trigger(ts_ns)
        if triggered:
            self._last_trigger_ts = ts_ns
        if not self.is_ready:
            return self._emit(None, False, triggered, source_event_time_ns=ts_ns, update_status="not_ready")
        plus_di, minus_di, _ = self._directional_values()
        value = self._adx if self._output == "adx" else plus_di if self._output == "plus_di" else minus_di
        return self._emit(value, True, triggered, source_event_time_ns=ts_ns, update_status="updated")

    def state_dict(self) -> dict:
        return {
            **self._base_state(), "previous_high": self._previous_high,
            "previous_low": self._previous_low, "previous_close": self._previous_close,
            "seed_tr": self._seed_tr, "seed_plus": self._seed_plus,
            "seed_minus": self._seed_minus, "smoothed_tr": self._smoothed_tr,
            "smoothed_plus": self._smoothed_plus, "smoothed_minus": self._smoothed_minus,
            "dx_seed": self._dx_seed, "adx": self._adx,
        }

    def load_state_dict(self, state: dict) -> None:
        self._load_base(state)
        self._previous_high = state.get("previous_high")
        self._previous_low = state.get("previous_low")
        self._previous_close = state.get("previous_close")
        self._seed_tr = list(state.get("seed_tr", []))
        self._seed_plus = list(state.get("seed_plus", []))
        self._seed_minus = list(state.get("seed_minus", []))
        self._smoothed_tr = state.get("smoothed_tr")
        self._smoothed_plus = state.get("smoothed_plus")
        self._smoothed_minus = state.get("smoothed_minus")
        self._dx_seed = list(state.get("dx_seed", []))
        self._adx = state.get("adx")
