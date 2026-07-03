"""TrendScore long strategy adapter (FeatureSnapshot -> engine).

Long-side mirror of ``strategies/trendscore_short/strategy.py``. Thin glue
between the runner's :class:`FeatureSnapshot` stream and the pure
:class:`TrendScoreLongEngine`. Reads raw open/high/low/close/volume off the
snapshot (identity-passthrough features, see ``plugin.build_specs``), drives the
engine, records the latest reason. Imports **no** ``nautilus_trader``.

Signals are ``BUY``/``SELL``/``HOLD``; the signal->order decision stays in
``SignalToOrderPolicy`` with ``sell_means: flat`` (BUY opens the long, SELL
flattens it), same as ``vwm_long``/``ma_crossover``.
"""
from __future__ import annotations

from feature_engine.api import FeatureSnapshot

from strategies.trendscore_long.config import TrendScoreLongConfig
from strategies.trendscore_long.engine import HOLD, TrendScoreLongEngine

# Identity passthrough feature names (window=1 rolling mean == the raw field);
# shared with ``plugin.build_specs``.
_OPEN = "trendscore_bar_open"
_HIGH = "trendscore_bar_high"
_LOW = "trendscore_bar_low"
_CLOSE = "trendscore_bar_close"
_VOLUME = "trendscore_bar_volume"


class TrendScoreLongStrategy:
    """Adapter: drive :class:`TrendScoreLongEngine` from feature snapshots."""

    def __init__(self, config: TrendScoreLongConfig) -> None:
        self._config = config
        self._engine = TrendScoreLongEngine(config)
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
