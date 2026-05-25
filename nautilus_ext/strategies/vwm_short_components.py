from __future__ import annotations

from dataclasses import dataclass

from nautilus_trader.indicators import AverageTrueRange
from nautilus_trader.indicators import ExponentialMovingAverage

from nautilus_ext.strategies.signal_types import BarInput
from nautilus_ext.strategies.tradeblazer_helpers import MomentumState


@dataclass(frozen=True)
class VwmShortSignalConfig:
    mom_len: int = 5
    avg_len: int = 20
    atr_len: int = 5
    atr_pcnt: float = 0.5
    setup_len: int = 5

    def __post_init__(self) -> None:
        if self.mom_len <= 0:
            raise ValueError("mom_len must be > 0.")
        if self.avg_len <= 0:
            raise ValueError("avg_len must be > 0.")
        if self.atr_len <= 0:
            raise ValueError("atr_len must be > 0.")
        if self.atr_pcnt < 0:
            raise ValueError("atr_pcnt must be >= 0.")
        if self.setup_len < 1:
            raise ValueError("setup_len must be >= 1.")


@dataclass(frozen=True)
class VwmShortSnapshot:
    prev_vwm: float | None
    prev_atr: float | None
    prev_se_price: float | None
    prev_s_setup: int
    prev_bull_setup: bool


@dataclass(frozen=True)
class VwmShortIndicatorValues:
    momentum: float | None
    vwm: float | None
    atr: float | None


class VwmShortIndicators:
    def __init__(self, config: VwmShortSignalConfig) -> None:
        self.momentum = MomentumState(config.mom_len)
        self.vwm_ema = ExponentialMovingAverage(config.avg_len)
        self.atr = AverageTrueRange(config.atr_len)

    def snapshot_values(self) -> tuple[float | None, float | None]:
        prev_vwm = self.vwm_ema.value if self.vwm_ema.has_inputs else None
        prev_atr = self.atr.value if self.atr.initialized else None
        return prev_vwm, prev_atr

    def update(self, bar: BarInput) -> VwmShortIndicatorValues:
        momentum = self.momentum.update(bar.close)
        self.atr.update_raw(bar.high, bar.low, bar.close)
        curr_atr = self.atr.value if self.atr.initialized else None

        if momentum is not None:
            self.vwm_ema.update_raw(bar.volume * momentum)
        curr_vwm = self.vwm_ema.value if self.vwm_ema.has_inputs else None

        return VwmShortIndicatorValues(
            momentum=momentum,
            vwm=curr_vwm,
            atr=curr_atr,
        )
