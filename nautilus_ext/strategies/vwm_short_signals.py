from __future__ import annotations

from nautilus_ext.strategies.signal_types import BarInput
from nautilus_ext.strategies.signal_types import SignalResult
from nautilus_ext.strategies.tradeblazer_helpers import cross_over
from nautilus_ext.strategies.tradeblazer_helpers import cross_under
from nautilus_ext.strategies.vwm_short_components import VwmShortIndicators
from nautilus_ext.strategies.vwm_short_components import VwmShortSignalConfig
from nautilus_ext.strategies.vwm_short_components import VwmShortSnapshot


VwmShortBarInput = BarInput
VwmShortSignalResult = SignalResult


class VolumeWeightedMomentumShortSignalEngine:
    def __init__(self, config: VwmShortSignalConfig) -> None:
        self.config = config
        self.indicators = VwmShortIndicators(config)
        self.current_bar = 0
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

        snapshot = self._snapshot()
        self.current_bar += 1
        values = self.indicators.update(bar)
        bull_setup = cross_over(snapshot.prev_vwm, values.vwm)
        bear_setup = cross_under(snapshot.prev_vwm, values.vwm)
        curr_se_price, curr_s_setup = self._update_setup(bar, bear_setup, snapshot)

        entry_trigger_price = None
        entry_signal = False
        exit_signal = False
        entry_setup_active = False
        cancel_entry = False
        reason = None

        warmed_up = (
            self.current_bar > self.config.avg_len
            and values.momentum is not None
            and snapshot.prev_atr is not None
            and snapshot.prev_se_price is not None
        )
        if warmed_up:
            entry_trigger_price = snapshot.prev_se_price - (
                self.config.atr_pcnt * snapshot.prev_atr
            )
            entry_setup_active = (
                active_position == 0
                and bar.volume > 0
                and snapshot.prev_s_setup <= self.config.setup_len
                and curr_s_setup >= 1
            )
            entry_signal = entry_setup_active and bar.low <= entry_trigger_price
            if entry_signal:
                reason = "enter_short"
            elif snapshot.prev_s_setup > self.config.setup_len:
                cancel_entry = True

        exit_signal = (
            active_position == -1
            and active_bars_since_entry > 0
            and bar.volume > 0
            and snapshot.prev_bull_setup
        )
        if exit_signal:
            reason = "exit_short"

        self.prev_bull_setup = bull_setup
        self.se_price = curr_se_price
        self.s_setup = curr_s_setup

        self._update_internal_position(
            external_position,
            active_bars_since_entry,
            entry_signal,
            exit_signal,
        )

        return SignalResult(
            entry_side="SELL" if entry_setup_active else None,
            entry_order_type="stop_market" if entry_setup_active else None,
            entry_price=entry_trigger_price if entry_setup_active else None,
            exit_side="BUY" if exit_signal else None,
            cancel_entry=cancel_entry,
            reason=reason,
            debug={
                "current_bar": self.current_bar,
                "momentum": values.momentum,
                "vwm": values.vwm,
                "atr": values.atr,
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

    def _snapshot(self) -> VwmShortSnapshot:
        prev_vwm, prev_atr = self.indicators.snapshot_values()
        return VwmShortSnapshot(
            prev_vwm=prev_vwm,
            prev_atr=prev_atr,
            prev_se_price=self.se_price,
            prev_s_setup=self.s_setup,
            prev_bull_setup=self.prev_bull_setup,
        )

    @staticmethod
    def _update_setup(
        bar: BarInput,
        bear_setup: bool,
        snapshot: VwmShortSnapshot,
    ) -> tuple[float | None, int]:
        if bear_setup:
            return bar.close, 0
        return snapshot.prev_se_price, snapshot.prev_s_setup + 1

    def _update_internal_position(
        self,
        external_position: bool,
        active_bars_since_entry: int,
        entry_signal: bool,
        exit_signal: bool,
    ) -> None:
        if external_position:
            return
        if entry_signal:
            self.position = -1
            self.bars_since_entry = 0
        elif exit_signal:
            self.position = 0
            self.bars_since_entry = 0
        else:
            self.bars_since_entry = active_bars_since_entry

    @staticmethod
    def _validate_bar(bar: BarInput) -> None:
        if bar.high < bar.low:
            raise ValueError("bar.high must be >= bar.low.")
        if bar.volume < 0:
            raise ValueError("bar.volume must be >= 0.")
