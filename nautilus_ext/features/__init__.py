"""Reusable streaming bar features.

The same engine instance can be warmed up with historical bars and then updated
one bar at a time from a live feed, keeping batch and incremental semantics aligned.
"""

from nautilus_ext.features.base import BarFeatureEngine
from nautilus_ext.features.base import FeatureSnapshot
from nautilus_ext.features.tradeblazer_features import RawMomentumFeature
from nautilus_ext.features.tradeblazer_features import cross_over
from nautilus_ext.features.tradeblazer_features import cross_under

__all__ = [
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
]


def __getattr__(name: str):
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
    raise AttributeError(f"module 'nautilus_ext.features' has no attribute {name!r}")
