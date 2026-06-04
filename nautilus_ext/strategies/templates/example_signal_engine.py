"""
Signal Engine development template (Mode B — feature-externalised).

Copy this file and adapt it to build a new strategy signal engine.

Mode B vs Mode A
----------------
Mode A (self-contained, e.g. VWM engine):
    The engine computes features internally inside ``update()``.
    Simpler to write; harder to share features across strategies.

Mode B (feature-externalised, this template):
    Features are computed by a separate FeaturePipeline and passed via
    StrategyRuntimeContext.  The signal engine only does decision logic.

    Advantages:
    - Multiple strategies can share the same FeaturePipeline (one computation).
    - Feature computation and signal logic are independently testable.
    - Strategy does NOT re-compute features that are already in context.

Steps to create a new Mode B signal engine
-------------------------------------------
1. Declare ``requires_features`` listing the feature_set_ids you consume.
2. In ``update()``, read from ``context.get_value(feature_set_id, name)``.
3. Always provide a fallback when features are None (engine warms up before data).
4. Return ``SignalResult`` with explicit ``order_intents`` or legacy fields.
5. Implement ``state_dict`` / ``load_state_dict`` for any internal state.
6. Register with ``@register_signal_engine("my_signal_v1")``.

Strategy spec integration
--------------------------
Declare consumed feature sets in your strategy_spec JSON:

    {
        "strategy": {
            "name": "my_signal_v1",
            "requires_features": ["example_obv_v1"],
            "feature_specs": {
                "example_obv_v1": { "window": 14 }
            },
            ...
        }
    }

The runner reads ``requires_features`` and builds the corresponding
FeaturePipeline before the strategy receives any events.
"""
from __future__ import annotations

from nautilus_ext.features.interfaces import StrategyRuntimeContext
from nautilus_ext.strategies.interfaces.input_types import BarInput
from nautilus_ext.strategies.interfaces.output_types import OrderIntent, SignalResult

# Name must match the registration key in strategy_spec JSON
SIGNAL_NAME = "example_obv_signal_v1"

# Declare which feature sets this engine reads.
# The runner uses this list to build the FeaturePipeline automatically.
REQUIRES_FEATURES = ["example_obv_v1"]


class ExampleObvSignalEngine:
    """Example Mode B signal engine that reads OBV + ROC from StrategyRuntimeContext.

    Signal logic (illustrative, not for production):
    - Enter long when OBV is rising and ROC > threshold.
    - Exit when OBV is falling or ROC < 0.
    - Hold otherwise.

    This engine does NOT compute features itself.
    Features come from StrategyRuntimeContext, pre-computed by FeaturePipeline.
    """

    # Required by BaseSignalEngine protocol
    name: str = SIGNAL_NAME
    requires_features: list[str] = REQUIRES_FEATURES

    def __init__(
        self,
        roc_threshold: float = 0.5,
        feature_set_id: str = "example_obv_v1",
    ) -> None:
        self._roc_threshold = roc_threshold
        self._feature_set_id = feature_set_id

        # Internal state: track previous OBV to detect direction
        self._prev_obv: float | None = None

    # ------------------------------------------------------------------
    # BaseSignalEngine protocol
    # ------------------------------------------------------------------

    def reset(self) -> None:
        """Clear all internal state (called before backtesting or live session)."""
        self._prev_obv = None

    def warmup(self, events, context=None) -> None:
        """Consume warmup events without generating signals.

        During warmup, update internal state but do not produce order intents.
        """
        for event in events:
            self._update_state(event, context)

    def update(self, event, context: StrategyRuntimeContext | None = None) -> SignalResult:
        """Main signal logic — called on every live bar.

        Parameters
        ----------
        event : BarInput
            The triggering market event.
        context : StrategyRuntimeContext | None
            Mode B context containing pre-computed FeatureEvents.
            If None, the engine returns a hold signal (no position change).

        Returns
        -------
        SignalResult
            Signal output.  order_intents is empty when holding.
        """
        self._update_state(event, context)

        if context is None or context.is_warmup:
            return SignalResult(signal_name=SIGNAL_NAME, reason="warmup or no context")

        # ------------------------------------------------------------------
        # Read features from context (Mode B pattern)
        # Strategy does NOT re-compute features — always read from context.
        # ------------------------------------------------------------------
        obv: float | None = context.get_value(self._feature_set_id, "obv")
        roc: float | None = context.get_value(self._feature_set_id, "roc")
        position: int = context.position or 0

        # Fallback: features may be None during the warmup phase even in
        # the live window if the engine hasn't received enough bars yet.
        if obv is None or roc is None:
            return SignalResult(signal_name=SIGNAL_NAME, reason="features_not_ready")

        # Determine OBV direction
        obv_rising = self._prev_obv is not None and obv > self._prev_obv
        obv_falling = self._prev_obv is not None and obv < self._prev_obv

        # ------------------------------------------------------------------
        # Signal decision
        # ------------------------------------------------------------------
        order_intents: list[OrderIntent] = []
        reason: str = "hold"

        if position == 0 and obv_rising and roc > self._roc_threshold:
            # Enter long
            order_intents = [
                OrderIntent(
                    instrument_id=event.instrument_id,
                    action="submit",
                    order_type="market",
                    side="buy",
                    reason=f"obv_rising roc={roc:.2f}>{self._roc_threshold}",
                )
            ]
            reason = "entry_long"

        elif position > 0 and (obv_falling or roc < 0):
            # Exit long
            order_intents = [
                OrderIntent(
                    instrument_id=event.instrument_id,
                    action="submit",
                    order_type="market",
                    side="sell",
                    reduce_only=True,
                    reason=f"obv_falling={obv_falling} roc={roc:.2f}",
                )
            ]
            reason = "exit_long"

        return SignalResult(
            signal_name=SIGNAL_NAME,
            order_intents=order_intents,
            reason=reason,
            debug={
                "obv": obv,
                "roc": roc,
                "obv_rising": obv_rising,
                "position": position,
            },
        )

    def state_dict(self) -> dict:
        """Serialise internal state for checkpoint / warm restart."""
        return {
            "roc_threshold": self._roc_threshold,
            "feature_set_id": self._feature_set_id,
            "prev_obv": self._prev_obv,
        }

    def load_state_dict(self, state: dict) -> None:
        """Restore internal state from a checkpoint dict."""
        self._roc_threshold = state["roc_threshold"]
        self._feature_set_id = state["feature_set_id"]
        self._prev_obv = state.get("prev_obv")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _update_state(self, event, context: StrategyRuntimeContext | None) -> None:
        """Update any internal state that must be tracked across bars."""
        if context is not None:
            obv = context.get_value(self._feature_set_id, "obv")
            if obv is not None:
                self._prev_obv = float(obv)
