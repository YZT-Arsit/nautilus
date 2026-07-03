"""VWM long signal engine (self-contained, Mode A).

Long-side mirror of ``strategies/vwm_short/signals.py``. Ported from the
TradeBlazer ``VolumeWeightedMomentumSys_L`` system. The engine computes its VWM
features internally (Mode A). Decision logic preserves TradeBlazer ``[1]``
semantics:

* entry trigger uses the previous ``LEPrice`` and previous ``ATR``;
* the setup-window check uses the previous ``LSetup``;
* the exit uses the previous ``BearSetup``.
"""
from __future__ import annotations

from dataclasses import dataclass

from strategies.vwm_long.signal_types import BarInput, SignalResult
from strategies.vwm_long.vwm_features import VwmFeatureConfig, VwmFeatureEngine


@dataclass(frozen=True)
class VwmLongSignalConfig:
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
class VwmLongSnapshot:
    prev_le_price: float | None
    prev_l_setup: int
    prev_bear_setup: bool


class VolumeWeightedMomentumLongSignalEngine:
    name = "vwm_long"

    def __init__(self, config: VwmLongSignalConfig) -> None:
        self.config = config
        self.features = VwmFeatureEngine(
            VwmFeatureConfig(
                mom_len=config.mom_len,
                avg_len=config.avg_len,
                atr_len=config.atr_len,
            ),
        )
        self.prev_bear_setup = False
        self.le_price: float | None = None
        self.l_setup = 0
        self.position = 0
        self.bars_since_entry = 0

    def reset(self) -> None:
        self.__init__(self.config)

    def warmup(self, events) -> None:
        for event in events:
            self.update(event)

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
        if not external_position and self.position == 1:
            active_bars_since_entry += 1

        snapshot = self._snapshot()
        features = self.features.update(bar)

        curr_le_price, curr_l_setup = self._update_setup(bar, features.bull_setup, snapshot)

        entry_trigger_price = None
        entry_signal = False
        exit_signal = False
        entry_setup_active = False
        cancel_entry = False
        reason = None

        warmed_up = (
            features.current_bar > self.config.avg_len
            and features.momentum is not None
            and features.prev_atr is not None
            and snapshot.prev_le_price is not None
        )
        if warmed_up:
            entry_trigger_price = snapshot.prev_le_price + (
                self.config.atr_pcnt * features.prev_atr
            )
            entry_setup_active = (
                active_position == 0
                and bar.volume > 0
                and snapshot.prev_l_setup <= self.config.setup_len
                and curr_l_setup >= 1
            )
            entry_signal = entry_setup_active and bar.high >= entry_trigger_price
            if entry_signal:
                reason = "enter_long"
            elif snapshot.prev_l_setup > self.config.setup_len:
                cancel_entry = True

        exit_signal = (
            active_position == 1
            and active_bars_since_entry > 0
            and bar.volume > 0
            and snapshot.prev_bear_setup
        )
        if exit_signal:
            reason = "exit_long"

        self.prev_bear_setup = features.bear_setup
        self.le_price = curr_le_price
        self.l_setup = curr_l_setup

        self._update_internal_position(
            external_position,
            active_bars_since_entry,
            entry_signal,
            exit_signal,
        )

        return SignalResult(
            entry_side="BUY" if entry_setup_active else None,
            entry_order_type="stop_market" if entry_setup_active else None,
            entry_price=entry_trigger_price if entry_setup_active else None,
            exit_side="SELL" if exit_signal else None,
            cancel_entry=cancel_entry,
            reason=reason,
            debug={
                "current_bar": features.current_bar,
                "momentum": features.momentum,
                "vwm": features.vwm,
                "atr": features.atr,
                "bull_setup": features.bull_setup,
                "bear_setup": features.bear_setup,
                "le_price": curr_le_price,
                "l_setup": curr_l_setup,
                "entry_signal": entry_signal,
                "exit_signal": exit_signal,
                "entry_setup_active": entry_setup_active,
                "entry_trigger_price": entry_trigger_price,
            },
        )

    def _snapshot(self) -> VwmLongSnapshot:
        return VwmLongSnapshot(
            prev_le_price=self.le_price,
            prev_l_setup=self.l_setup,
            prev_bear_setup=self.prev_bear_setup,
        )

    @staticmethod
    def _update_setup(
        bar: BarInput,
        bull_setup: bool,
        snapshot: VwmLongSnapshot,
    ) -> tuple[float | None, int]:
        if bull_setup:
            return bar.close, 0
        return snapshot.prev_le_price, snapshot.prev_l_setup + 1

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
            self.position = 1
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
