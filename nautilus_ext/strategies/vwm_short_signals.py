from __future__ import annotations
from dataclasses import dataclass
from math import fsum
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
class VwmShortBarInput:
    open: float
    high: float
    low: float
    close: float
    volume: float
@dataclass(frozen=True)
class VwmShortSignalResult:
    current_bar: int
    momentum: float | None
    vwm: float | None
    atr: float | None
    bull_setup: bool
    bear_setup: bool
    se_price: float | None
    s_setup: int
    entry_signal: bool
    exit_signal: bool
    entry_setup_active: bool
    entry_trigger_price: float | None
    cancel_entry: bool
    reason: str | None = None
class VolumeWeightedMomentumShortSignalEngine:
    def __init__(self, config: VwmShortSignalConfig) -> None:
        self.config = config
        self._alpha = 2.0 / (config.avg_len + 1.0)
        self._closes: list[float] = []
        self._true_ranges: list[float] = []

        self.current_bar = 0
        self.prev_close: float | None = None
        self.prev_vwm: float | None = None
        self.prev_atr: float | None = None
        self.prev_se_price: float | None = None
        self.prev_s_setup = 0
        self.prev_bull_setup = False

        self.vwm: float | None = None
        self.atr: float | None = None
        self.se_price: float | None = None
        self.s_setup = 0

        self.position = 0
        self.bars_since_entry = 0

    def reset(self) -> None:
        self.__init__(self.config)

    def update(
        self,
        bar: VwmShortBarInput,
        position: int | None = None,
        bars_since_entry: int | None = None,
    ) -> VwmShortSignalResult:
        self._validate_bar(bar)

        external_position = position is not None
        active_position = self.position if position is None else position
        active_bars_since_entry = (
            self.bars_since_entry if bars_since_entry is None else bars_since_entry
        )
        if not external_position and self.position == -1:
            active_bars_since_entry += 1

        prev_vwm = self.vwm
        prev_atr = self.atr
        prev_se_price = self.se_price
        prev_s_setup = self.s_setup
        prev_bull_setup = self.prev_bull_setup

        self.current_bar += 1
        true_range = self._true_range(bar)
        self._true_ranges.append(true_range)
        self._closes.append(bar.close)

        momentum = self._momentum()
        curr_vwm = self._update_vwm(bar.volume, momentum)
        curr_atr = self._update_atr()

        bull_setup = prev_vwm is not None and curr_vwm is not None and prev_vwm <= 0 < curr_vwm
        bear_setup = prev_vwm is not None and curr_vwm is not None and prev_vwm >= 0 > curr_vwm

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

        self.prev_close = bar.close
        self.prev_vwm = prev_vwm
        self.prev_atr = prev_atr
        self.prev_se_price = prev_se_price
        self.prev_s_setup = prev_s_setup
        self.prev_bull_setup = bull_setup
        self.vwm = curr_vwm
        self.atr = curr_atr
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

        return VwmShortSignalResult(
            current_bar=self.current_bar,
            momentum=momentum,
            vwm=curr_vwm,
            atr=curr_atr,
            bull_setup=bull_setup,
            bear_setup=bear_setup,
            se_price=curr_se_price,
            s_setup=curr_s_setup,
            entry_signal=entry_signal,
            exit_signal=exit_signal,
            entry_setup_active=entry_setup_active,
            entry_trigger_price=entry_trigger_price,
            cancel_entry=cancel_entry,
            reason=reason,
        )

    def _momentum(self) -> float | None:
        if len(self._closes) <= self.config.mom_len:
            return None
        return self._closes[-1] - self._closes[-1 - self.config.mom_len]

    def _update_vwm(self, volume: float, momentum: float | None) -> float | None:
        if momentum is None:
            return self.vwm
        raw_vwm = volume * momentum
        if self.vwm is None:
            return raw_vwm
        return self.vwm + self._alpha * (raw_vwm - self.vwm)

    def _update_atr(self) -> float | None:
        if len(self._true_ranges) < self.config.atr_len:
            return None
        window = self._true_ranges[-self.config.atr_len :]
        return fsum(window) / self.config.atr_len

    def _true_range(self, bar: VwmShortBarInput) -> float:
        if self.prev_close is None:
            return bar.high - bar.low
        return max(
            bar.high - bar.low,
            abs(bar.high - self.prev_close),
            abs(bar.low - self.prev_close),
        )

    @staticmethod
    def _validate_bar(bar: VwmShortBarInput) -> None:
        if bar.high < bar.low:
            raise ValueError("bar.high must be >= bar.low.")
        if bar.volume < 0:
            raise ValueError("bar.volume must be >= 0.")