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
    simple_return, log_return, ewma, spread, mid_price, book_imbalance,
    realized_volatility; and derived (feature-to-feature) types: ratio,
    difference, sum, product, rolling_std_derived.

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

from feature_engine.compute.feature_base import FeatureBase
from feature_engine.compute.features import (
    BookImbalanceFeature,
    DifferenceDerivedFeature,
    EWMAFeature,
    LogReturnFeature,
    MidPriceFeature,
    ProductDerivedFeature,
    RatioDerivedFeature,
    RealizedVolatilityFeature,
    RollingMaxFeature,
    RollingMeanFeature,
    RollingMinFeature,
    RollingStdDerivedFeature,
    RollingStdFeature,
    RollingSumFeature,
    RollingVolumeSumFeature,
    SimpleReturnFeature,
    SpreadFeature,
    SumDerivedFeature,
    VWAPFeature,
)
# Modular feature library (pure Python; kept out of the legacy features.py).
from feature_engine.compute.feature_lib import (
    ATRFeature,
    AvgTradeSizeFeature,
    BollingerPercentBFeature,
    BollingerWidthFeature,
    BreakoutDownFeature,
    BreakoutUpFeature,
    CandleBodyRatioFeature,
    CommodityChannelIndexFeature,
    DrawdownFromRollingHighFeature,
    DirectionalMovementFeature,
    ExponentialMovingAverageFeature,
    RelativeStrengthIndexFeature,
    SuperTrendFeature,
    AwesomeOscillatorFeature,
    AroonFeature,
    MovingAverageConvergenceDivergenceFeature,
    ConfirmedFractalFeature,
    ParabolicSarFeature,
    LargeTradeRatioFeature,
    LowerShadowRatioFeature,
    HlcMeanFeature,
    HullMovingAverageFeature,
    MomentumNFeature,
    PricePositionFeature,
    QuoteVolumeFeature,
    ReturnNFeature,
    RollingRangeFeature,
    SignedTradeVolumeFeature,
    TradeCountFeature,
    TradeImbalanceFeature,
    TradeIntensityFeature,
    TradePriceMeanFeature,
    TradeQuoteVolumeSumFeature,
    TradeVolumeSumFeature,
    TradeVWAPFeature,
    TrueRangeFeature,
    UpperShadowRatioFeature,
    VolatilityRatioFeature,
    VolumeRatioFeature,
    VolumeZScoreFeature,
    VWAPDistanceFeature,
    ZScoreFeature,
)
from feature_engine.compute.spec import FeatureSpec


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
    # Derived (feature-to-feature) types — require depends_on in FeatureSpec
    "ratio": RatioDerivedFeature,
    "difference": DifferenceDerivedFeature,
    "sum": SumDerivedFeature,
    "product": ProductDerivedFeature,
    "rolling_std_derived": RollingStdDerivedFeature,
    # OHLCV feature library (pure Python; input_type="bar")
    # A. price / bar structure
    "rolling_range": RollingRangeFeature,
    "true_range": TrueRangeFeature,
    "candle_body_ratio": CandleBodyRatioFeature,
    "upper_shadow_ratio": UpperShadowRatioFeature,
    "lower_shadow_ratio": LowerShadowRatioFeature,
    # B. trend / momentum
    "return_n": ReturnNFeature,
    "momentum_n": MomentumNFeature,
    "price_position": PricePositionFeature,
    "drawdown_from_rolling_high": DrawdownFromRollingHighFeature,
    "breakout_up": BreakoutUpFeature,
    "breakout_down": BreakoutDownFeature,
    "hma": HullMovingAverageFeature,
    "cci": CommodityChannelIndexFeature,
    "hlc_mean": HlcMeanFeature,
    "directional_movement": DirectionalMovementFeature,
    "ema": ExponentialMovingAverageFeature,
    "rsi": RelativeStrengthIndexFeature,
    "awesome_oscillator": AwesomeOscillatorFeature,
    "aroon": AroonFeature,
    "macd": MovingAverageConvergenceDivergenceFeature,
    "confirmed_fractal": ConfirmedFractalFeature,
    "psar": ParabolicSarFeature,
    "supertrend": SuperTrendFeature,
    # C. volatility
    "atr": ATRFeature,
    "volatility_ratio": VolatilityRatioFeature,
    "bollinger_width": BollingerWidthFeature,
    "bollinger_percent_b": BollingerPercentBFeature,
    # D. normalization / volume
    "zscore": ZScoreFeature,
    "volume_zscore": VolumeZScoreFeature,
    "volume_ratio": VolumeRatioFeature,
    "quote_volume": QuoteVolumeFeature,
    "vwap_distance": VWAPDistanceFeature,
    # Trade (tick) features — input_type="trade"
    "trade_count": TradeCountFeature,
    "trade_volume_sum": TradeVolumeSumFeature,
    "trade_quote_volume_sum": TradeQuoteVolumeSumFeature,
    "avg_trade_size": AvgTradeSizeFeature,
    "signed_trade_volume": SignedTradeVolumeFeature,
    "trade_imbalance": TradeImbalanceFeature,
    "trade_vwap": TradeVWAPFeature,
    "large_trade_ratio": LargeTradeRatioFeature,
    "trade_intensity": TradeIntensityFeature,
    "trade_price_mean": TradePriceMeanFeature,
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
        from feature_engine.compute.backend import _FEATURE_CLASSES
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
