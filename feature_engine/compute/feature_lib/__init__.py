"""Modular OHLCV feature library (pure Python).

New technical features live here, split by category, instead of growing the
historical monolithic ``feature_engine/compute/features.py``.  Each module
reuses the shared scaffolding from ``feature_lib.base`` (which itself reuses the
helpers in ``features.py``), so behaviour matches the existing features.

No module in this package imports ``nautilus_trader`` or uses a Nautilus
indicator.  Features are registered for the PythonBackend in
``feature_engine/compute/backend.py``.
"""
from feature_engine.compute.feature_lib.normalization import ZScoreFeature
from feature_engine.compute.feature_lib.price_action import (
    BreakoutDownFeature,
    BreakoutUpFeature,
    CandleBodyRatioFeature,
    DrawdownFromRollingHighFeature,
    LowerShadowRatioFeature,
    PricePositionFeature,
    RollingRangeFeature,
    UpperShadowRatioFeature,
)
from feature_engine.compute.feature_lib.returns import (
    MomentumNFeature,
    ReturnNFeature,
)
from feature_engine.compute.feature_lib.volatility import (
    ATRFeature,
    BollingerPercentBFeature,
    BollingerWidthFeature,
    TrueRangeFeature,
    VolatilityRatioFeature,
)
from feature_engine.compute.feature_lib.volume import (
    QuoteVolumeFeature,
    VolumeRatioFeature,
    VolumeZScoreFeature,
    VWAPDistanceFeature,
)

__all__ = [
    # price_action
    "RollingRangeFeature",
    "PricePositionFeature",
    "DrawdownFromRollingHighFeature",
    "BreakoutUpFeature",
    "BreakoutDownFeature",
    "CandleBodyRatioFeature",
    "UpperShadowRatioFeature",
    "LowerShadowRatioFeature",
    # returns
    "ReturnNFeature",
    "MomentumNFeature",
    # volatility
    "TrueRangeFeature",
    "ATRFeature",
    "VolatilityRatioFeature",
    "BollingerWidthFeature",
    "BollingerPercentBFeature",
    # normalization
    "ZScoreFeature",
    # volume
    "VolumeZScoreFeature",
    "VolumeRatioFeature",
    "QuoteVolumeFeature",
    "VWAPDistanceFeature",
]
