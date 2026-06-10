"""Compatibility shim — the canonical feature layer moved to ``feature_engine``.

This package used to be the canonical feature-processing layer. Its modules
(``api``, ``runner``, ``compute``, the Feature Data Layer, …) now live in the
top-level ``feature_engine`` package. This shim re-exports them so legacy imports
keep working::

    from nautilus_ext.features import FeatureSnapshot          # -> feature_engine
    from nautilus_ext.features.api import FeatureSpec          # -> feature_engine.api
    from nautilus_ext.features.runner import FeatureStrategyRunner

New code should import from ``feature_engine`` directly. Architecture:
``data_engine -> feature_engine -> strategy_framework -> strategies``.
"""
from __future__ import annotations

import importlib
import sys

import feature_engine as _feature_engine

# Mirror the public surface without eagerly resolving lazy (native-dep) names.
__all__ = list(_feature_engine.__all__)

# Alias submodules so ``import nautilus_ext.features.<x>`` resolves to
# ``feature_engine.<x>``. Heavy/optional submodules (e.g. ``nautilus_indicators``,
# which imports Nautilus native code) are imported lazily and simply skipped here
# if their dependencies are unavailable; they remain reachable via attribute
# access on ``feature_engine``.
_ALIASED_SUBMODULES = (
    "api", "runner", "base", "builders", "compute",
    "feature_cache", "feature_checkpoint", "feature_engine", "feature_event",
    "feature_joiner", "feature_manifest", "feature_pipeline", "feature_recorder",
    "feature_registry", "feature_schema", "feature_store", "interfaces",
    "tradeblazer_features", "vwm_adapter", "vwm_features",
    "examples", "examples.synthetic_bars",
)
for _name in _ALIASED_SUBMODULES:
    try:
        sys.modules[f"{__name__}.{_name}"] = importlib.import_module(f"feature_engine.{_name}")
    except Exception:  # optional/heavy dependency missing in this environment
        pass


def __getattr__(name: str):
    """Delegate attribute access (incl. lazy names) to ``feature_engine``."""
    return getattr(_feature_engine, name)
