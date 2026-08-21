"""Moving Average Convergence Divergence feature."""
from __future__ import annotations

from typing import Any

from feature_engine.compute.feature_lib.base import (
    _AbstractFeature, _bar_field, _ts_ns, FeatureUpdate, WarmupRequirement,
)
from feature_engine.compute.spec import FeatureSpec


class _SeededEma:
    def __init__(self, window: int) -> None:
        self.window = window
        self.alpha = 2.0 / (window + 1.0)
        self.seed: list[float] = []
        self.value: float | None = None

    def push(self, value: float) -> float | None:
        if self.value is None:
            self.seed.append(value)
            if len(self.seed) == self.window:
                self.value = sum(self.seed) / self.window
        else:
            self.value += self.alpha * (value - self.value)
        return self.value

    def reset(self) -> None:
        self.seed.clear(); self.value = None

    def state_dict(self) -> dict:
        return {"seed": self.seed, "value": self.value}

    def load_state_dict(self, state: dict) -> None:
        self.seed = list(state.get("seed", [])); self.value = state.get("value")


class MovingAverageConvergenceDivergenceFeature(_AbstractFeature):
    """MACD DIF, DEA/signal or histogram selected by ``params['output']``."""

    def __init__(self, spec: FeatureSpec) -> None:
        super().__init__(spec)
        self._fast_window = int(spec.params.get("fast_window", 12))
        self._slow_window = int(spec.params.get("slow_window", 26))
        self._signal_window = int(spec.params.get("signal_window", 9))
        self._output = str(spec.params.get("output", "dif"))
        if not 0 < self._fast_window < self._slow_window or self._signal_window <= 0:
            raise ValueError("MACD requires 0 < fast < slow and positive signal window")
        if self._output not in {"dif", "signal", "histogram"}:
            raise ValueError(f"unsupported MACD output: {self._output}")
        self._fast = _SeededEma(self._fast_window)
        self._slow = _SeededEma(self._slow_window)
        self._signal = _SeededEma(self._signal_window)
        self._dif: float | None = None

    def warmup_required(self) -> WarmupRequirement:
        n = self._slow_window if self._output == "dif" else self._slow_window + self._signal_window - 1
        return WarmupRequirement(n_events=n, unit="bars")

    @property
    def is_ready(self) -> bool:
        return self._dif is not None if self._output == "dif" else self._signal.value is not None

    def reset(self) -> None:
        self._fast.reset(); self._slow.reset(); self._signal.reset(); self._dif = None; self._reset_base()

    def update(self, event: Any) -> FeatureUpdate:
        self._event_count += 1
        ts_ns = _ts_ns(event, self._spec.trigger.time_semantics)
        price = _bar_field(event, self._spec.input_field or "close")
        if price is None:
            return self._no_change()
        fast, slow = self._fast.push(price), self._slow.push(price)
        if fast is not None and slow is not None:
            self._dif = fast - slow
            signal = self._signal.push(self._dif)
        else:
            signal = None
        triggered = self._should_trigger(ts_ns)
        if triggered:
            self._last_trigger_ts = ts_ns
        if not self.is_ready:
            return self._emit(None, False, triggered, source_event_time_ns=ts_ns, update_status="not_ready")
        value = self._dif if self._output == "dif" else signal if self._output == "signal" else 2.0 * (self._dif - signal)
        return self._emit(value, True, triggered, source_event_time_ns=ts_ns, update_status="updated")

    def state_dict(self) -> dict:
        return {**self._base_state(), "fast": self._fast.state_dict(), "slow": self._slow.state_dict(),
                "signal": self._signal.state_dict(), "dif": self._dif}

    def load_state_dict(self, state: dict) -> None:
        self._load_base(state); self._fast.load_state_dict(state["fast"])
        self._slow.load_state_dict(state["slow"]); self._signal.load_state_dict(state["signal"])
        self._dif = state.get("dif")

