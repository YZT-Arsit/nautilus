"""Reusable streaming bar features and the Feature Data Layer.

The same engine instance can be warmed up with historical bars and then updated
one bar at a time from a live feed, keeping batch and incremental semantics aligned.

Feature Data Layer (new)
------------------------
FeatureEvent         Immutable first-class feature snapshot (replaces SignalResult.debug).
FeatureSetSpec       Formal schema: names, types, versions for each feature set.
FeatureEngineBase    Abstract base / protocol for all feature engines.
FeatureRegistry      register_feature_engine / build_feature_engine factory.
OnlineFeatureStore   In-memory ring buffer for real-time signal engine access.
OfflineFeatureStore  Parquet-backed persistence with batched flush.
FeaturePipeline      Orchestrates N engines over a MarketEvent stream.
StrategyRuntimeContext  Context bundle for Mode B (feature-externalised) strategies.
VwmBarFeatureEngine  Adapter: VwmFeatureEngine → FeatureEvent output.
FeatureRecorder      Session-scoped OfflineFeatureStore wrapper.
FeatureQueryCache    LRU cache for repeated Parquet queries.
FeatureJoiner        Join FeatureEvents with bar/tick DataFrames.
FeatureCheckpointManager  Save/load FeaturePipeline state to JSON.
"""
from nautilus_ext.features.base import BarFeatureEngine
from nautilus_ext.features.base import FeatureSnapshot
from nautilus_ext.features.feature_event import FeatureEvent
from nautilus_ext.features.feature_schema import FeatureFieldSpec, FeatureSetSpec
from nautilus_ext.features.tradeblazer_features import RawMomentumFeature
from nautilus_ext.features.tradeblazer_features import cross_over
from nautilus_ext.features.tradeblazer_features import cross_under

__all__ = [
    # Legacy (preserved for backward compat)
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
    # Feature Data Layer
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


def __getattr__(name: str):
    # --- legacy Nautilus-dependent indicators ---
    if name in {"AtrFeature", "EmaFeature"}:
        from nautilus_ext.features.nautilus_indicators import AtrFeature
        from nautilus_ext.features.nautilus_indicators import EmaFeature
        return {"AtrFeature": AtrFeature, "EmaFeature": EmaFeature}[name]

    if name in {"VwmFeatureConfig", "VwmFeatureEngine", "VwmFeatureSnapshot"}:
        from nautilus_ext.features.vwm_features import VwmFeatureConfig
        from nautilus_ext.features.vwm_features import VwmFeatureEngine
        from nautilus_ext.features.vwm_features import VwmFeatureSnapshot
        return {
            "VwmFeatureConfig": VwmFeatureConfig,
            "VwmFeatureEngine": VwmFeatureEngine,
            "VwmFeatureSnapshot": VwmFeatureSnapshot,
        }[name]

    # --- Feature Data Layer ---
    if name in {"FeatureEngineBase", "BaseFeatureEngine"}:
        from nautilus_ext.features.feature_engine import (
            BaseFeatureEngine,
            FeatureEngineBase,
        )
        return {"FeatureEngineBase": FeatureEngineBase,
                "BaseFeatureEngine": BaseFeatureEngine}[name]

    if name in {"OnlineFeatureStore", "OfflineFeatureStore"}:
        from nautilus_ext.features.feature_store import (
            OfflineFeatureStore,
            OnlineFeatureStore,
        )
        return {"OnlineFeatureStore": OnlineFeatureStore,
                "OfflineFeatureStore": OfflineFeatureStore}[name]

    if name == "FeaturePipeline":
        from nautilus_ext.features.feature_pipeline import FeaturePipeline
        return FeaturePipeline

    if name == "StrategyRuntimeContext":
        from nautilus_ext.features.interfaces import StrategyRuntimeContext
        return StrategyRuntimeContext

    if name == "VwmBarFeatureEngine":
        from nautilus_ext.features.vwm_adapter import VwmBarFeatureEngine
        return VwmBarFeatureEngine

    if name == "FeatureRecorder":
        from nautilus_ext.features.feature_recorder import FeatureRecorder
        return FeatureRecorder

    if name == "FeatureQueryCache":
        from nautilus_ext.features.feature_cache import FeatureQueryCache
        return FeatureQueryCache

    if name == "FeatureJoiner":
        from nautilus_ext.features.feature_joiner import FeatureJoiner
        return FeatureJoiner

    if name == "FeatureCheckpointManager":
        from nautilus_ext.features.feature_checkpoint import FeatureCheckpointManager
        return FeatureCheckpointManager

    if name in {"register_feature_engine", "build_feature_engine",
                "available_feature_engines"}:
        from nautilus_ext.features.feature_registry import (
            available_feature_engines,
            build_feature_engine,
            register_feature_engine,
        )
        return {
            "register_feature_engine": register_feature_engine,
            "build_feature_engine": build_feature_engine,
            "available_feature_engines": available_feature_engines,
        }[name]

    raise AttributeError(f"module 'nautilus_ext.features' has no attribute {name!r}")
