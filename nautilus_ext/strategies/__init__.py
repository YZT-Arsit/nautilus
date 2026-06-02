from nautilus_ext.strategies.strategy_spec import NautilusStrategySpec
from nautilus_ext.strategies.strategy_spec import StrategyContext
from nautilus_ext.strategies.interfaces import FeatureVectorInput
from nautilus_ext.strategies.interfaces import FundingRateInput
from nautilus_ext.strategies.interfaces import MarketEvent
from nautilus_ext.strategies.interfaces import OrderBookInput
from nautilus_ext.strategies.interfaces import OrderIntent
from nautilus_ext.strategies.interfaces import QuoteTickInput
from nautilus_ext.strategies.interfaces import StrategyInputSchema
from nautilus_ext.strategies.interfaces import StrategySpecV2
from nautilus_ext.strategies.interfaces import TradeTickInput
from nautilus_ext.strategies.signal_types import BarInput
from nautilus_ext.strategies.signal_types import SignalResult
from nautilus_ext.strategies.registry import available_signal_engines
from nautilus_ext.strategies.registry import build_signal_engine
from nautilus_ext.strategies.registry import get_signal_engine_class
from nautilus_ext.strategies.registry import register_signal_engine
from nautilus_ext.strategies.tradeblazer_helpers import MomentumState
from nautilus_ext.strategies.tradeblazer_helpers import cross_over
from nautilus_ext.strategies.tradeblazer_helpers import cross_under

__all__ = [
    "BarInput",
    "BaseBarStrategy",
    "FeatureVectorInput",
    "FundingRateInput",
    "MarketEvent",
    "MomentumState",
    "NautilusStrategySpec",
    "OrderBookInput",
    "OrderIntent",
    "QuoteTickInput",
    "SignalResult",
    "StrategyInputSchema",
    "StrategySpecV2",
    "StrategyContext",
    "TradeTickInput",
    "VolumeWeightedMomentumShortSignalEngine",
    "VwmShortBarInput",
    "VwmShortSignalConfig",
    "available_signal_engines",
    "build_signal_engine",
    "cross_over",
    "cross_under",
    "get_signal_engine_class",
    "register_signal_engine",
]


def __getattr__(name: str):
    if name == "BaseBarStrategy":
        from nautilus_ext.strategies.base_bar_strategy import BaseBarStrategy

        return BaseBarStrategy
    if name in {
        "VolumeWeightedMomentumShortSignalEngine",
        "VwmShortBarInput",
        "VwmShortSignalConfig",
    }:
        from nautilus_ext.strategies.vwm_short_signals import VwmShortBarInput
        from nautilus_ext.strategies.vwm_short_signals import VwmShortSignalConfig
        from nautilus_ext.strategies.vwm_short_signals import (
            VolumeWeightedMomentumShortSignalEngine,
        )

        return {
            "VolumeWeightedMomentumShortSignalEngine": VolumeWeightedMomentumShortSignalEngine,
            "VwmShortBarInput": VwmShortBarInput,
            "VwmShortSignalConfig": VwmShortSignalConfig,
        }[name]
    raise AttributeError(f"module 'nautilus_ext.strategies' has no attribute {name!r}")
