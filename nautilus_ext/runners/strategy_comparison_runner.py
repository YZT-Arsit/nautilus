from nautilus_ext.results.comparison_reporter import NautilusComparisonReporter
from nautilus_ext.runners.backtest_runner import BacktestRunResult
from nautilus_ext.runners.backtest_runner import NautilusBacktestRunner
from nautilus_ext.strategies.strategy_spec import NautilusStrategySpec


class NautilusMultiStrategyRunner:
    def __init__(
        self,
        data_connector,
        engine_config,
        strategies: list[NautilusStrategySpec],
        output_dir: str | None = None,
        continue_on_error: bool = False,
    ):
        self.data_connector = data_connector
        self.engine_config = engine_config
        self.strategies = strategies
        self.output_dir = output_dir
        self.continue_on_error = continue_on_error
        self.results = []
        self.comparison_report_files = None
        self._validate_strategies()

    def run_all(self) -> list[BacktestRunResult]:
        enabled_strategies = self._enabled_strategies()
        self.data_connector.prepare_data()

        runner = NautilusBacktestRunner(
            data_connector=self.data_connector,
            engine_config=self.engine_config,
            output_dir=self.output_dir,
        )
        results = []
        for strategy in enabled_strategies:
            try:
                results.append(runner.run_strategy(strategy))
            except Exception as exc:
                if not self.continue_on_error:
                    raise
                results.append(self._failed_result(strategy, exc))

        self.results = results
        if self.output_dir is not None:
            self.comparison_report_files = NautilusComparisonReporter(
                run_results=results,
                output_dir=self.output_dir,
            ).export()

        return results

    def _validate_strategies(self) -> None:
        if not self.strategies:
            raise ValueError("At least one strategy spec is required.")

        enabled_strategies = self._enabled_strategies()
        if not enabled_strategies:
            raise ValueError("At least one enabled strategy spec is required.")

        names = [strategy.name for strategy in enabled_strategies]
        duplicates = sorted({name for name in names if names.count(name) > 1})
        if duplicates:
            raise ValueError(f"Duplicate enabled strategy names are not allowed: {duplicates}")

    def _enabled_strategies(self) -> list[NautilusStrategySpec]:
        return [strategy for strategy in self.strategies if strategy.enabled]

    def _failed_result(self, strategy: NautilusStrategySpec, exc: Exception) -> BacktestRunResult:
        run_id = NautilusBacktestRunner._make_run_id(strategy.name)
        bars = self.data_connector.bars or []
        return BacktestRunResult(
            run_id=run_id,
            strategy_name=strategy.name,
            engine=None,
            bar_type=self.data_connector.bar_type,
            bars_count=len(bars),
            output_dir=self.output_dir,
            report_dir=None,
            report_files=None,
            metrics={"available": False, "error": str(exc)},
            status="failed",
            error=str(exc),
        )


NautilusStrategyComparisonRunner = NautilusMultiStrategyRunner
