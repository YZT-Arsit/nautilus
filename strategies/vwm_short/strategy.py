"""VWM short strategy plugin (Mode B, feature-externalised).

This adapts the existing ``VolumeWeightedMomentumShortSignalEngine`` (in
``nautilus_ext``) to the shared ``StrategyPlugin`` contract used by
``run_strategy.py``. It runs the engine in **Mode B**: VWM features are produced
by the registered ``vwm_features_v1`` feature engine, fed through a
``FeaturePipeline``, and handed to the signal engine via a
``StrategyRuntimeContext`` (the engine reads ``context.get_feature_values``).

Bridging notes
--------------
* ``run_strategy`` only hands the strategy a :class:`FeatureSnapshot`. To
  reconstruct the bar the engine needs, ``build_specs`` declares four identity
  passthrough specs (rolling mean over a window of 1 == the raw value) for
  ``close/high/low/volume``. This keeps the FeatureSpec / BackendRegistry /
  PythonBackend dispatch actively in the live chain; the VWM features
  themselves come from the Mode-B pipeline, not from these specs.
* The engine's entry is a ``stop_market`` at a trigger price. The native
  backend replays market orders, so we emit the short entry on the bar where
  the engine's trigger fires (``reason == "enter_short"``): a real Nautilus
  market fill at that bar.
* Signals are BUY / SELL / HOLD only; the signal->order decision stays in
  ``SignalToOrderPolicy`` (config ``sell_means: short``). SELL opens the short,
  BUY closes it.

This module imports no ``nautilus_trader`` symbols. The VWM feature engine it
uses internally wraps Nautilus indicators (a pre-existing dependency); those are
imported lazily when a strategy instance is built, not at module import.
"""
from __future__ import annotations

from dataclasses import dataclass

from feature_engine.api import FeatureSnapshot, FeatureSpec, rolling_mean_spec
from strategy_framework.plugin import StrategyPlugin

BUY, SELL, HOLD = "BUY", "SELL", "HOLD"

# Identity passthrough feature names (window=1 rolling mean == the raw field).
_CLOSE = "vwm_bar_close"
_HIGH = "vwm_bar_high"
_LOW = "vwm_bar_low"
_VOLUME = "vwm_bar_volume"

_VWM_FEATURE_SET_ID = "vwm_features_v1"


@dataclass(frozen=True)
class VwmShortConfig:
    """User-facing parameters for the VWM short strategy."""

    mom_len: int = 5
    avg_len: int = 20
    atr_len: int = 5
    atr_pcnt: float = 0.5
    setup_len: int = 5
    instrument_id: str = "BTCUSDT.BINANCE"
    bar_type: str | None = None


def build_specs(config: VwmShortConfig) -> list[FeatureSpec]:
    """Identity passthrough specs exposing OHLCV to ``on_snapshot``.

    A rolling mean over a window of 1 returns the field value unchanged and is
    ready from the first bar. These keep the spec-driven feature engine in the
    live chain; VWM math is done by the Mode-B ``vwm_features_v1`` pipeline.
    """
    passthrough = {"input_type": "bar", "window": 1}
    return [
        rolling_mean_spec(_CLOSE, input_field="close", **passthrough),
        rolling_mean_spec(_HIGH, input_field="high", **passthrough),
        rolling_mean_spec(_LOW, input_field="low", **passthrough),
        rolling_mean_spec(_VOLUME, input_field="volume", **passthrough),
    ]


class VwmShortStrategy:
    """Drive the VWM short signal engine (Mode B) from feature snapshots."""

    def __init__(self, config: VwmShortConfig) -> None:
        # Lazy imports: the VWM feature engine wraps Nautilus indicators, so
        # only touch it when a strategy is actually built (not at import time).
        from feature_engine.feature_pipeline import FeaturePipeline
        from feature_engine.vwm_adapter import VwmBarFeatureEngine
        from feature_engine.vwm_features import VwmFeatureConfig
        from nautilus_ext.strategies.vwm_short_components import VwmShortSignalConfig
        from nautilus_ext.strategies.vwm_short_signals import (
            VolumeWeightedMomentumShortSignalEngine,
        )

        self._config = config
        feature_config = VwmFeatureConfig(
            mom_len=config.mom_len,
            avg_len=config.avg_len,
            atr_len=config.atr_len,
        )
        self._pipeline = FeaturePipeline([VwmBarFeatureEngine(feature_config)])
        self._engine = VolumeWeightedMomentumShortSignalEngine(
            VwmShortSignalConfig(
                mom_len=config.mom_len,
                avg_len=config.avg_len,
                atr_len=config.atr_len,
                atr_pcnt=config.atr_pcnt,
                setup_len=config.setup_len,
            ),
        )
        # We drive position externally and pass it through the context so the
        # engine stays stateless about fills (Mode B with external position).
        self._position = 0
        self._bars_since_entry = 0

    def on_snapshot(self, snapshot: FeatureSnapshot) -> str:
        from feature_engine.interfaces import StrategyRuntimeContext
        from nautilus_ext.strategies.signal_types import BarInput

        close = snapshot.value(_CLOSE)
        high = snapshot.value(_HIGH)
        low = snapshot.value(_LOW)
        volume = snapshot.value(_VOLUME)
        # Identity specs are ready from bar 1; guard defensively all the same.
        if close is None or high is None or low is None or volume is None:
            return HOLD

        ts_ns = int(snapshot.ts_event or 0)
        bar = BarInput(
            open=close,
            high=high,
            low=low,
            close=close,
            volume=volume,
            ts_event=ts_ns // 1_000_000,  # ns -> ms (BarInput legacy field)
            event_time_ns=ts_ns,
            instrument_id=snapshot.instrument_id or self._config.instrument_id,
            bar_type=self._config.bar_type,
        )

        # Count bars since entry before the engine evaluates the exit rule.
        if self._position == -1:
            self._bars_since_entry += 1

        feature_events = self._pipeline.update(bar)
        context = StrategyRuntimeContext(
            event=bar,
            features={fe.feature_set_id: fe for fe in feature_events},
            position=self._position,
            bars_since_entry=self._bars_since_entry,
        )
        result = self._engine.update(bar, context=context)

        if result.reason == "enter_short":
            self._position = -1
            self._bars_since_entry = 0
            return SELL  # open short (SignalToOrderPolicy sell_means=short)
        if result.reason == "exit_short":
            self._position = 0
            self._bars_since_entry = 0
            return BUY  # close short
        return HOLD


PLUGIN = StrategyPlugin(
    name="vwm_short",
    config_cls=VwmShortConfig,
    strategy_cls=VwmShortStrategy,
    build_specs=build_specs,
    default_config_path="strategies/vwm_short/config.yaml",
)
