"""
BaseFeatureEngine — unified protocol and abstract base for feature engines.

Two layers are provided:

1. ``BaseFeatureEngine`` (Protocol, runtime-checkable)
   Structural typing: any class that implements the required attributes
   satisfies the protocol without explicit inheritance.

2. ``FeatureEngineBase`` (ABC)
   Convenience base that provides default ``warmup()`` and ``update_many()``
   implementations.  Subclass this to get free batch-processing behaviour.

Design rules
------------
- ``update(event)`` must NOT create a DataFrame; one lightweight object per call.
- Engines that do not handle a given event type return ``None`` silently.
- ``state_dict`` / ``load_state_dict`` enable warm restarts without re-downloading.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Iterable, Protocol, runtime_checkable

from feature_engine.feature_event import FeatureEvent
from feature_engine.feature_schema import FeatureSetSpec

if TYPE_CHECKING:
    from nautilus_ext.strategies.interfaces.input_types import MarketEvent


@runtime_checkable
class BaseFeatureEngine(Protocol):
    """Structural protocol — any class with these attributes qualifies."""

    @property
    def name(self) -> str: ...

    @property
    def schema(self) -> FeatureSetSpec: ...

    def reset(self) -> None: ...

    def warmup(self, events: Iterable) -> None: ...

    def update(self, event) -> FeatureEvent | None: ...

    def update_many(self, events: Iterable) -> list[FeatureEvent]: ...

    def state_dict(self) -> dict: ...

    def load_state_dict(self, state: dict) -> None: ...


class FeatureEngineBase(ABC):
    """Convenience base class with default ``warmup`` and ``update_many``.

    Subclasses must implement:  ``name``, ``schema``, ``reset``, ``update``,
    ``state_dict``, ``load_state_dict``.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Stable engine identifier; should match ``feature_set_id``."""

    @property
    @abstractmethod
    def schema(self) -> FeatureSetSpec:
        """Schema describing this engine's output columns."""

    @abstractmethod
    def reset(self) -> None:
        """Clear all internal state to the initial condition."""

    @abstractmethod
    def update(self, event) -> FeatureEvent | None:
        """Process one market event; return None if event type is ignored.

        This is the hot path — must not create a DataFrame.
        """

    @abstractmethod
    def state_dict(self) -> dict:
        """Serialise internal state for checkpoint / restore."""

    @abstractmethod
    def load_state_dict(self, state: dict) -> None:
        """Restore internal state from a checkpoint dict."""

    # ------------------------------------------------------------------
    # Default batch helpers (override if a vectorised path is faster)
    # ------------------------------------------------------------------

    def warmup(self, events: Iterable) -> None:
        """Pre-heat the engine with historical events; outputs are discarded."""
        for event in events:
            self.update(event)

    def update_many(self, events: Iterable) -> list[FeatureEvent]:
        """Process a sequence of events; returns only non-None results."""
        results: list[FeatureEvent] = []
        for event in events:
            fe = self.update(event)
            if fe is not None:
                results.append(fe)
        return results
