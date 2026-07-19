"""Dual-MA strategy adapter (FeatureSnapshot -> engine).

Thin glue between the runner's :class:`FeatureSnapshot` stream and the pure
:class:`DualMaEngine`. Reads raw open/close off the snapshot (identity-passthrough
features, see ``plugin.build_specs``), drives the engine, and returns a
:class:`PlannedSignal` — a display label that also carries the bar's sized order
plan. Imports **no** ``nautilus_trader``.

Dual-MA is a stop-and-reverse system (flips between long and short), so it sizes a
reversing order and uses the rich-plan path (``PlannedSignal.actions``) rather than
the single-fixed-quantity ``BUY``/``SELL``/``HOLD`` string path. Run it through
``execution.backend: nautilus_backtest`` with ``mode: simulated`` and
``allow_short: true`` (the fill model that flips a position on a larger opposing
order), same as ``turtle_trader``.
"""
from __future__ import annotations

from feature_engine.api import FeatureSnapshot
from strategy_framework.execution.intents import PlannedSignal

from strategies.dual_ma.config import DualMaConfig
from strategies.dual_ma.engine import HOLD, DualMaEngine

# Identity passthrough feature names (window=1 rolling mean == the raw field);
# shared with ``plugin.build_specs``.
_OPEN = "dualma_bar_open"
_CLOSE = "dualma_bar_close"


class DualMaStrategy:
    """Adapter: drive :class:`DualMaEngine` from feature snapshots."""

    def __init__(self, config: DualMaConfig) -> None:
        self._config = config
        self._engine = DualMaEngine(config)
        self.last_reason = "warmup"

    @property
    def position(self) -> int:
        return self._engine.position

    def on_snapshot(self, snapshot: FeatureSnapshot) -> PlannedSignal:
        open_ = snapshot.value(_OPEN)
        close = snapshot.value(_CLOSE)
        if open_ is None or close is None:
            self.last_reason = "warmup"
            return PlannedSignal(HOLD, ())
        label, actions, reason = self._engine.update(float(open_), float(close))
        self.last_reason = reason
        return PlannedSignal(label, actions)

    def on_warmup_snapshot(self, snapshot: FeatureSnapshot) -> None:
        """Advance the decision engine without emitting or assuming an order."""
        close = snapshot.value(_CLOSE)
        if close is not None:
            self._engine.warmup(float(close))
