"""
Backend abstraction for feature creation.

Two components:

FeatureBackend (Protocol)
    Structural interface: create a FeatureBase instance from a FeatureSpec.
    Any class with create_feature() satisfies the protocol.

BackendRegistry
    Maps backend name strings (e.g. "python", "numpy") to registered backends.
    SpecFeatureEngine calls registry.create_feature(spec) for each spec,
    so adding a new backend (e.g. a Rust extension) only requires one
    registry.register() call — zero strategy code changes.

PythonBackend
    Pure-Python implementation. Type dispatch via params["type"] first, then
    by name prefix (longest-match first). Implements: rolling_mean, rolling_std,
    rolling_min, rolling_max, rolling_sum, rolling_volume_sum, vwap,
    simple_return, log_return, ewma, spread, mid_price, book_imbalance.

    Dispatch priority:
    1. params["type"] — explicit, always wins.
    2. Exact name match — "rolling_sum" → RollingSumFeature.
    3. Longest-prefix name match — "rolling_sum_5bar" → rolling_sum (not
       rolling_volume_sum, because rolling_volume_sum does not match the
       prefix test for that name).
    Ambiguity is impossible by construction: longer keys shadow shorter ones.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from nautilus_ext.features.compute.feature_base import FeatureBase
from nautilus_ext.features.compute.features import (
    BookImbalanceFeature,
    EWMAFeature,
    LogReturnFeature,
    MidPriceFeature,
    RealizedVolatilityFeature,
    RollingMaxFeature,
    RollingMeanFeature,
    RollingMinFeature,
    RollingStdFeature,
    RollingSumFeature,
    RollingVolumeSumFeature,
    SimpleReturnFeature,
    SpreadFeature,
    VWAPFeature,
)
from nautilus_ext.features.compute.spec import FeatureSpec


@runtime_checkable
class FeatureBackend(Protocol):
    """Structural protocol for feature creation backends.

    Any class with a ``create_feature(spec)`` method satisfies this protocol
    without explicit inheritance.
    """

    def create_feature(self, spec: FeatureSpec) -> FeatureBase:
        """Instantiate a FeatureBase from the given FeatureSpec."""
        ...


# ---------------------------------------------------------------------------
# Type-to-class mapping for PythonBackend
# ---------------------------------------------------------------------------

_FEATURE_CLASSES: dict[str, type] = {
    "rolling_mean": RollingMeanFeature,
    "rolling_std": RollingStdFeature,
    "rolling_min": RollingMinFeature,
    "rolling_max": RollingMaxFeature,
    "rolling_sum": RollingSumFeature,
    "rolling_volume_sum": RollingVolumeSumFeature,
    "realized_volatility": RealizedVolatilityFeature,
    "vwap": VWAPFeature,
    "simple_return": SimpleReturnFeature,
    "log_return": LogReturnFeature,
    "ewma": EWMAFeature,
    "spread": SpreadFeature,
    "mid_price": MidPriceFeature,
    "book_imbalance": BookImbalanceFeature,
}

# Sorted longest-first to avoid prefix ambiguity (e.g. "rolling_std" vs "rolling_std_dev")
_TYPE_KEYS_BY_LEN: tuple[str, ...] = tuple(
    sorted(_FEATURE_CLASSES.keys(), key=len, reverse=True)
)


def _infer_type(name: str) -> str | None:
    """Infer the feature type key from a feature spec name.

    Tries exact match first, then prefix match (longest key first).
    """
    if name in _FEATURE_CLASSES:
        return name
    for key in _TYPE_KEYS_BY_LEN:
        if name.startswith(key):
            return key
    return None


class PythonBackend:
    """Pure-Python feature backend.

    Type dispatch order:
    1. ``spec.params["type"]`` — explicit, highest priority.
    2. Name prefix matching  — ``"rolling_mean_close_20"`` → ``rolling_mean``.
    3. ValueError if neither resolves.

    To register a custom feature class without subclassing PythonBackend,
    extend _FEATURE_CLASSES directly:
        from nautilus_ext.features.compute.backend import _FEATURE_CLASSES
        _FEATURE_CLASSES["my_custom"] = MyCustomFeature
    """

    def available_feature_types(self) -> list[str]:
        """Return all registered feature type keys, sorted alphabetically."""
        return sorted(_FEATURE_CLASSES.keys())

    def create_feature(self, spec: FeatureSpec) -> FeatureBase:
        # 1. Explicit type key in params
        type_key = spec.params.get("type")
        if type_key:
            cls = _FEATURE_CLASSES.get(type_key)
            if cls is None:
                raise ValueError(
                    f"PythonBackend: unknown feature type {type_key!r} "
                    f"in spec {spec.name!r}. Known types: {sorted(_FEATURE_CLASSES)}"
                )
            return cls(spec)

        # 2. Infer from name prefix
        inferred = _infer_type(spec.name)
        if inferred:
            return _FEATURE_CLASSES[inferred](spec)

        raise ValueError(
            f"PythonBackend: cannot determine feature type for spec {spec.name!r}. "
            f"Set params={{'type': '<type>'}} or use a name that starts with a known type. "
            f"Known types: {sorted(_FEATURE_CLASSES)}"
        )


class BackendRegistry:
    """Registry mapping backend name strings to FeatureBackend implementations.

    Usage
    -----
    registry = BackendRegistry()
    registry.register("python", PythonBackend())
    registry.register("numpy", MyNumpyBackend())   # swap in without touching strategies

    feature = registry.create_feature(spec)        # dispatches by spec.backend
    """

    def __init__(self) -> None:
        self._backends: dict[str, FeatureBackend] = {}

    def register(self, name: str, backend: FeatureBackend) -> None:
        """Register a backend under the given name."""
        self._backends[name] = backend

    def create_feature(self, spec: FeatureSpec) -> FeatureBase:
        """Create a feature instance for the given spec.

        Raises ValueError when spec.backend is not registered.
        """
        backend = self._backends.get(spec.backend)
        if backend is None:
            raise ValueError(
                f"BackendRegistry: no backend registered for {spec.backend!r}. "
                f"Registered backends: {sorted(self._backends)}"
            )
        return backend.create_feature(spec)

    def available_backends(self) -> list[str]:
        return sorted(self._backends.keys())


def build_default_registry() -> BackendRegistry:
    """Return a BackendRegistry pre-loaded with the pure-Python backend."""
    registry = BackendRegistry()
    registry.register("python", PythonBackend())
    return registry
