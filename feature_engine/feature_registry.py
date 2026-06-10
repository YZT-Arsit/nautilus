"""
Feature engine registry — register and build feature engines by name.

Usage
-----
Register via decorator::

    @register_feature_engine("my_features_v1")
    class MyFeatureEngine(FeatureEngineBase):
        ...

Register directly::

    register_feature_engine("my_features_v1", MyFeatureEngine)

Build from name or spec dict::

    engine = build_feature_engine("my_features_v1")
    engine = build_feature_engine("my_features_v1", params={"window": 10})
    engine = build_feature_engine({"name": "my_features_v1", "params": {...}})

New feature sets only need a new engine file + one decorator line + a config entry.
Runners and pipelines do not need to change.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

# Registry: name → class
_REGISTRY: dict[str, type] = {}


def register_feature_engine(name: str, cls=None):
    """Register a feature engine class under a stable identifier.

    Accepts both decorator and direct-call forms::

        @register_feature_engine("vwm_features_v1")
        class VwmBarFeatureEngine(FeatureEngineBase): ...

        register_feature_engine("vwm_features_v1", VwmBarFeatureEngine)
    """
    def _register(cls_: type) -> type:
        _REGISTRY[name] = cls_
        log.debug("feature_registry: registered %r → %s", name, cls_.__name__)
        return cls_

    if cls is not None:
        return _register(cls)
    return _register


def get_feature_engine_class(name: str) -> type:
    """Return the class registered under *name*.

    Raises ``KeyError`` if not found.
    """
    if name not in _REGISTRY:
        raise KeyError(
            f"Feature engine {name!r} not found. "
            f"Available: {sorted(_REGISTRY)}"
        )
    return _REGISTRY[name]


def available_feature_engines() -> list[str]:
    """Return sorted list of all registered feature engine names."""
    return sorted(_REGISTRY)


def build_feature_engine(spec_or_name, params: dict | None = None):
    """Instantiate a feature engine from a name or spec dict.

    Parameters
    ----------
    spec_or_name : str | dict
        ``str`` — looked up directly in the registry.
        ``dict`` — must have key ``"name"``; optional key ``"params"``
        is merged with the *params* argument (params arg overrides).
    params : dict | None
        Constructor keyword arguments.

    Examples
    --------
    >>> engine = build_feature_engine("vwm_features_v1")
    >>> engine = build_feature_engine(
    ...     {"name": "vwm_features_v1", "params": {"mom_len": 3}},
    ...     params={"avg_len": 10},
    ... )
    """
    if isinstance(spec_or_name, str):
        name = spec_or_name
        merged_params: dict = dict(params or {})
    elif isinstance(spec_or_name, dict):
        name = spec_or_name["name"]
        merged_params = {**spec_or_name.get("params", {}), **(params or {})}
    else:
        raise TypeError(
            f"spec_or_name must be str or dict, got {type(spec_or_name).__name__}"
        )

    cls = get_feature_engine_class(name)
    return cls(**merged_params) if merged_params else cls()


def _ensure_builtin_engines_registered() -> None:
    """Trigger registration of built-in adapters (lazy import)."""
    try:
        import nautilus_ext.features.vwm_adapter  # noqa: F401
    except Exception:
        pass  # Optional: Nautilus indicators not compiled on this machine


_ensure_builtin_engines_registered()
