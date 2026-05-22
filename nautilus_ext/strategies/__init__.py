from nautilus_ext.strategies.strategy_spec import NautilusStrategySpec
from nautilus_ext.strategies.strategy_spec import StrategyContext
from nautilus_ext.strategies.signal_types import BarInput
from nautilus_ext.strategies.signal_types import SignalResult

__all__ = [
    "BarInput",
    "BaseBarStrategy",
    "NautilusStrategySpec",
    "SignalResult",
    "StrategyContext",
    "VolumeWeightedMomentumShortSignalEngine",
    "VwmShortBarInput",
    "VwmShortSignalConfig",
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
