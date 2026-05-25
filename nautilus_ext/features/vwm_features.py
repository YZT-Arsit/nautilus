from __future__ import annotations

from dataclasses import dataclass

from nautilus_ext.features.nautilus_indicators import AtrFeature
from nautilus_ext.features.nautilus_indicators import EmaFeature
from nautilus_ext.features.tradeblazer_features import RawMomentumFeature
from nautilus_ext.features.tradeblazer_features import cross_over
from nautilus_ext.features.tradeblazer_features import cross_under
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
    """Streaming VWM/ATR features suitable for historical warmup and live bars."""

    def __init__(self, config: VwmFeatureConfig) -> None:
        self.config = config
        self.momentum = RawMomentumFeature(config.mom_len)
        self.vwm = EmaFeature(config.avg_len)
        self.atr = AtrFeature(config.atr_len)
        self.current_bar = 0

    def reset(self) -> None:
        self.momentum.reset()
        self.vwm.reset()
        self.atr.reset()
        self.current_bar = 0

    def update(self, bar: BarInput) -> VwmFeatureSnapshot:
        prev_vwm = self.vwm.value
        prev_atr = self.atr.value
        self.current_bar += 1

        momentum = self.momentum.update(bar.close)
        atr = self.atr.update(bar)
        if momentum is not None:
            self.vwm.update_raw(bar.volume * momentum)
        vwm = self.vwm.value

        return VwmFeatureSnapshot(
            current_bar=self.current_bar,
            momentum=momentum,
            vwm=vwm,
            atr=atr,
            prev_vwm=prev_vwm,
            prev_atr=prev_atr,
            bull_setup=cross_over(prev_vwm, vwm),
            bear_setup=cross_under(prev_vwm, vwm),
        )
