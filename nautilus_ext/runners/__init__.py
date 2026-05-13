__all__ = [
    "BacktestRunResult",
    "EngineRunConfig",
    "NautilusBacktestRunner",
    "NautilusEngineRunner",
    "NautilusMultiStrategyRunner",
    "NautilusStrategyComparisonRunner",
]


def __getattr__(name: str):
    if name in {"BacktestRunResult", "NautilusBacktestRunner"}:
        from nautilus_ext.runners.backtest_runner import BacktestRunResult
        from nautilus_ext.runners.backtest_runner import NautilusBacktestRunner

        return {
            "BacktestRunResult": BacktestRunResult,
            "NautilusBacktestRunner": NautilusBacktestRunner,
        }[name]
    if name in {"EngineRunConfig", "NautilusEngineRunner"}:
        from nautilus_ext.runners.engine_runner import EngineRunConfig
        from nautilus_ext.runners.engine_runner import NautilusEngineRunner

        return {
            "EngineRunConfig": EngineRunConfig,
            "NautilusEngineRunner": NautilusEngineRunner,
        }[name]
    if name in {"NautilusMultiStrategyRunner", "NautilusStrategyComparisonRunner"}:
        from nautilus_ext.runners.strategy_comparison_runner import NautilusMultiStrategyRunner
        from nautilus_ext.runners.strategy_comparison_runner import NautilusStrategyComparisonRunner

        return {
            "NautilusMultiStrategyRunner": NautilusMultiStrategyRunner,
            "NautilusStrategyComparisonRunner": NautilusStrategyComparisonRunner,
        }[name]

    raise AttributeError(f"module 'nautilus_ext.runners' has no attribute {name!r}")
