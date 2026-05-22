from __future__ import annotations
from dataclasses import dataclass
from nautilus_trader.indicators import AverageTrueRange
from nautilus_trader.indicators import ExponentialMovingAverage
from nautilus_ext.strategies.signal_types import BarInput
from nautilus_ext.strategies.signal_types import SignalResult
from nautilus_ext.strategies.tradeblazer_helpers import MomentumState
from nautilus_ext.strategies.tradeblazer_helpers import cross_over
from nautilus_ext.strategies.tradeblazer_helpers import cross_under

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
VwmShortBarInput = BarInput
VwmShortSignalResult = SignalResult

class VolumeWeightedMomentumShortSignalEngine:
    def __init__(self, config: VwmShortSignalConfig) -> None:
        self.config = config
        self.momentum = MomentumState(config.mom_len)
        self.vwm_ema = ExponentialMovingAverage(config.avg_len)
        self.atr = AverageTrueRange(config.atr_len)

        self.current_bar = 0
        self.prev_vwm: float | None = None
        self.prev_atr: float | None = None
        self.prev_se_price: float | None = None
        self.prev_s_setup = 0
        self.prev_bull_setup = False

        self.se_price: float | None = None
        self.s_setup = 0

        self.position = 0
        self.bars_since_entry = 0

    def reset(self) -> None:
        self.__init__(self.config)

    def update(
        self,
        bar: BarInput,
        position: int | None = None,
        bars_since_entry: int | None = None,
    ) -> SignalResult:
        self._validate_bar(bar)

        external_position = position is not None
        active_position = self.position if position is None else position
        active_bars_since_entry = (
            self.bars_since_entry if bars_since_entry is None else bars_since_entry
        )
        if not external_position and self.position == -1:
            active_bars_since_entry += 1

        prev_vwm = self.vwm_ema.value if self.vwm_ema.has_inputs else None
        prev_atr = self.atr.value if self.atr.initialized else None
        prev_se_price = self.se_price
        prev_s_setup = self.s_setup
        prev_bull_setup = self.prev_bull_setup

        self.current_bar += 1

        momentum = self.momentum.update(bar.close)
        self.atr.update_raw(bar.high, bar.low, bar.close)
        curr_atr = self.atr.value if self.atr.initialized else None
        if momentum is not None:
            self.vwm_ema.update_raw(bar.volume * momentum)
        curr_vwm = self.vwm_ema.value if self.vwm_ema.has_inputs else None

        bull_setup = cross_over(prev_vwm, curr_vwm, 0.0)
        bear_setup = cross_under(prev_vwm, curr_vwm, 0.0)

        if bear_setup:
            curr_se_price = bar.close
            curr_s_setup = 0
        else:
            curr_se_price = prev_se_price
            curr_s_setup = prev_s_setup + 1

        entry_trigger_price = None
        entry_signal = False
        exit_signal = False
        entry_setup_active = False
        cancel_entry = False
        reason = None

        warmed_up = (
            self.current_bar > self.config.avg_len
            and momentum is not None
            and prev_atr is not None
            and prev_se_price is not None
        )
        if warmed_up:
            entry_trigger_price = prev_se_price - (self.config.atr_pcnt * prev_atr)
            entry_setup_active = (
                active_position == 0
                and bar.volume > 0
                and prev_s_setup <= self.config.setup_len
                and curr_s_setup >= 1
            )
            entry_signal = entry_setup_active and bar.low <= entry_trigger_price
            if entry_signal:
                reason = "enter_short"
            elif prev_s_setup > self.config.setup_len:
                cancel_entry = True

        exit_signal = (
            active_position == -1
            and active_bars_since_entry > 0
            and bar.volume > 0
            and prev_bull_setup
        )
        if exit_signal:
            reason = "exit_short"

        self.prev_vwm = prev_vwm
        self.prev_atr = prev_atr
        self.prev_se_price = prev_se_price
        self.prev_s_setup = prev_s_setup
        self.prev_bull_setup = bull_setup
        self.se_price = curr_se_price
        self.s_setup = curr_s_setup

        if not external_position:
            if entry_signal:
                self.position = -1
                self.bars_since_entry = 0
            elif exit_signal:
                self.position = 0
                self.bars_since_entry = 0
            else:
                self.bars_since_entry = active_bars_since_entry

        return SignalResult(
            entry_side="SELL" if entry_setup_active else None,
            entry_order_type="stop_market" if entry_setup_active else None,
            entry_price=entry_trigger_price if entry_setup_active else None,
            exit_side="BUY" if exit_signal else None,
            cancel_entry=cancel_entry,
            reason=reason,
            debug={
                "current_bar": self.current_bar,
                "momentum": momentum,
                "vwm": curr_vwm,
                "atr": curr_atr,
                "bull_setup": bull_setup,
                "bear_setup": bear_setup,
                "se_price": curr_se_price,
                "s_setup": curr_s_setup,
                "entry_signal": entry_signal,
                "exit_signal": exit_signal,
                "entry_setup_active": entry_setup_active,
                "entry_trigger_price": entry_trigger_price,
            },
        )

    @staticmethod
    def _validate_bar(bar: BarInput) -> None:
        if bar.high < bar.low:
            raise ValueError("bar.high must be >= bar.low.")
        if bar.volume < 0:
            raise ValueError("bar.volume must be >= 0.")