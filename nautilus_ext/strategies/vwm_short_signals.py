from __future__ import annotations

from feature_engine.vwm_features import VwmFeatureConfig
from feature_engine.vwm_features import VwmFeatureEngine
from nautilus_ext.strategies.interfaces.strategy_schema import StrategyInputSchema
from nautilus_ext.strategies.signal_types import BarInput
from nautilus_ext.strategies.signal_types import SignalResult
from nautilus_ext.strategies.vwm_short_components import VwmShortSignalConfig
from nautilus_ext.strategies.vwm_short_components import VwmShortSnapshot


VwmShortBarInput = BarInput
VwmShortSignalResult = SignalResult

_VWM_FEATURE_SET_ID = "vwm_features_v1"


class _VwmFeaturesFromContext:
    """Read-only attribute adapter: wraps a FeatureEvent.values dict as a snapshot.

    Used in Mode B when the FeaturePipeline provides pre-computed VWM features
    via StrategyRuntimeContext.  Mirrors the attribute surface of VwmFeatureSnapshot
    so the signal logic below needs no branching.
    """
    __slots__ = (
        "current_bar", "momentum", "vwm", "atr",
        "prev_vwm", "prev_atr", "bull_setup", "bear_setup",
    )

    def __init__(self, d: dict) -> None:
        self.current_bar = int(d.get("current_bar", 0))
        self.momentum = d.get("momentum")
        self.vwm = d.get("vwm")
        self.atr = d.get("atr")
        self.prev_vwm = d.get("prev_vwm")
        self.prev_atr = d.get("prev_atr")
        self.bull_setup = bool(d.get("bull_setup", False))
        self.bear_setup = bool(d.get("bear_setup", False))


class VolumeWeightedMomentumShortSignalEngine:
    name = "vwm_short"
    input_schema = StrategyInputSchema(
        input_types=["bar"],
        symbols=[],
        timeframes=None,
        warmup={"bars": 200},
        requires_position=True,
        requires_portfolio=False,
        multi_asset=False,
        multi_timeframe=False,
    )

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
        context: dict | None = None,
        position: int | None = None,
        bars_since_entry: int | None = None,
    ) -> SignalResult:
        if context is not None:
            position = context.get("position", position)
            bars_since_entry = context.get("bars_since_entry", bars_since_entry)
        self._validate_bar(bar)

        external_position = position is not None
        active_position = self.position if position is None else position
        active_bars_since_entry = (
            self.bars_since_entry if bars_since_entry is None else bars_since_entry
        )
        if not external_position and self.position == -1:
            active_bars_since_entry += 1

        snapshot = self._snapshot()

        # Mode B: use pre-computed external features from FeaturePipeline if available.
        # Falls back to Mode A (internal VwmFeatureEngine) when context is absent or
        # does not carry the expected feature set.
        ext_vals = None
        if context is not None and hasattr(context, "get_feature_values"):
            ext_vals = context.get_feature_values(_VWM_FEATURE_SET_ID)

        if ext_vals is not None:
            features = _VwmFeaturesFromContext(ext_vals)
        else:
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
