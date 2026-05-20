from nautilus_ext.strategies.strategy_spec import NautilusStrategySpec
from nautilus_ext.strategies.strategy_spec import StrategyContext

__all__ = [
    "NautilusStrategySpec",
    "StrategyContext",
    "VolumeWeightedMomentumShortConfig",
    "VolumeWeightedMomentumShortStrategy",
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
    if name in {"VolumeWeightedMomentumShortConfig", "VolumeWeightedMomentumShortStrategy"}:
        from nautilus_ext.strategies.volume_weighted_momentum_short import (
            VolumeWeightedMomentumShortConfig,
        )
        from nautilus_ext.strategies.volume_weighted_momentum_short import (
            VolumeWeightedMomentumShortStrategy,
        )

        return {
            "VolumeWeightedMomentumShortConfig": VolumeWeightedMomentumShortConfig,
            "VolumeWeightedMomentumShortStrategy": VolumeWeightedMomentumShortStrategy,
        }[name]

    raise AttributeError(f"module 'nautilus_ext.strategies' has no attribute {name!r}")
