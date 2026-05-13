from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from nautilus_ext.results.result_reporter import NautilusResultReporter
from nautilus_ext.strategies.strategy_spec import NautilusStrategySpec
from nautilus_ext.strategies.strategy_spec import StrategyContext


@dataclass
class BacktestRunResult:
    run_id: str
    strategy_name: str
    engine: object
    bar_type: object
    bars_count: int
    output_dir: str | None = None
    report_dir: str | None = None
    report_files: dict | None = None
    metrics: dict | None = None
    status: str = "completed"
    error: str | None = None


class NautilusBacktestRunner:
    def __init__(
        self,
        data_connector,
        engine_config,
        output_dir: str | None = None,
    ):
        self.data_connector = data_connector
        self.engine_config = engine_config
        self.output_dir = output_dir

    def run_strategy(self, strategy_spec: NautilusStrategySpec):
        if not strategy_spec.enabled:
            raise ValueError(f"Cannot run disabled strategy spec {strategy_spec.name!r}.")

        bars = self.data_connector.prepare_data()
        bar_type = self.data_connector.get_bar_type()
        run_id = self._make_run_id(strategy_spec.name)
        context = StrategyContext(
            bar_type=bar_type,
            instrument=self.data_connector.instrument,
            strategy_name=strategy_spec.name,
            run_id=run_id,
            params=dict(strategy_spec.params or {}),
        )
        strategy = strategy_spec.build_strategy(context)

        from nautilus_ext.runners.engine_runner import NautilusEngineRunner

        engine = NautilusEngineRunner(self.engine_config).run(
            instrument=self.data_connector.instrument,
            data=bars,
            strategy=strategy,
        )

        run_output_dir = None
        report_files = None
        metrics = {"available": False}
        if self.output_dir is not None:
            run_output_dir = str(Path(self.output_dir) / run_id)
            result = BacktestRunResult(
                run_id=run_id,
                strategy_name=strategy_spec.name,
                engine=engine,
                bar_type=bar_type,
                bars_count=len(bars),
                output_dir=self.output_dir,
                report_dir=run_output_dir,
                metrics=metrics,
            )
            report_files = NautilusResultReporter(result).export(run_output_dir)
            result.report_files = report_files
            return result

        return BacktestRunResult(
            run_id=run_id,
            strategy_name=strategy_spec.name,
            engine=engine,
            bar_type=bar_type,
            bars_count=len(bars),
            output_dir=self.output_dir,
            report_dir=run_output_dir,
            report_files=report_files,
            metrics=metrics,
        )

    @staticmethod
    def _make_run_id(strategy_name: str) -> str:
        safe_name = "".join(
            char.lower() if char.isalnum() else "_"
            for char in strategy_name.strip()
        ).strip("_")
        if not safe_name:
            safe_name = "strategy"

        timestamp = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d_%H%M%S")
        return f"{safe_name}_{timestamp}_{uuid4().hex[:8]}"
