"""VWM short signal engine (self-contained, Mode A).

Ported from ``nautilus_ext.strategies.vwm_short_signals`` /
``vwm_short_components``. The Mode-B (feature-externalised) branch and the
``StrategyInputSchema`` dependency are dropped; the engine computes its VWM
features internally (Mode A — the original design). Decision logic is
unchanged, preserving TradeBlazer ``[1]`` semantics:

* entry trigger uses the previous ``SEPrice`` and previous ``ATR``;
* the setup-window check uses the previous ``SSetup``;
* the exit uses the previous ``BullSetup``.
"""
from __future__ import annotations

from dataclasses import dataclass

from strategies.vwm_short.signal_types import BarInput, SignalResult
from strategies.vwm_short.vwm_features import VwmFeatureConfig, VwmFeatureEngine


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
    prev_se_price: float | None
    prev_s_setup: int
    prev_bull_setup: bool


class VolumeWeightedMomentumShortSignalEngine:
    name = "vwm_short"

    def __init__(self, config: VwmShortSignalConfig) -> None:
        self.config = config
        self.features = VwmFeatureEngine(
            VwmFeatureConfig(
                mom_len=config.mom_len,
                avg_len=config.avg_len,
                atr_len=config.atr_len,
            ),
        )
        self.prev_bull_setup = False
        self.se_price: float | None = None
        self.s_setup = 0
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
        if not external_position and self.position == -1:
            active_bars_since_entry += 1

        snapshot = self._snapshot()
        features = self.features.update(bar)

        curr_se_price, curr_s_setup = self._update_setup(bar, features.bear_setup, snapshot)

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
            and snapshot.prev_se_price is not None
        )
        if warmed_up:
            entry_trigger_price = snapshot.prev_se_price - (
                self.config.atr_pcnt * features.prev_atr
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

        self.prev_bull_setup = features.bull_setup
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
                "current_bar": features.current_bar,
                "momentum": features.momentum,
                "vwm": features.vwm,
                "atr": features.atr,
                "bull_setup": features.bull_setup,
                "bear_setup": features.bear_setup,
                "se_price": curr_se_price,
                "s_setup": curr_s_setup,
                "entry_signal": entry_signal,
                "exit_signal": exit_signal,
                "entry_setup_active": entry_setup_active,
                "entry_trigger_price": entry_trigger_price,
            },
        )

    def _snapshot(self) -> VwmShortSnapshot:
        return VwmShortSnapshot(
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
