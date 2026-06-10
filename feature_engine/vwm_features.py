from __future__ import annotations

import dataclasses
from collections import deque
from dataclasses import asdict
from dataclasses import dataclass

from feature_engine.nautilus_indicators import AtrFeature
from feature_engine.nautilus_indicators import EmaFeature
from feature_engine.tradeblazer_features import RawMomentumFeature
from feature_engine.tradeblazer_features import cross_over
from feature_engine.tradeblazer_features import cross_under
from nautilus_ext.strategies.signal_types import BarInput


@dataclass(frozen=True)
class VwmFeatureConfig:
    mom_len: int = 5
    avg_len: int = 20
    atr_len: int = 5

    def __post_init__(self) -> None:
        if self.mom_len <= 0:
            raise ValueError("mom_len must be > 0.")
        if self.avg_len <= 0:
            raise ValueError("avg_len must be > 0.")
        if self.atr_len <= 0:
            raise ValueError("atr_len must be > 0.")


@dataclass(frozen=True)
class VwmFeatureSnapshot:
    current_bar: int
    momentum: float | None
    vwm: float | None
    atr: float | None
    prev_vwm: float | None
    prev_atr: float | None
    bull_setup: bool
    bear_setup: bool


class VwmFeatureEngine:
    """Streaming VWM/ATR features suitable for historical warmup and live bars.

    Checkpoints contain replayable bars so the public Nautilus indicator API is
    used to reconstruct EMA/ATR state exactly after restart. This is exact and
    intentionally conservative; compact native-indicator checkpoints can be
    added later if Nautilus exposes a public restoration API.
    """

    def __init__(self, config: VwmFeatureConfig) -> None:
        self.config = config
        self.momentum = RawMomentumFeature(config.mom_len)
        self.vwm = EmaFeature(config.avg_len)
        self.atr = AtrFeature(config.atr_len)
        self.current_bar = 0
        self._processed_bars: list[dict[str, float]] = []
        self._true_ranges: deque[float] = deque(maxlen=config.atr_len)
        self._previous_close: float | None = None
        self._latest_snapshot: VwmFeatureSnapshot | None = None
        self.last_ts_event: str | None = None

    def reset(self) -> None:
        self.momentum.reset()
        self.vwm.reset()
        self.atr.reset()
        self.current_bar = 0
        self._processed_bars.clear()
        self._true_ranges.clear()
        self._previous_close = None
        self._latest_snapshot = None
        self.last_ts_event = None

    def update(self, bar: BarInput) -> VwmFeatureSnapshot:
        prev_vwm = self.vwm.value
        prev_atr = self.atr.value
        self.current_bar += 1

        self._processed_bars.append(asdict(bar))
        self._true_ranges.append(self._true_range(bar))
        self._previous_close = bar.close
        momentum = self.momentum.update(bar.close)
        atr = self.atr.update(bar)
        if momentum is not None:
            self.vwm.update_raw(bar.volume * momentum)
        vwm = self.vwm.value

        self._latest_snapshot = VwmFeatureSnapshot(
            current_bar=self.current_bar,
            momentum=momentum,
            vwm=vwm,
            atr=atr,
            prev_vwm=prev_vwm,
            prev_atr=prev_atr,
            bull_setup=cross_over(prev_vwm, vwm),
            bear_setup=cross_under(prev_vwm, vwm),
        )
        return self._latest_snapshot

    def state_dict(self) -> dict:
        latest = asdict(self._latest_snapshot) if self._latest_snapshot is not None else None
        return {
            "version": 1,
            "config": asdict(self.config),
            "current_bar": self.current_bar,
            "processed_bars": list(self._processed_bars),
            "momentum_state": self.momentum.state_dict(),
            "current_momentum": latest["momentum"] if latest else None,
            "current_vwm": latest["vwm"] if latest else None,
            "previous_vwm": latest["prev_vwm"] if latest else None,
            "current_atr": latest["atr"] if latest else None,
            "previous_atr": latest["prev_atr"] if latest else None,
            "atr_true_range_window": list(self._true_ranges),
            "atr_previous_close": self._previous_close,
            "bull_setup": latest["bull_setup"] if latest else False,
            "bear_setup": latest["bear_setup"] if latest else False,
            "last_ts_event": self.last_ts_event,
        }

    def load_state_dict(self, state: dict) -> None:
        if state.get("config") != asdict(self.config):
            raise ValueError("VwmFeatureEngine config does not match checkpoint.")
        bars = state.get("processed_bars")
        if bars is None:
            raise ValueError("VwmFeatureEngine checkpoint is missing processed_bars.")
        self.reset()
        bar_fields = {f.name for f in dataclasses.fields(BarInput)}
        for values in bars:
            self.update(BarInput(**{k: v for k, v in values.items() if k in bar_fields}))
        self.last_ts_event = state.get("last_ts_event")
        if self.current_bar != int(state.get("current_bar", -1)):
            raise ValueError("VwmFeatureEngine checkpoint replay produced an invalid bar count.")

    def set_last_ts_event(self, ts_event) -> None:
        self.last_ts_event = ts_event.isoformat() if ts_event is not None else None

    def _true_range(self, bar: BarInput) -> float:
        if self._previous_close is None:
            return bar.high - bar.low
        return max(
            bar.high - bar.low,
            abs(bar.high - self._previous_close),
            abs(bar.low - self._previous_close),
        )
