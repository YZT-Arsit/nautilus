"""Internal NautilusTrader extension package."""

__all__ = [
    "BacktestRunResult",
    "NautilusAutoBarDataConnector",
    "NautilusBacktestRunner",
    "NautilusComparisonReporter",
    "NautilusMultiStrategyRunner",
    "NautilusResultReporter",
    "NautilusStrategyComparisonRunner",
    "NautilusStrategySpec",
    "StrategyContext",
]


def __getattr__(name: str):
    if name == "NautilusAutoBarDataConnector":
        from nautilus_ext.connectors import NautilusAutoBarDataConnector

        return NautilusAutoBarDataConnector
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
