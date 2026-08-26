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
from feature_engine.compute.feature_lib.bias import BiasFeature
from feature_engine.compute.feature_lib.obv import OnBalanceVolumeFeature
from feature_engine.compute.feature_lib.cci import CommodityChannelIndexFeature
from feature_engine.compute.feature_lib.hlc_mean import HlcMeanFeature
from feature_engine.compute.feature_lib.hma import HullMovingAverageFeature
from feature_engine.compute.feature_lib.directional_movement import DirectionalMovementFeature
from feature_engine.compute.feature_lib.exponential_moving_average import ExponentialMovingAverageFeature
from feature_engine.compute.feature_lib.rsi import RelativeStrengthIndexFeature
from feature_engine.compute.feature_lib.supertrend import SuperTrendFeature
from feature_engine.compute.feature_lib.awesome_oscillator import AwesomeOscillatorFeature
from feature_engine.compute.feature_lib.aroon import AroonFeature
from feature_engine.compute.feature_lib.macd import MovingAverageConvergenceDivergenceFeature
from feature_engine.compute.feature_lib.fractal import ConfirmedFractalFeature
from feature_engine.compute.feature_lib.psar import ParabolicSarFeature
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
from feature_engine.compute.feature_lib.trade import (
    AvgTradeSizeFeature,
    LargeTradeRatioFeature,
    SignedTradeVolumeFeature,
    TradeCountFeature,
    TradeImbalanceFeature,
    TradeIntensityFeature,
    TradePriceMeanFeature,
    TradeQuoteVolumeSumFeature,
    TradeVolumeSumFeature,
    TradeVWAPFeature,
)
from feature_engine.compute.feature_lib.volume import (
    QuoteVolumeFeature,
    VolumeRatioFeature,
    VolumeZScoreFeature,
    VWAPDistanceFeature,
)
from feature_engine.compute.feature_lib.session import (
    CompletedTimeframeFeature,
    CryptoUtcSessionFeature,
    SessionFlattenDueFeature,
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
    "BiasFeature",
    "OnBalanceVolumeFeature",
    "CommodityChannelIndexFeature",
    "HlcMeanFeature",
    "HullMovingAverageFeature",
    "DirectionalMovementFeature",
    "ExponentialMovingAverageFeature",
    "RelativeStrengthIndexFeature",
    "SuperTrendFeature",
    "AwesomeOscillatorFeature",
    "AroonFeature",
    "MovingAverageConvergenceDivergenceFeature",
    "ConfirmedFractalFeature",
    "ParabolicSarFeature",
    # volume
    "VolumeZScoreFeature",
    "VolumeRatioFeature",
    "QuoteVolumeFeature",
    "VWAPDistanceFeature",
    "CryptoUtcSessionFeature",
    "SessionFlattenDueFeature",
    "CompletedTimeframeFeature",
    # trade (tick)
    "TradeCountFeature",
    "TradeVolumeSumFeature",
    "TradeQuoteVolumeSumFeature",
    "AvgTradeSizeFeature",
    "SignedTradeVolumeFeature",
    "TradeImbalanceFeature",
    "TradeVWAPFeature",
    "LargeTradeRatioFeature",
    "TradeIntensityFeature",
    "TradePriceMeanFeature",
]
