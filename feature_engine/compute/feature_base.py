"""
FeatureBase — structural protocol for individual incremental feature computations.

Any class that implements the required attributes satisfies this protocol
without explicit inheritance (structural duck-typing via typing.Protocol).

Design contract
---------------
- update() must be O(1) or amortized O(1) — no full-history recomputation.
- update() during warmup behaves identically to live updates; state accumulates.
- is_ready becomes True once enough events have been processed (per WarmupRequirement).
- state_dict() / load_state_dict() support checkpoint / restore without re-downloading.
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from feature_engine.compute.spec import (
    FeatureSpec,
    FeatureUpdate,
    FeatureValue,
    WarmupRequirement,
)


@runtime_checkable
class FeatureBase(Protocol):
    """Stable protocol for individual incremental feature computations.

    Strategy code depends on this protocol and on FeatureSnapshot; it must
    never depend on concrete feature classes or backend state objects.
    """

    @property
    def spec(self) -> FeatureSpec:
        """The stable specification this feature was built from."""
        ...

    def warmup_required(self) -> WarmupRequirement:
        """Declare how many events this feature needs before it is ready."""
        ...

    def reset(self) -> None:
        """Clear all state to the initial condition (as if no events seen)."""
        ...

    def update(self, event: Any) -> FeatureUpdate:
        """Process one market event and return the latest value.

        Must be O(1) or amortized O(1). Must not create a DataFrame.
        When the trigger policy does not fire, the cached value is returned
        with triggered=False.
        """
        ...

    @property
    def value(self) -> FeatureValue:
        """Most recent FeatureValue (cached from the last update call)."""
        ...

    @property
    def is_ready(self) -> bool:
        """True when the feature has processed enough events for reliable output."""
        ...

    def state_dict(self) -> dict:
        """Return a JSON-serialisable dict for checkpoint / restore."""
        ...

    def load_state_dict(self, state: dict) -> None:
        """Restore all internal state from a checkpoint dict."""
        ...
