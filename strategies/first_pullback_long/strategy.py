"""First-PullBack long strategy adapter (FeatureSnapshot -> engine).

Thin glue between the runner's :class:`FeatureSnapshot` stream and the pure
:class:`FirstPullbackLongEngine`. Reads raw open/high/low/close/volume off the
snapshot (identity-passthrough features, see ``plugin.build_specs``), drives the
engine, records the latest reason. Imports **no** ``nautilus_trader``.

Signals are ``BUY``/``SELL``/``HOLD``; the signal->order decision stays in
``SignalToOrderPolicy`` with ``sell_means: flat`` (BUY opens the long, SELL
flattens it), same as ``obv_revisited_long``.
"""
from __future__ import annotations

from feature_engine.api import FeatureSnapshot

from strategies.first_pullback_long.config import FirstPullbackLongConfig
from strategies.first_pullback_long.engine import HOLD, FirstPullbackLongEngine

# Identity passthrough feature names (window=1 rolling mean == the raw field);
# shared with ``plugin.build_specs``.
_OPEN = "fpb_l_bar_open"
_HIGH = "fpb_l_bar_high"
_LOW = "fpb_l_bar_low"
_CLOSE = "fpb_l_bar_close"
_VOLUME = "fpb_l_bar_volume"


class FirstPullbackLongStrategy:
    """Adapter: drive :class:`FirstPullbackLongEngine` from snapshots."""

    def __init__(self, config: FirstPullbackLongConfig) -> None:
        self._config = config
        self._engine = FirstPullbackLongEngine(config)
        self.last_reason = "warmup_hold"

    @property
    def position(self) -> int:
        return self._engine.position

    def on_snapshot(self, snapshot: FeatureSnapshot) -> str:
        open_ = snapshot.value(_OPEN)
        high = snapshot.value(_HIGH)
        low = snapshot.value(_LOW)
        close = snapshot.value(_CLOSE)
        volume = snapshot.value(_VOLUME)
        if open_ is None or high is None or low is None or close is None or volume is None:
            self.last_reason = "warmup_hold"
            return HOLD
        signal, reason = self._engine.update(
            float(open_), float(high), float(low), float(close), float(volume)
        )
        self.last_reason = reason
        return signal
