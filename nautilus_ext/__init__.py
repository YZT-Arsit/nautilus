"""Internal NautilusTrader extension package."""

__all__ = [
    "BacktestRunResult",
    "AutoInstrumentBuilder",
    "AutoInstrumentProfileBuilder",
    "AutoEngineConfigBuilder",
    "EngineConfigProfile",
    "InstrumentProfile",
    "InstrumentRegistry",
    "NautilusAutoBarDataConnector",
    "NautilusBacktestRunner",
    "NautilusComparisonReporter",
    "NautilusMultiStrategyRunner",
    "NautilusResultReporter",
    "NautilusStrategyComparisonRunner",
    "NautilusStrategySpec",
    "SUPPORTED_INSTRUMENT_TYPES",
    "StrategyContext",
    "VolumeWeightedMomentumShortConfig",
    "VolumeWeightedMomentumShortSignalEngine",
    "VolumeWeightedMomentumShortStrategy",
]


def __getattr__(name: str):
    if name == "NautilusAutoBarDataConnector":
        from nautilus_ext.connectors import NautilusAutoBarDataConnector

        return NautilusAutoBarDataConnector
    if name in {"AutoEngineConfigBuilder", "EngineConfigProfile"}:
        from nautilus_ext.config import AutoEngineConfigBuilder
        from nautilus_ext.config import EngineConfigProfile

        return {
            "AutoEngineConfigBuilder": AutoEngineConfigBuilder,
            "EngineConfigProfile": EngineConfigProfile,
        }[name]
    if name in {
        "AutoInstrumentBuilder",
        "AutoInstrumentProfileBuilder",
        "InstrumentProfile",
        "InstrumentRegistry",
        "SUPPORTED_INSTRUMENT_TYPES",
    }:
        from nautilus_ext.instruments import AutoInstrumentBuilder
        from nautilus_ext.instruments import AutoInstrumentProfileBuilder
        from nautilus_ext.instruments import InstrumentProfile
        from nautilus_ext.instruments import InstrumentRegistry
        from nautilus_ext.instruments import SUPPORTED_INSTRUMENT_TYPES

        return {
            "AutoInstrumentBuilder": AutoInstrumentBuilder,
            "AutoInstrumentProfileBuilder": AutoInstrumentProfileBuilder,
            "InstrumentProfile": InstrumentProfile,
            "InstrumentRegistry": InstrumentRegistry,
            "SUPPORTED_INSTRUMENT_TYPES": SUPPORTED_INSTRUMENT_TYPES,
        }[name]
    if name in {
        "BacktestRunResult",
        "NautilusBacktestRunner",
        "NautilusMultiStrategyRunner",
        "NautilusStrategyComparisonRunner",
    }:
        from nautilus_ext.runners import BacktestRunResult
        from nautilus_ext.runners import NautilusBacktestRunner
        from nautilus_ext.runners import NautilusMultiStrategyRunner
        from nautilus_ext.runners import NautilusStrategyComparisonRunner

        return {
            "BacktestRunResult": BacktestRunResult,
            "NautilusBacktestRunner": NautilusBacktestRunner,
            "NautilusMultiStrategyRunner": NautilusMultiStrategyRunner,
            "NautilusStrategyComparisonRunner": NautilusStrategyComparisonRunner,
        }[name]
    if name in {"NautilusComparisonReporter", "NautilusResultReporter"}:
        from nautilus_ext.results import NautilusComparisonReporter
        from nautilus_ext.results import NautilusResultReporter

        return {
            "NautilusComparisonReporter": NautilusComparisonReporter,
            "NautilusResultReporter": NautilusResultReporter,
        }[name]
    if name in {"NautilusStrategySpec", "StrategyContext"}:
        from nautilus_ext.strategies import NautilusStrategySpec
        from nautilus_ext.strategies import StrategyContext

        return {
            "NautilusStrategySpec": NautilusStrategySpec,
            "StrategyContext": StrategyContext,
        }[name]
    if name == "VolumeWeightedMomentumShortSignalEngine":
        from nautilus_ext.strategies import VolumeWeightedMomentumShortSignalEngine

        return VolumeWeightedMomentumShortSignalEngine
    if name in {"VolumeWeightedMomentumShortConfig", "VolumeWeightedMomentumShortStrategy"}:
        from nautilus_ext.strategies import VolumeWeightedMomentumShortConfig
        from nautilus_ext.strategies import VolumeWeightedMomentumShortStrategy

        return {
            "VolumeWeightedMomentumShortConfig": VolumeWeightedMomentumShortConfig,
            "VolumeWeightedMomentumShortStrategy": VolumeWeightedMomentumShortStrategy,
        }[name]

    raise AttributeError(f"module 'nautilus_ext' has no attribute {name!r}")
