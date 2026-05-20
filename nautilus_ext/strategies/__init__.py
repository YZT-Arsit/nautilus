from nautilus_ext.strategies.strategy_spec import NautilusStrategySpec
from nautilus_ext.strategies.strategy_spec import StrategyContext

__all__ = [
    "NautilusStrategySpec",
    "StrategyContext",
    "VolumeWeightedMomentumShortSignalEngine",
    "VwmShortBarInput",
    "VwmShortSignalConfig",
]


def __getattr__(name: str):
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
