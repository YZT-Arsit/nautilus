"""Trend-breakout + ATR strategy adapter (FeatureSnapshot -> engine).

Thin glue between the runner's :class:`FeatureSnapshot` stream and the pure
:class:`TrendBreakoutAtrEngine`. It reads raw close/high/low off the snapshot
(exposed as identity-passthrough features, see ``plugin.build_specs``), drives
the engine, and records the latest decision reason. Imports **no**
``nautilus_trader``. Behaviour is unchanged from the original single-file
module; this split is purely structural.
"""
from __future__ import annotations

from feature_engine.api import FeatureSnapshot

from strategies.trend_breakout_atr.config import TrendBreakoutAtrConfig
from strategies.trend_breakout_atr.engine import BUY, HOLD, TrendBreakoutAtrEngine

# Identity passthrough feature names (window=1 rolling mean == the raw field):
# the runner hands the strategy a FeatureSnapshot, so we expose raw OHLC this way
# and compute trend/breakout/ATR inside the engine (full look-ahead control).
# Shared with ``plugin.build_specs`` so the produced specs and the values read
# here stay in lockstep.
_CLOSE = "tba_bar_close"
_HIGH = "tba_bar_high"
_LOW = "tba_bar_low"


class TrendBreakoutAtrStrategy:
    """Adapter: drive :class:`TrendBreakoutAtrEngine` from feature snapshots."""

    def __init__(self, config: TrendBreakoutAtrConfig) -> None:
        self._config = config
        self._engine = TrendBreakoutAtrEngine(config)
        self.last_reason = "warmup_hold"

    @property
    def position(self) -> int:
        return self._engine.position

    def on_snapshot(self, snapshot: FeatureSnapshot) -> str:
        close = snapshot.value(_CLOSE)
        high = snapshot.value(_HIGH)
        low = snapshot.value(_LOW)
        if close is None or high is None or low is None:
            self.last_reason = "warmup_hold"
            return HOLD
        signal, reason = self._engine.update(float(close), float(high), float(low))
        self.last_reason = reason
        return signal
