"""Global feature registry.

Features register themselves at import time via the ``@register`` decorator.
The registry is a thin dict mapping ``meta.name`` → ``Feature`` class. We keep
classes (not instances) here so each engine can create fresh state-bearing
instances for its own thread/actor.
"""
from __future__ import annotations

from typing import TypeVar

from feature_engine.core.feature import Feature

_REGISTRY: dict[str, type[Feature]] = {}
T = TypeVar("T", bound=Feature)


def register(cls: type[T]) -> type[T]:
    """Decorator: register a Feature subclass by its ``meta.name``.

    Re-registering the same name with a *different* class raises ``ValueError``
    so we catch accidental collisions instead of silently shadowing.
    """
    if not hasattr(cls, "meta"):
        raise TypeError(f"{cls.__name__} has no .meta attribute")
    name = cls.meta.name  # type: ignore[attr-defined]
    existing = _REGISTRY.get(name)
    if existing is not None and existing is not cls:
        raise ValueError(
            f"Feature name {name!r} already registered to {existing.__name__}; "
            f"refusing to overwrite with {cls.__name__}"
        )
    _REGISTRY[name] = cls
    return cls


def get(name: str) -> type[Feature]:
    """Look up a feature class by name. Raises ``KeyError`` if unknown."""
    try:
        return _REGISTRY[name]
    except KeyError as e:
        raise KeyError(
            f"Unknown feature {name!r}. Known: {sorted(_REGISTRY)}"
        ) from e


def registry() -> dict[str, type[Feature]]:
    """Return a shallow copy of the registry — for inspection only."""
    return dict(_REGISTRY)


def clear() -> None:
    """Drop all registrations. For test isolation."""
    _REGISTRY.clear()
