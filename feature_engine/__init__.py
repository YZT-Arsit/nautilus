"""feature_engine — the canonical custom feature processing layer.

This package now hosts **two** historically separate pieces, both reachable from
here:

1. The **streaming feature-compute layer** (moved here from ``nautilus_ext``):
   ``api`` (FeatureSnapshot / FeatureSpec / rolling_mean_spec), ``runner``
   (FeatureStrategyRunner), ``compute`` (the spec engine), plus the Feature Data
   Layer (FeatureEvent, stores, pipeline, registry, …).

2. The **offline/streaming feature framework** (formerly ``quant_feature_engine``):
   ``core`` (Feature / FeatureDAG / registry), ``storage``, ``streaming``,
   ``execution``.

Importing this package is intentionally cheap: heavy/optional pieces are loaded
lazily via ``__getattr__``. ``nautilus_ext/features`` is now a thin compatibility
shim that re-exports from here.

Architecture: ``data_engine -> feature_engine -> strategy_framework -> strategies``.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

# --- eager, lightweight exports from the streaming feature-compute layer ---
from feature_engine.base import BarFeatureEngine, FeatureSnapshot
from feature_engine.feature_event import FeatureEvent
from feature_engine.feature_schema import FeatureFieldSpec, FeatureSetSpec
from feature_engine.tradeblazer_features import RawMomentumFeature, cross_over, cross_under

__all__ = [
    # --- offline/streaming framework (formerly quant_feature_engine) ---
    "Feature",
    "FeatureMeta",
    "register",
    "registry",
    "FeatureDAG",
    # --- streaming feature-compute layer (legacy names, backward compat) ---
    "AtrFeature",
    "BarFeatureEngine",
    "EmaFeature",
    "FeatureSnapshot",
    "RawMomentumFeature",
    "VwmFeatureConfig",
    "VwmFeatureEngine",
    "VwmFeatureSnapshot",
    "cross_over",
    "cross_under",
    # --- Feature Data Layer ---
    "FeatureEvent",
    "FeatureFieldSpec",
    "FeatureSetSpec",
    "FeatureEngineBase",
    "BaseFeatureEngine",
    "FeaturePipeline",
    "OnlineFeatureStore",
    "OfflineFeatureStore",
    "StrategyRuntimeContext",
    "VwmBarFeatureEngine",
    "FeatureRecorder",
    "FeatureQueryCache",
    "FeatureJoiner",
    "FeatureCheckpointManager",
    "register_feature_engine",
    "build_feature_engine",
    "available_feature_engines",
]


def __getattr__(name: str):  # PEP 562 lazy attribute access
    # === offline/streaming framework (formerly quant_feature_engine) ===
    if name in {"Feature", "FeatureMeta"}:
        from feature_engine.core.feature import Feature, FeatureMeta

        return {"Feature": Feature, "FeatureMeta": FeatureMeta}[name]
    if name == "register":
        from feature_engine.core.registry import register

        return register
    if name == "registry":
        from feature_engine.core.registry import registry

        return registry
    if name == "FeatureDAG":
        from feature_engine.core.dag import FeatureDAG

        return FeatureDAG

    # === streaming feature-compute layer ===
    # --- legacy Nautilus-dependent indicators (kept lazy: native deps) ---
    if name in {"AtrFeature", "EmaFeature"}:
        from feature_engine.nautilus_indicators import AtrFeature, EmaFeature

        return {"AtrFeature": AtrFeature, "EmaFeature": EmaFeature}[name]

    if name in {"VwmFeatureConfig", "VwmFeatureEngine", "VwmFeatureSnapshot"}:
        from feature_engine.vwm_features import (
            VwmFeatureConfig,
            VwmFeatureEngine,
            VwmFeatureSnapshot,
        )

        return {
            "VwmFeatureConfig": VwmFeatureConfig,
            "VwmFeatureEngine": VwmFeatureEngine,
            "VwmFeatureSnapshot": VwmFeatureSnapshot,
        }[name]

    # --- Feature Data Layer ---
    if name in {"FeatureEngineBase", "BaseFeatureEngine"}:
        from feature_engine.feature_engine import BaseFeatureEngine, FeatureEngineBase

        return {"FeatureEngineBase": FeatureEngineBase, "BaseFeatureEngine": BaseFeatureEngine}[name]

    if name in {"OnlineFeatureStore", "OfflineFeatureStore"}:
        from feature_engine.feature_store import OfflineFeatureStore, OnlineFeatureStore

        return {"OnlineFeatureStore": OnlineFeatureStore, "OfflineFeatureStore": OfflineFeatureStore}[name]

    if name == "FeaturePipeline":
        from feature_engine.feature_pipeline import FeaturePipeline

        return FeaturePipeline

    if name == "StrategyRuntimeContext":
        from feature_engine.interfaces import StrategyRuntimeContext

        return StrategyRuntimeContext

    if name == "VwmBarFeatureEngine":
        from feature_engine.vwm_adapter import VwmBarFeatureEngine

        return VwmBarFeatureEngine

    if name == "FeatureRecorder":
        from feature_engine.feature_recorder import FeatureRecorder

        return FeatureRecorder

    if name == "FeatureQueryCache":
        from feature_engine.feature_cache import FeatureQueryCache

        return FeatureQueryCache

    if name == "FeatureJoiner":
        from feature_engine.feature_joiner import FeatureJoiner

        return FeatureJoiner

    if name == "FeatureCheckpointManager":
        from feature_engine.feature_checkpoint import FeatureCheckpointManager

        return FeatureCheckpointManager

    if name in {"register_feature_engine", "build_feature_engine", "available_feature_engines"}:
        from feature_engine.feature_registry import (
            available_feature_engines,
            build_feature_engine,
            register_feature_engine,
        )

        return {
            "register_feature_engine": register_feature_engine,
            "build_feature_engine": build_feature_engine,
            "available_feature_engines": available_feature_engines,
        }[name]

    raise AttributeError(f"module 'feature_engine' has no attribute {name!r}")


if TYPE_CHECKING:
    from feature_engine.core.dag import FeatureDAG  # noqa: F401
    from feature_engine.core.feature import Feature, FeatureMeta  # noqa: F401
    from feature_engine.core.registry import register, registry  # noqa: F401
