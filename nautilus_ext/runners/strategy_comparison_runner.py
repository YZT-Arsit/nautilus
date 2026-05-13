from nautilus_ext.connectors.auto_bar_data_connector import NautilusAutoBarDataConnector
from nautilus_ext.runners.backtest_runner import BacktestRunResult
from nautilus_ext.runners.backtest_runner import NautilusBacktestRunner
from nautilus_ext.runners.engine_runner import EngineRunConfig
from nautilus_ext.strategies.strategy_spec import NautilusStrategySpec


class NautilusStrategyComparisonRunner:
    def __init__(
        self,
        data_connector: NautilusAutoBarDataConnector,
        engine_config: EngineRunConfig,
        strategies: list[NautilusStrategySpec],
        output_dir: str | None = None,
    ):
        self.data_connector = data_connector
        self.engine_config = engine_config
        self.strategies = strategies
        self.output_dir = output_dir
        self._validate_strategy_names()

    def run_all(self) -> list[BacktestRunResult]:
        enabled_strategies = [strategy for strategy in self.strategies if strategy.enabled]
        runner = NautilusBacktestRunner(
            data_connector=self.data_connector,
            engine_config=self.engine_config,
            output_dir=self.output_dir,
        )
        return [runner.run_strategy(strategy) for strategy in enabled_strategies]

    def _validate_strategy_names(self) -> None:
        names = [strategy.name for strategy in self.strategies]
        duplicates = sorted({name for name in names if names.count(name) > 1})
        if duplicates:
            raise ValueError(f"Duplicate strategy names are not allowed: {duplicates}")
