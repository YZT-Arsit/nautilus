"""Internal NautilusTrader extension package."""

__all__ = [
    "BacktestRunResult",
    "AutoInstrumentBuilder",
    "AutoInstrumentProfileBuilder",
    "InstrumentProfile",
    "NautilusAutoBarDataConnector",
    "NautilusBacktestRunner",
    "NautilusComparisonReporter",
    "NautilusMultiStrategyRunner",
    "NautilusResultReporter",
    "NautilusStrategyComparisonRunner",
    "NautilusStrategySpec",
    "SUPPORTED_INSTRUMENT_TYPES",
    "StrategyContext",
]


def __getattr__(name: str):
    if name == "NautilusAutoBarDataConnector":
        from nautilus_ext.connectors import NautilusAutoBarDataConnector

        return NautilusAutoBarDataConnector
    if name in {
        "AutoInstrumentBuilder",
        "AutoInstrumentProfileBuilder",
        "InstrumentProfile",
        "SUPPORTED_INSTRUMENT_TYPES",
    }:
        from nautilus_ext.instruments import AutoInstrumentBuilder
        from nautilus_ext.instruments import AutoInstrumentProfileBuilder
        from nautilus_ext.instruments import InstrumentProfile
        from nautilus_ext.instruments import SUPPORTED_INSTRUMENT_TYPES

        return {
            "AutoInstrumentBuilder": AutoInstrumentBuilder,
            "AutoInstrumentProfileBuilder": AutoInstrumentProfileBuilder,
            "InstrumentProfile": InstrumentProfile,
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

    raise AttributeError(f"module 'nautilus_ext' has no attribute {name!r}")
