"""Turtle trading system strategy adapter (FeatureSnapshot -> engine).

Thin glue between the runner's :class:`FeatureSnapshot` stream and the pure
:class:`TurtleTraderEngine`. It reads raw open/high/low/close off the snapshot
(exposed as identity-passthrough features, see ``plugin.build_specs``), drives
the engine, and returns a :class:`PlannedSignal` — a display label that also
carries the bar's sized order plan. Imports **no** ``nautilus_trader``.

The Turtle system sizes its own orders (risk/N) and pyramids multiple units, so
it uses the rich-plan path (``PlannedSignal.actions``) rather than the
single-fixed-quantity ``BUY``/``SELL``/``HOLD`` string path. Run it through
``execution.backend: nautilus_backtest`` with ``mode: simulated`` (the fill model
that understands sized multi-order plans and honours per-order fill prices).
"""
from __future__ import annotations

from feature_engine.api import FeatureSnapshot
from strategy_framework.execution.intents import PlannedSignal

from strategies.turtle_trader.config import TurtleTraderConfig
from strategies.turtle_trader.engine import HOLD, TurtleTraderEngine

# Identity passthrough feature names (window=1 rolling mean == the raw field);
# shared with ``plugin.build_specs`` so produced specs and values read here stay
# in lockstep. Turtle needs OPEN too (gap-to-open fill logic).
_OPEN = "turtle_bar_open"
_HIGH = "turtle_bar_high"
_LOW = "turtle_bar_low"
_CLOSE = "turtle_bar_close"


class TurtleTraderStrategy:
    """Adapter: drive :class:`TurtleTraderEngine` from feature snapshots."""

    def __init__(self, config: TurtleTraderConfig) -> None:
        self._config = config
        self._engine = TurtleTraderEngine(config)
        self.last_reason = "warmup"

    @property
    def position(self) -> int:
        return self._engine.position

    def on_snapshot(self, snapshot: FeatureSnapshot) -> PlannedSignal:
        open_ = snapshot.value(_OPEN)
        high = snapshot.value(_HIGH)
        low = snapshot.value(_LOW)
        close = snapshot.value(_CLOSE)
        # Identity specs are ready from bar 1; guard defensively all the same.
        if open_ is None or high is None or low is None or close is None:
            self.last_reason = "warmup"
            return PlannedSignal(HOLD, ())
        label, actions, reason = self._engine.update(
            float(open_), float(high), float(low), float(close)
        )
        self.last_reason = reason
        return PlannedSignal(label, actions)
