"""VWM long strategy plugin (self-contained, Mode A).

Long-side mirror of ``strategies/vwm_short/strategy.py``. Adapts the
self-contained ``VolumeWeightedMomentumLongSignalEngine`` to the shared
``StrategyPlugin`` contract used by ``run_strategy.py``. The signal engine
computes its VWM features internally (Mode A).

Bridging notes
--------------
* ``run_strategy`` only hands the strategy a :class:`FeatureSnapshot`. To
  reconstruct the bar the engine needs, ``build_specs`` declares four identity
  passthrough specs (rolling mean over a window of 1 == the raw value) for
  ``close/high/low/volume``, keeping the spec-driven feature engine in the live
  chain. The VWM maths themselves are computed inside the signal engine.
* The engine's entry is a ``stop_market`` at a trigger price. The native
  backend replays market orders, so we emit the long entry on the bar where the
  engine's trigger fires (``reason == "enter_long"``): a real Nautilus market
  fill at that bar.
* Signals are BUY / SELL / HOLD only; the signal->order decision stays in
  ``SignalToOrderPolicy`` (config ``sell_means: flat``). BUY opens the long,
  SELL flattens it.

This module imports no ``nautilus_trader`` symbols at import time. The VWM
feature engine wraps Nautilus indicators (a pre-existing dependency); those are
imported lazily when a strategy instance is built.
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
class VwmLongConfig:
    """User-facing parameters for the VWM long strategy.

    The ``trend_filter_*`` fields add an OPTIONAL, config-gated regime gate on
    long entries (see :func:`should_block_long_entry`). It is **disabled by
    default**; with ``enable_trend_filter=False`` the strategy behaves as a
    straight port of the TradeBlazer ``VolumeWeightedMomentumSys_L`` system. The
    gate never touches the VWM signal math itself — it only decides whether an
    already-produced ``enter_long`` is allowed in the current regime.
    """

    mom_len: int = 5
    avg_len: int = 20
    atr_len: int = 5
    atr_pcnt: float = 0.5
    setup_len: int = 5
    instrument_id: str = "BTCUSDT.BINANCE"
    bar_type: str | None = None
    # optional, default-off trend regime filter (gates long entries only)
    enable_trend_filter: bool = False
    trend_filter_fast_len: int = 96
    trend_filter_slow_len: int = 384
    trend_filter_mode: str = "long_only_uptrend"
    trend_filter_source: str = "close"


# --- pure trend-filter helpers (unit-testable, no Nautilus) ------------------

def simple_moving_average(values: list[float], length: int) -> float | None:
    """Mean of the last ``length`` values, or None if too few / bad length."""
    if length <= 0 or len(values) < length:
        return None
    window = values[-length:]
    return sum(window) / float(length)


def trend_gate(closes: list[float], *, fast_len: int, slow_len: int,
               mode: str = "long_only_uptrend") -> bool | None:
    """Regime gate for long entries.

    Returns ``True`` (uptrend -> long allowed), ``False`` (downtrend -> block),
    or ``None`` (insufficient history to decide). ``long_only_uptrend`` allows a
    long only when ``fast_ma > slow_ma``. Unknown modes do not gate (True).
    """
    fast = simple_moving_average(closes, fast_len)
    slow = simple_moving_average(closes, slow_len)
    if fast is None or slow is None:
        return None
    if mode == "long_only_uptrend":
        return fast > slow
    return True


def should_block_long_entry(closes: list[float], *, enabled: bool, fast_len: int,
                            slow_len: int, mode: str = "long_only_uptrend") -> bool:
    """Whether to block a long entry this bar.

    ``enabled=False`` -> never block (baseline behaviour, bit-for-bit). When
    enabled, block in a downtrend and, conservatively, during warmup (gate None).
    """
    if not enabled:
        return False
    gate = trend_gate(closes, fast_len=fast_len, slow_len=slow_len, mode=mode)
    if gate is None:
        return True
    return not gate


def build_specs(config: VwmLongConfig) -> list[FeatureSpec]:
    """Identity passthrough specs exposing OHLCV to ``on_snapshot``.

    A rolling mean over a window of 1 returns the field value unchanged and is
    ready from the first bar. These keep the spec-driven feature engine in the
    live chain; VWM math is done by the internal ``vwm_features_v1`` pipeline.
    """
    passthrough = {"input_type": "bar", "window": 1}
    return [
        rolling_mean_spec(_CLOSE, input_field="close", **passthrough),
        rolling_mean_spec(_HIGH, input_field="high", **passthrough),
        rolling_mean_spec(_LOW, input_field="low", **passthrough),
        rolling_mean_spec(_VOLUME, input_field="volume", **passthrough),
    ]


class VwmLongStrategy:
    """Drive the VWM long signal engine from feature snapshots."""

    def __init__(self, config: VwmLongConfig) -> None:
        # Lazy imports: the VWM feature engine wraps Nautilus indicators, so
        # only touch it when a strategy is actually built (not at import time).
        from strategies.vwm_long.signals import (
            VolumeWeightedMomentumLongSignalEngine,
            VwmLongSignalConfig,
        )

        self._config = config
        self._engine = VolumeWeightedMomentumLongSignalEngine(
            VwmLongSignalConfig(
                mom_len=config.mom_len,
                avg_len=config.avg_len,
                atr_len=config.atr_len,
                atr_pcnt=config.atr_pcnt,
                setup_len=config.setup_len,
            ),
        )
        # We drive position externally and pass it through the context so the
        # engine stays stateless about fills (external position).
        self._position = 0
        self._bars_since_entry = 0
        # rolling source history for the optional trend filter (default off)
        self._trend_source: list[float] = []
        # diagnostics: long entries the engine produced, allowed vs blocked
        self.allowed_entry_count = 0
        self.blocked_entry_count = 0

    def on_snapshot(self, snapshot: FeatureSnapshot) -> str:
        from strategies.vwm_long.signal_types import BarInput

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

        # Track the trend-filter source (default close) for the optional gate.
        src = {"close": close, "high": high, "low": low, "volume": volume}.get(
            self._config.trend_filter_source, close)
        self._trend_source.append(float(src))

        # Count bars since entry before the engine evaluates the exit rule.
        if self._position == 1:
            self._bars_since_entry += 1

        result = self._engine.update(
            bar,
            position=self._position,
            bars_since_entry=self._bars_since_entry,
        )

        if result.reason == "enter_long":
            # Optional, default-off regime gate: block the long in a downtrend.
            if should_block_long_entry(
                self._trend_source,
                enabled=self._config.enable_trend_filter,
                fast_len=self._config.trend_filter_fast_len,
                slow_len=self._config.trend_filter_slow_len,
                mode=self._config.trend_filter_mode,
            ):
                self.blocked_entry_count += 1
                return HOLD  # entry suppressed; stay flat, engine sees position 0
            self.allowed_entry_count += 1
            self._position = 1
            self._bars_since_entry = 0
            return BUY  # open long (SignalToOrderPolicy sell_means=flat)
        if result.reason == "exit_long":
            self._position = 0
            self._bars_since_entry = 0
            return SELL  # flatten the long
        return HOLD


PLUGIN = StrategyPlugin(
    name="vwm_long",
    config_cls=VwmLongConfig,
    strategy_cls=VwmLongStrategy,
    build_specs=build_specs,
    default_config_path="strategies/vwm_long/config.yaml",
)
