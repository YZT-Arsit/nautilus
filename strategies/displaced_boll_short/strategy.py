"""Displaced-Bollinger short strategy adapter (FeatureSnapshot -> engine).

Thin glue between the runner's :class:`FeatureSnapshot` stream and the pure
:class:`DisplacedBollShortEngine`. Reads raw open/high/low/close/volume off the
snapshot (identity-passthrough features, see ``plugin.build_specs``), drives the
engine, records the latest reason. Imports **no** ``nautilus_trader``.

Signals are ``BUY``/``SELL``/``HOLD``; the signal->order decision stays in
``SignalToOrderPolicy`` with ``sell_means: short`` (SELL opens the short, BUY
covers it), same as ``vwm_short``.
"""
from __future__ import annotations

from feature_engine.api import FeatureSnapshot

from strategies.displaced_boll_short.config import DisplacedBollShortConfig
from strategies.displaced_boll_short.engine import HOLD, DisplacedBollShortEngine

# Identity passthrough feature names (window=1 rolling mean == the raw field);
# shared with ``plugin.build_specs``.
_OPEN = "dboll_s_bar_open"
_HIGH = "dboll_s_bar_high"
_LOW = "dboll_s_bar_low"
_CLOSE = "dboll_s_bar_close"
_VOLUME = "dboll_s_bar_volume"


class DisplacedBollShortStrategy:
    """Adapter: drive :class:`DisplacedBollShortEngine` from snapshots."""

    def __init__(self, config: DisplacedBollShortConfig) -> None:
        self._config = config
        self._engine = DisplacedBollShortEngine(config)
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
