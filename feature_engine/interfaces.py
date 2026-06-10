"""
StrategyRuntimeContext — enriched context for Mode B signal engines.

Two operating modes are supported:

Mode A (self-contained, backward-compatible):
    signal_engine.update(bar, position=..., bars_since_entry=...)
    The engine computes features internally.  VWM engine uses this mode.

Mode B (feature-externalised):
    feature_events = pipeline.update(event)
    context = StrategyRuntimeContext(event=bar, features=..., position=...)
    result = signal_engine.update(bar, context=context)
    The engine reads pre-computed FeatureEvents from context and only does
    decision logic.  Different strategies can share the same FeaturePipeline.

StrategyRuntimeContext is also used internally by the runner to pass the
complete state snapshot to Mode B signal engines without coupling the engine
to a specific runner implementation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from nautilus_ext.features.feature_event import FeatureEvent


@dataclass
class StrategyRuntimeContext:
    """Context bundle passed to Mode B signal engines.

    Parameters
    ----------
    event
        The triggering market event (e.g. BarInput, TradeTickInput).
    features : dict[str, FeatureEvent]
        Latest FeatureEvent per feature_set_id, populated by FeaturePipeline.
        Keyed by ``feature_set_id`` string.
    position : int | None
        Current position: -1 (short), 0 (flat), 1 (long).
    bars_since_entry : int
        Bars elapsed since the last entry.  0 when not in position.
    portfolio_snapshot : dict | None
        Optional portfolio state (P&L, margin, exposure).
        Not populated in the current paper-live runner; reserved for
        future TradingNode integration.
    is_warmup : bool
        True if this event is part of the warmup sequence.
    """

    event: Any
    features: dict[str, FeatureEvent] = field(default_factory=dict)
    position: int | None = 0
    bars_since_entry: int = 0
    portfolio_snapshot: dict | None = None
    is_warmup: bool = False

    # ------------------------------------------------------------------
    # Feature access helpers
    # ------------------------------------------------------------------

    def get_feature_values(self, feature_set_id: str) -> dict | None:
        """Return the ``values`` dict of the latest FeatureEvent, or None."""
        fe = self.features.get(feature_set_id)
        return fe.values if fe is not None else None

    def get_value(
        self,
        feature_set_id: str,
        name: str,
        default: Any = None,
    ) -> Any:
        """Return one feature value by set ID and name, or default."""
        fe = self.features.get(feature_set_id)
        if fe is None:
            return default
        return fe.values.get(name, default)

    def get(self, key: str, default: Any = None) -> Any:
        """Dict-style .get() for backward compat with legacy context dicts.

        Checks ``position`` and ``bars_since_entry`` keys first, then
        looks up feature values across all feature sets.
        """
        if key == "position":
            return self.position if self.position is not None else default
        if key == "bars_since_entry":
            return self.bars_since_entry
        # Search feature values
        for fe in self.features.values():
            if key in fe.values:
                return fe.values[key]
        return default

    def to_legacy_dict(self) -> dict:
        """Flatten to legacy dict format for Mode A engines."""
        d: dict = {
            "position": self.position,
            "bars_since_entry": self.bars_since_entry,
            "is_warmup": self.is_warmup,
        }
        for fsid, fe in self.features.items():
            for k, v in fe.values.items():
                d[f"{fsid}.{k}"] = v
        return d
