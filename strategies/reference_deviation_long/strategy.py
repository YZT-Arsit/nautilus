"""Reference Deviation System long strategy adapter (FeatureSnapshot -> engine).

Thin glue between the runner's :class:`FeatureSnapshot` stream and the pure
:class:`ReferenceDeviationLongEngine`. Reads raw open/high/low/close/volume off
the snapshot (identity-passthrough features, see ``plugin.build_specs``), drives
the engine, records the latest reason. Imports **no** ``nautilus_trader``.

Signals are ``BUY``/``SELL``/``HOLD``; the signal->order decision stays in
``SignalToOrderPolicy`` with ``sell_means: flat`` (BUY opens the long, SELL
flattens it).
"""
from __future__ import annotations

from feature_engine.api import FeatureSnapshot

from strategies.reference_deviation_long.config import ReferenceDeviationLongConfig
from strategies.reference_deviation_long.engine import HOLD, ReferenceDeviationLongEngine

# Identity passthrough feature names (window=1 rolling mean == the raw field);
# shared with ``plugin.build_specs``.
_OPEN = "refdev_l_bar_open"
_HIGH = "refdev_l_bar_high"
_LOW = "refdev_l_bar_low"
_CLOSE = "refdev_l_bar_close"
_VOLUME = "refdev_l_bar_volume"


class ReferenceDeviationLongStrategy:
    """Adapter: drive :class:`ReferenceDeviationLongEngine` from snapshots."""

    def __init__(self, config: ReferenceDeviationLongConfig) -> None:
        self._config = config
        self._engine = ReferenceDeviationLongEngine(config)
        self.last_reason = "warmup_hold"

    @property
    def position(self) -> int:
        return self._engine.position

    def on_snapshot(self, snapshot: FeatureSnapshot) -> str:
        """Legacy baseline entry point using the engine's immediate-fill wrapper."""
        return self.signal_from_snapshot(snapshot)

    def signal_from_snapshot(
        self,
        snapshot: FeatureSnapshot,
        *,
        position: int | None = None,
        bars_since_entry: int | None = None,
    ) -> str:
        """Generate a signal, optionally using execution-supplied state."""
        open_ = snapshot.value(_OPEN)
        high = snapshot.value(_HIGH)
        low = snapshot.value(_LOW)
        close = snapshot.value(_CLOSE)
        volume = snapshot.value(_VOLUME)
        if open_ is None or high is None or low is None or close is None or volume is None:
            self.last_reason = "warmup_hold"
            return HOLD
        values = (float(open_), float(high), float(low), float(close), float(volume))
        if position is None:
            signal, reason = self._engine.update(*values)
        else:
            signal, reason = self._engine.generate_signal(
                *values,
                position=position,
                bars_since_entry=int(bars_since_entry or 0),
            )
        self.last_reason = reason
        return signal
